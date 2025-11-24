# Implementation Plan: Complete Vendor → Customer Invoice Flow with 360 Dialog

## Overview

This plan details the implementation of the missing pieces to complete the vendor → customer invoice flow. The guided flow and invoice sending are already implemented. We need to implement:

1. One-line invoice command
2. Payment button click → STK Push trigger
3. Enhanced error handling and edge cases

## Phase 1: One-Line Invoice Command Implementation

### Task 1.1: Implement one-line invoice command handler

**Files:** `src/app/routers/whatsapp.py`

**Location:** Lines 267-273 (currently stubbed)

**Current Code:**
```python
elif command == "invoice":
    # One-line invoice command (will be implemented in Phase 6)
    logger.info(
        "One-line invoice command received",
        extra={"params": params, "sender": sender},
    )
    response_text = "One-line invoice creation will be implemented in Phase 6."
```

**Required Changes:**
```python
elif command == "invoice":
    # One-line invoice command: invoice <phone_or_name> <amount> <desc>
    logger.info(
        "One-line invoice command received",
        extra={"params": params, "sender": sender},
    )

    # Validate that we have the required parameters
    if "phone" in params:
        # Phone-based invoice
        customer_msisdn = params["phone"]
        customer_name = None
    elif "name" in params:
        # Name-based invoice (phone will be looked up or requested)
        customer_name = params["name"]
        # For MVP, we require phone number directly
        response_text = (
            "For quick invoice, please use phone number format:\n"
            "invoice 2547XXXXXXXX <amount> <description>"
        )
        continue  # Skip to send response
    else:
        response_text = (
            "Invalid invoice format. Use:\n"
            "invoice <phone> <amount> <description>\n"
            "Example: invoice 254712345678 1000 Web design services"
        )
        continue  # Skip to send response

    # Validate amount
    amount = params.get("amount")
    if not amount or amount < 1:
        response_text = "Amount must be at least 1 KES"
        continue

    # Validate description
    description = params.get("description")
    if not description or len(description) < 3:
        response_text = "Description must be at least 3 characters"
        continue
    if len(description) > 120:
        response_text = "Description must not exceed 120 characters"
        continue

    # Create invoice
    try:
        from ..models import Invoice
        from ..schemas import InvoiceCreate

        invoice_create = InvoiceCreate(
            msisdn=customer_msisdn,
            customer_name=customer_name,
            merchant_msisdn=sender,
            amount_cents=amount * 100,  # Convert to cents
            description=description,
        )

        # Generate invoice ID
        import random
        import time
        timestamp = int(time.time())
        random_num = random.randint(1000, 9999)
        invoice_id = f"INV-{timestamp}-{random_num}"

        # Calculate VAT (16% of total amount)
        vat_amount = int((invoice_create.amount_cents * 16) / 116)

        # Create invoice record
        invoice = Invoice(
            id=invoice_id,
            customer_name=invoice_create.customer_name,
            msisdn=invoice_create.msisdn,
            merchant_msisdn=invoice_create.merchant_msisdn,
            amount_cents=invoice_create.amount_cents,
            vat_amount=vat_amount,
            currency="KES",
            description=invoice_create.description,
            status="PENDING",
            pay_ref=None,
            pay_link=None,
        )

        db.add(invoice)
        await db.commit()
        await db.refresh(invoice)

        logger.info(
            "Invoice created from one-line command",
            extra={"invoice_id": invoice.id, "merchant_msisdn": sender},
        )

        # Send invoice to customer
        send_success = await whatsapp_service.send_invoice_to_customer(
            invoice_id=invoice.id,
            customer_msisdn=invoice.msisdn,
            customer_name=invoice.customer_name,
            amount_cents=invoice.amount_cents,
            description=invoice.description,
            db_session=db,
        )

        # Update invoice status
        if send_success:
            invoice.status = "SENT"
            await db.commit()
            await db.refresh(invoice)

            # Send merchant confirmation
            await whatsapp_service.send_merchant_confirmation(
                merchant_msisdn=sender,
                invoice_id=invoice.id,
                customer_msisdn=invoice.msisdn,
                amount_cents=invoice.amount_cents,
                status=invoice.status,
            )

            # Override response text (confirmation already sent)
            response_text = None
        else:
            logger.warning(
                "Invoice created but failed to send (one-line command)",
                extra={"invoice_id": invoice.id},
            )
            response_text = (
                f"Invoice {invoice.id} created but failed to send to customer. "
                f"Status: PENDING. You can try again later."
            )

    except ValueError as ve:
        # Validation error
        logger.warning(
            "Validation error in one-line invoice",
            extra={"error": str(ve), "sender": sender},
        )
        response_text = f"Invalid invoice data: {str(ve)}"

    except Exception as e:
        logger.error(
            "Failed to create invoice from one-line command",
            extra={"error": str(e), "merchant_msisdn": sender},
            exc_info=True,
        )
        await db.rollback()
        response_text = (
            "Failed to create invoice. Please try again or use the guided flow "
            "(send 'invoice' without parameters)."
        )
```

**Dependencies:** None (all required code already exists)

**Testing:**
- Send message: `invoice 254712345678 1000 Web design services`
- Verify invoice created in database with status PENDING
- Verify customer receives WhatsApp message with payment button
- Verify invoice status updated to SENT
- Verify merchant receives confirmation message
- Test error cases: invalid phone, invalid amount, missing description

### Task 1.2: Update one-line invoice parser to handle edge cases

**Files:** `src/app/services/whatsapp.py`

**Location:** Lines 352-381 (parse_command function)

**Current Code:** (Already handles phone/name, amount, description)

**Required Changes:** Add validation for edge cases:
```python
# One-line invoice command: invoice <phone_or_name> <amount> <desc...>
invoice_pattern = r"^invoice\s+(\S+(?:\s+\S+)*?)\s+(\d+)\s+(.{3,})$"
match = re.match(invoice_pattern, message_text.strip(), re.IGNORECASE)
if match:
    phone_or_name = match.group(1).strip()
    amount_str = match.group(2).strip()
    description = match.group(3).strip()

    # Validate amount is numeric and positive
    try:
        amount = int(amount_str)
        if amount < 1:
            return {
                "command": "invoice",
                "params": {"error": "Amount must be at least 1 KES"},
            }
    except ValueError:
        return {
            "command": "invoice",
            "params": {"error": "Amount must be a number"},
        }

    # Validate description length
    if len(description) < 3:
        return {
            "command": "invoice",
            "params": {"error": "Description must be at least 3 characters"},
        }
    if len(description) > 120:
        return {
            "command": "invoice",
            "params": {"error": "Description must not exceed 120 characters"},
        }

    # Check if it's a phone number (starts with 254 and is numeric)
    if re.match(r"^2547\d{8}$", phone_or_name):
        # It's a phone number
        return {
            "command": "invoice",
            "params": {
                "phone": phone_or_name,
                "amount": amount,
                "description": description,
            },
        }
    else:
        # It's a name - return error for MVP (name lookup not implemented)
        return {
            "command": "invoice",
            "params": {
                "error": "For quick invoice, please use phone number format: invoice 2547XXXXXXXX <amount> <description>",
            },
        }
```

**Dependencies:** Task 1.1

**Testing:**
- Test with valid phone: `invoice 254712345678 1000 Services`
- Test with name: `invoice JohnDoe 1000 Services` (should show error)
- Test with invalid amount: `invoice 254712345678 0 Services` (should show error)
- Test with short description: `invoice 254712345678 1000 ab` (should show error)
- Test with long description: `invoice 254712345678 1000 <121 characters>` (should show error)

### Task 1.3: Add error handling for one-line command in webhook handler

**Files:** `src/app/routers/whatsapp.py`

**Location:** After line 273 (in the one-line command handler)

**Required Changes:** Add error parameter handling from parser:
```python
elif command == "invoice":
    # Check if parser returned an error
    if "error" in params:
        response_text = params["error"]
        logger.warning(
            "One-line invoice validation error",
            extra={"error": params["error"], "sender": sender},
        )
    else:
        # Continue with invoice creation (Task 1.1 implementation)
        ...
```

**Dependencies:** Task 1.2

**Testing:**
- Send invalid commands and verify error messages are user-friendly
- Verify errors are logged correctly
- Verify no database changes for invalid commands

---

## Phase 2: Payment Button Click → STK Push Integration

### Task 2.1: Implement STK Push trigger on button click

**Files:** `src/app/routers/whatsapp.py`

**Location:** Lines 188-235 (currently acknowledged but not implemented)

**Current Code:**
```python
if message_type == "interactive":
    # Check if it's a payment button click
    if message_text.startswith("pay_"):
        invoice_id = message_text[4:]  # Remove "pay_" prefix
        logger.info(
            "Payment button clicked",
            extra={"sender": sender, "invoice_id": invoice_id},
        )

        # For now, just acknowledge - Phase 7 will implement STK Push
        response_text = (
            f"Payment request received for invoice {invoice_id}. "
            f"STK Push will be implemented in Phase 7."
        )
```

**Required Changes:**
```python
if message_type == "interactive":
    # Check if it's a payment button click
    if message_text.startswith("pay_"):
        invoice_id = message_text[4:]  # Remove "pay_" prefix
        logger.info(
            "Payment button clicked",
            extra={"sender": sender, "invoice_id": invoice_id},
        )

        # Lookup invoice in database
        from ..models import Invoice, Payment
        from sqlalchemy import select

        invoice_stmt = select(Invoice).where(Invoice.id == invoice_id)
        invoice_result = await db.execute(invoice_stmt)
        invoice = invoice_result.scalar_one_or_none()

        if not invoice:
            logger.warning(
                "Invoice not found for payment button",
                extra={"invoice_id": invoice_id, "sender": sender},
            )
            response_text = (
                f"Invoice {invoice_id} not found. Please contact the merchant."
            )
        elif invoice.status == "PAID":
            logger.info(
                "Invoice already paid",
                extra={"invoice_id": invoice_id, "sender": sender},
            )
            response_text = (
                f"Invoice {invoice_id} has already been paid. "
                f"Receipt: {invoice.pay_ref or 'N/A'}"
            )
        elif invoice.status not in ["SENT", "PENDING"]:
            logger.warning(
                "Invalid invoice status for payment",
                extra={"invoice_id": invoice_id, "status": invoice.status},
            )
            response_text = (
                f"Invoice {invoice_id} cannot be paid (status: {invoice.status}). "
                f"Please contact the merchant."
            )
        else:
            # Invoice is valid for payment - initiate STK Push
            try:
                from ..services.mpesa import MPesaService
                from ..config import settings
                from uuid import uuid4

                mpesa_service = MPesaService(environment=settings.mpesa_environment)

                # Generate idempotency key
                idempotency_key = f"{invoice_id}-button-{int(time.time())}"

                # Check if payment already exists for this invoice
                existing_payment_stmt = select(Payment).where(
                    Payment.invoice_id == invoice_id,
                    Payment.status == "INITIATED"
                )
                existing_payment_result = await db.execute(existing_payment_stmt)
                existing_payment = existing_payment_result.scalar_one_or_none()

                if existing_payment:
                    logger.info(
                        "Payment already initiated for this invoice",
                        extra={"invoice_id": invoice_id, "payment_id": existing_payment.id},
                    )
                    response_text = (
                        "Payment request already sent! Check your phone for the M-PESA prompt. "
                        "If you didn't receive it, please try again in a moment."
                    )
                else:
                    # Convert amount from cents to whole KES
                    amount_kes = round(invoice.amount_cents / 100)

                    # Create Payment record with status INITIATED
                    payment = Payment(
                        id=str(uuid4()),
                        invoice_id=invoice.id,
                        method="MPESA_STK",
                        status="INITIATED",
                        amount_cents=invoice.amount_cents,
                        idempotency_key=idempotency_key,
                        raw_request={},
                        raw_callback=None,
                        mpesa_receipt=None,
                    )

                    db.add(payment)
                    await db.commit()
                    await db.refresh(payment)

                    # Prepare STK Push request
                    account_reference = invoice.id[:20]  # Max 20 characters
                    transaction_desc = invoice.description[:20]  # Max 20 characters

                    # Initiate STK Push
                    stk_response = await mpesa_service.initiate_stk_push(
                        phone_number=sender,  # Customer's phone (sender of button click)
                        amount=amount_kes,
                        account_reference=account_reference,
                        transaction_desc=transaction_desc,
                    )

                    # Update payment with raw request and response
                    payment.raw_request = {
                        "phone_number": sender,
                        "amount": amount_kes,
                        "account_reference": account_reference,
                        "transaction_desc": transaction_desc,
                        "stk_response": stk_response,
                    }

                    # Store CheckoutRequestID and MerchantRequestID for callback matching
                    payment.checkout_request_id = stk_response.get("CheckoutRequestID")
                    payment.merchant_request_id = stk_response.get("MerchantRequestID")

                    await db.commit()
                    await db.refresh(payment)

                    logger.info(
                        "STK Push initiated from button click",
                        extra={
                            "payment_id": payment.id,
                            "invoice_id": invoice.id,
                            "customer_msisdn": sender,
                        },
                    )

                    response_text = (
                        f"Check your phone for the M-PESA payment prompt!\n"
                        f"Amount: KES {amount_kes}\n"
                        f"You'll receive a receipt once payment is complete."
                    )

                    # Update invoice status to PAYMENT_INITIATED (optional)
                    # invoice.status = "PAYMENT_INITIATED"
                    # await db.commit()

            except Exception as stk_error:
                logger.error(
                    "Failed to initiate STK Push from button click",
                    extra={
                        "error": str(stk_error),
                        "invoice_id": invoice_id,
                        "sender": sender,
                    },
                    exc_info=True,
                )
                # Update payment status to FAILED if it was created
                if 'payment' in locals():
                    payment.status = "FAILED"
                    payment.raw_request = {
                        "error": str(stk_error),
                        "phone_number": sender,
                        "amount": amount_kes,
                    }
                    await db.commit()

                response_text = (
                    "Failed to initiate payment. Please try again or contact support. "
                    f"Error: {str(stk_error)}"
                )
```

**Dependencies:** None (M-PESA integration already exists)

**Testing:**
- Send invoice to customer
- Customer clicks "Pay with M-PESA" button
- Verify Payment record created with status INITIATED
- Verify STK Push sent to customer's phone
- Verify customer receives confirmation message
- Test error cases: invalid invoice, already paid, M-PESA API failure
- Test duplicate button clicks (should not create multiple payments)

### Task 2.2: Add message logging for payment button clicks

**Files:** `src/app/routers/whatsapp.py`

**Location:** Lines 205-228 (already exists but needs enhancement)

**Current Code:** (Already logs button clicks)

**Required Changes:** Update log entry to include payment initiation status:
```python
# Create MessageLog entry for button click
try:
    button_click_log = MessageLog(
        invoice_id=invoice_id,
        channel="WHATSAPP",
        direction="IN",
        event="payment_button_clicked",
        payload={
            "button_id": message_text,
            "invoice_id": invoice_id,
            "payment_initiated": 'payment' in locals() and payment.status == "INITIATED",
            "payment_id": payment.id if 'payment' in locals() else None,
            "stk_request_sent": 'stk_response' in locals(),
            "timestamp": datetime.utcnow().isoformat(),
        },
    )
    db.add(button_click_log)
    await db.commit()
except Exception as log_error:
    logger.error(
        "Failed to log button click",
        extra={"error": str(log_error), "invoice_id": invoice_id},
    )
```

**Dependencies:** Task 2.1

**Testing:**
- Click payment button
- Verify message_log entry created with correct metadata
- Verify no PII stored in log
- Check logs include payment_id and STK status

---

## Phase 3: Invoice Link Format and Public Access

### Task 3.1: Verify invoice viewing route URL format

**Files:** `src/app/routers/invoices.py` (check if view route exists)

**Location:** Check for GET /{invoice_id} route

**Current Code:** (Based on git status, this route exists but we need to verify)

**Required Changes:** Ensure route exists and returns invoice details:
```python
@router.get("/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(
    invoice_id: str,
    db: AsyncSession = Depends(get_db),
) -> Invoice:
    """
    Get invoice details by ID.

    This endpoint is public (no authentication required) to allow customers
    to view invoices they received via WhatsApp link.

    Args:
        invoice_id: Invoice ID
        db: Database session

    Returns:
        Invoice details

    Raises:
        HTTPException: 404 if invoice not found
    """
    from sqlalchemy import select

    invoice_stmt = select(Invoice).where(Invoice.id == invoice_id)
    invoice_result = await db.execute(invoice_stmt)
    invoice = invoice_result.scalar_one_or_none()

    if not invoice:
        logger.warning(
            "Invoice not found for viewing",
            extra={"invoice_id": invoice_id},
        )
        raise HTTPException(
            status_code=404,
            detail="Invoice not found"
        )

    logger.info(
        "Invoice viewed",
        extra={"invoice_id": invoice_id, "status": invoice.status},
    )

    return invoice
```

**Dependencies:** None

**Testing:**
- Access invoice via browser: `https://invoiceiq-new.fly.dev/{invoice_id}`
- Verify invoice details displayed
- Verify payment button visible for SENT/PENDING invoices
- Test with invalid invoice_id (should return 404)

### Task 3.2: Update invoice message format to include correct link

**Files:** `src/app/services/whatsapp.py`

**Location:** Lines 653-654 (message_text formatting)

**Current Code:**
```python
# Format invoice message (keep ≤ 2 lines as per CLAUDE.md)
message_text = f"Invoice {invoice_id}\nAmount: KES {amount_kes:.2f} | {description}"
```

**Required Changes:** Add invoice viewing link:
```python
# Format invoice message (keep ≤ 2 lines as per CLAUDE.md)
# Include link to view invoice details
invoice_link = f"https://invoiceiq-new.fly.dev/{invoice_id}"
message_text = (
    f"Invoice {invoice_id}\n"
    f"Amount: KES {amount_kes:.2f} | {description}\n"
    f"View: {invoice_link}"
)
```

**Dependencies:** Task 3.1

**Testing:**
- Send invoice to customer
- Verify WhatsApp message includes clickable link
- Click link and verify invoice details page loads
- Verify payment button works from web view

### Task 3.3: Add environment variable for base URL

**Files:** `src/app/config.py`, `.env.example`

**Location:** Add new config field

**Current Code:** N/A

**Required Changes:**

In `src/app/config.py`:
```python
class Settings(BaseSettings):
    # ... existing fields ...

    # Application URLs
    base_url: str = "https://invoiceiq-new.fly.dev"  # Default for production

    # ... rest of config ...
```

In `.env.example`:
```bash
# Application Configuration
BASE_URL=https://invoiceiq-new.fly.dev
```

Update invoice message in `whatsapp.py`:
```python
from ..config import settings

invoice_link = f"{settings.base_url}/{invoice_id}"
```

**Dependencies:** Task 3.2

**Testing:**
- Set BASE_URL in .env to localhost for testing
- Verify links use correct base URL
- Test in both local and production environments

---

## Phase 4: Error Handling and Edge Cases

### Task 4.1: Handle customer phone mismatch in button click

**Files:** `src/app/routers/whatsapp.py`

**Location:** In Task 2.1 implementation (payment button handler)

**Current Code:** Uses `sender` (button clicker) as phone number

**Required Changes:** Verify button clicker matches invoice customer:
```python
# Verify button clicker is the invoice customer
if sender != invoice.msisdn:
    logger.warning(
        "Payment button clicked by different phone number",
        extra={
            "invoice_id": invoice_id,
            "invoice_customer": invoice.msisdn,
            "button_clicker": sender,
        },
    )
    response_text = (
        f"This invoice is for {invoice.msisdn}. "
        f"If you are the customer, please use the phone number that received the invoice."
    )
else:
    # Continue with STK Push (existing code from Task 2.1)
    ...
```

**Dependencies:** Task 2.1

**Testing:**
- Send invoice to 254712345678
- Forward message to 254798765432
- Have second number click payment button
- Verify error message displayed
- Verify no STK Push sent
- Test with correct customer phone (should work)

### Task 4.2: Add timeout handling for STK Push

**Files:** `src/app/services/mpesa.py`

**Location:** Check existing timeout configuration in initiate_stk_push

**Current Code:** (Likely already has timeout in httpx client)

**Required Changes:** Ensure timeout is set and handle timeout errors:
```python
async def initiate_stk_push(
    self,
    phone_number: str,
    amount: int,
    account_reference: str,
    transaction_desc: str,
) -> dict:
    """
    Initiate M-PESA STK Push request with timeout handling.
    """
    # ... existing token and request preparation code ...

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.post(
                url,
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            return response.json()

    except httpx.TimeoutException as e:
        logger.error(
            "STK Push request timed out",
            extra={
                "phone_number": phone_number,
                "amount": amount,
                "timeout": 30.0,
            },
            exc_info=True,
        )
        raise Exception("Payment service timed out. Please try again.")

    except httpx.HTTPStatusError as e:
        logger.error(
            "M-PESA API returned error",
            extra={
                "status_code": e.response.status_code,
                "response": e.response.text,
            },
            exc_info=True,
        )
        raise Exception(f"Payment service error: {e.response.status_code}")

    except Exception as e:
        logger.error(
            "Unexpected error in STK Push",
            extra={"error": str(e)},
            exc_info=True,
        )
        raise
```

**Dependencies:** None

**Testing:**
- Mock M-PESA API timeout (use pytest-timeout or similar)
- Verify error logged
- Verify user-friendly error message returned
- Verify payment record marked as FAILED

### Task 4.3: Add duplicate STK Push prevention

**Files:** `src/app/routers/whatsapp.py`

**Location:** In Task 2.1 implementation

**Current Code:** (Already checks for existing INITIATED payment)

**Required Changes:** Enhance duplicate check:
```python
# Check if payment already exists for this invoice (any status)
existing_payment_stmt = select(Payment).where(
    Payment.invoice_id == invoice_id
).order_by(Payment.created_at.desc())
existing_payment_result = await db.execute(existing_payment_stmt)
existing_payment = existing_payment_result.scalar_one_or_none()

if existing_payment:
    if existing_payment.status == "INITIATED":
        # Payment in progress
        logger.info(
            "Payment already initiated",
            extra={"invoice_id": invoice_id, "payment_id": existing_payment.id},
        )
        response_text = (
            "Payment request already sent! Check your phone for the M-PESA prompt. "
            "If you didn't receive it, please wait 2 minutes and try again."
        )
    elif existing_payment.status == "SUCCESS":
        # Already paid
        response_text = (
            f"This invoice has already been paid. Receipt: {existing_payment.mpesa_receipt or 'N/A'}"
        )
    elif existing_payment.status == "FAILED":
        # Previous attempt failed, allow retry
        time_since_failure = (datetime.utcnow() - existing_payment.updated_at).total_seconds()
        if time_since_failure < 120:  # 2 minutes cooldown
            response_text = (
                f"Previous payment failed. Please wait {int(120 - time_since_failure)} seconds before retrying."
            )
        else:
            # Allow retry (continue with STK Push)
            logger.info(
                "Retrying payment after previous failure",
                extra={"invoice_id": invoice_id, "previous_payment_id": existing_payment.id},
            )
            # Continue with STK Push creation
    else:
        # Unknown status
        logger.warning(
            "Payment exists with unknown status",
            extra={"invoice_id": invoice_id, "status": existing_payment.status},
        )
        response_text = (
            f"Payment status unclear ({existing_payment.status}). Please contact the merchant."
        )
else:
    # No existing payment, create new one (existing Task 2.1 code)
    ...
```

**Dependencies:** Task 2.1

**Testing:**
- Click payment button twice quickly
- Verify second click shows "already sent" message
- Verify only one Payment record created
- Test retry after FAILED status (wait 2 minutes)
- Verify cooldown period prevents immediate retry

### Task 4.4: Add merchant notification for payment failures

**Files:** `src/app/routers/payments.py` (STK callback handler)

**Location:** Lines 528-544 (payment failed section)

**Current Code:**
```python
else:
    # Payment failed
    payment.status = "FAILED"
    invoice.status = "FAILED"

    logger.info(
        "Payment failed",
        extra={
            "payment_id": payment.id,
            "invoice_id": invoice.id,
            "result_code": result_code,
            "result_desc": parsed.get("result_desc"),
        },
    )

    await db.commit()
```

**Required Changes:** Add merchant notification for failed payments:
```python
else:
    # Payment failed
    payment.status = "FAILED"
    invoice.status = "FAILED"

    logger.info(
        "Payment failed",
        extra={
            "payment_id": payment.id,
            "invoice_id": invoice.id,
            "result_code": result_code,
            "result_desc": parsed.get("result_desc"),
        },
    )

    await db.commit()

    # Notify merchant of payment failure
    try:
        whatsapp_service = WhatsAppService()

        # Get readable failure reason
        failure_reasons = {
            1: "Insufficient balance",
            1032: "Cancelled by user",
            1037: "Timeout - user did not respond",
            2001: "Invalid phone number",
        }
        failure_reason = failure_reasons.get(
            result_code,
            f"Payment failed (code {result_code})"
        )

        # Notify merchant
        merchant_message = (
            f"Payment failed for invoice {invoice.id}\n"
            f"Customer: {invoice.msisdn}\n"
            f"Reason: {failure_reason}"
        )
        await whatsapp_service.send_message(
            invoice.merchant_msisdn,
            merchant_message
        )

        # Notify customer
        customer_message = (
            f"Payment for invoice {invoice.id} was not completed.\n"
            f"Reason: {failure_reason}\n"
            f"You can try again by clicking the Pay button in the invoice message."
        )
        await whatsapp_service.send_message(
            invoice.msisdn,
            customer_message
        )

        logger.info(
            "Payment failure notifications sent",
            extra={"invoice_id": invoice.id},
        )

    except Exception as notify_error:
        logger.error(
            "Failed to send payment failure notifications",
            extra={
                "error": str(notify_error),
                "invoice_id": invoice.id,
            },
            exc_info=True,
        )
```

**Dependencies:** None

**Testing:**
- Simulate STK Push failure (cancel on phone)
- Verify merchant receives failure notification
- Verify customer receives failure notification with reason
- Verify messages are user-friendly
- Test various failure result codes

---

## Phase 5: Testing and Validation

### Task 5.1: Write integration tests for one-line invoice flow

**Files:** `tests/integration/test_invoice_creation.py`

**Location:** Add new test cases

**Current Code:** (Existing tests may cover guided flow)

**Required Changes:** Add test cases:
```python
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from src.app.models import Invoice, Payment, MessageLog
from src.app.main import app


@pytest.mark.asyncio
async def test_one_line_invoice_creation_success(async_client, db_session, mock_whatsapp_api, mock_mpesa_api):
    """Test successful one-line invoice creation and sending."""
    # Simulate WhatsApp webhook with one-line invoice command
    webhook_payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "254712345678",
                        "type": "text",
                        "text": {
                            "body": "invoice 254798765432 1000 Web design services"
                        }
                    }]
                }
            }]
        }]
    }

    response = await async_client.post("/whatsapp/webhook", json=webhook_payload)
    assert response.status_code == 200

    # Verify invoice created
    invoice_stmt = select(Invoice).where(Invoice.merchant_msisdn == "254712345678")
    result = await db_session.execute(invoice_stmt)
    invoice = result.scalar_one_or_none()

    assert invoice is not None
    assert invoice.msisdn == "254798765432"
    assert invoice.amount_cents == 100000  # 1000 KES in cents
    assert invoice.description == "Web design services"
    assert invoice.status == "SENT"

    # Verify WhatsApp message sent to customer
    assert mock_whatsapp_api.send_message_called
    assert "254798765432" in mock_whatsapp_api.last_recipient

    # Verify merchant confirmation sent
    assert "254712345678" in mock_whatsapp_api.recipients


@pytest.mark.asyncio
async def test_one_line_invoice_invalid_phone(async_client, db_session):
    """Test one-line invoice with invalid phone number."""
    webhook_payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "254712345678",
                        "type": "text",
                        "text": {
                            "body": "invoice 123456 1000 Services"
                        }
                    }]
                }
            }]
        }]
    }

    response = await async_client.post("/whatsapp/webhook", json=webhook_payload)
    assert response.status_code == 200

    # Verify no invoice created
    invoice_stmt = select(Invoice).where(Invoice.merchant_msisdn == "254712345678")
    result = await db_session.execute(invoice_stmt)
    invoice = result.scalar_one_or_none()
    assert invoice is None


@pytest.mark.asyncio
async def test_one_line_invoice_invalid_amount(async_client, db_session):
    """Test one-line invoice with invalid amount."""
    webhook_payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "254712345678",
                        "type": "text",
                        "text": {
                            "body": "invoice 254798765432 0 Services"
                        }
                    }]
                }
            }]
        }]
    }

    response = await async_client.post("/whatsapp/webhook", json=webhook_payload)
    assert response.status_code == 200

    # Verify no invoice created
    invoice_stmt = select(Invoice).where(Invoice.merchant_msisdn == "254712345678")
    result = await db_session.execute(invoice_stmt)
    invoice = result.scalar_one_or_none()
    assert invoice is None
```

**Dependencies:** Tasks 1.1-1.3

**Testing:**
- Run pytest with coverage: `pytest tests/integration/test_invoice_creation.py --cov`
- Verify all test cases pass
- Verify coverage > 80% for one-line invoice code paths

### Task 5.2: Write integration tests for payment button flow

**Files:** `tests/integration/test_payment_flow.py`

**Location:** Add new test cases

**Required Changes:** Add test cases:
```python
@pytest.mark.asyncio
async def test_payment_button_click_success(async_client, db_session, mock_whatsapp_api, mock_mpesa_api):
    """Test successful payment button click and STK Push."""
    # Create invoice first
    invoice = Invoice(
        id="INV-TEST-001",
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=100000,
        vat_amount=13793,
        currency="KES",
        description="Test invoice",
        status="SENT"
    )
    db_session.add(invoice)
    await db_session.commit()

    # Simulate button click webhook
    webhook_payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "254712345678",
                        "type": "interactive",
                        "interactive": {
                            "type": "button_reply",
                            "button_reply": {
                                "id": "pay_INV-TEST-001",
                                "title": "Pay with M-PESA"
                            }
                        }
                    }]
                }
            }]
        }]
    }

    response = await async_client.post("/whatsapp/webhook", json=webhook_payload)
    assert response.status_code == 200

    # Verify Payment record created
    payment_stmt = select(Payment).where(Payment.invoice_id == "INV-TEST-001")
    result = await db_session.execute(payment_stmt)
    payment = result.scalar_one_or_none()

    assert payment is not None
    assert payment.status == "INITIATED"
    assert payment.method == "MPESA_STK"

    # Verify STK Push called
    assert mock_mpesa_api.stk_push_called
    assert mock_mpesa_api.last_phone == "254712345678"
    assert mock_mpesa_api.last_amount == 1000  # 100000 cents = 1000 KES

    # Verify confirmation message sent
    assert mock_whatsapp_api.send_message_called


@pytest.mark.asyncio
async def test_payment_button_wrong_customer(async_client, db_session):
    """Test payment button clicked by wrong customer."""
    # Create invoice for customer A
    invoice = Invoice(
        id="INV-TEST-002",
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=100000,
        vat_amount=13793,
        currency="KES",
        description="Test invoice",
        status="SENT"
    )
    db_session.add(invoice)
    await db_session.commit()

    # Customer B tries to pay (different phone)
    webhook_payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "254799999999",  # Wrong customer
                        "type": "interactive",
                        "interactive": {
                            "type": "button_reply",
                            "button_reply": {
                                "id": "pay_INV-TEST-002",
                                "title": "Pay with M-PESA"
                            }
                        }
                    }]
                }
            }]
        }]
    }

    response = await async_client.post("/whatsapp/webhook", json=webhook_payload)
    assert response.status_code == 200

    # Verify no Payment record created
    payment_stmt = select(Payment).where(Payment.invoice_id == "INV-TEST-002")
    result = await db_session.execute(payment_stmt)
    payment = result.scalar_one_or_none()
    assert payment is None


@pytest.mark.asyncio
async def test_payment_button_duplicate_click(async_client, db_session, mock_mpesa_api):
    """Test duplicate payment button clicks."""
    # Create invoice and payment
    invoice = Invoice(
        id="INV-TEST-003",
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=100000,
        vat_amount=13793,
        currency="KES",
        description="Test invoice",
        status="SENT"
    )
    db_session.add(invoice)
    await db_session.commit()

    # First button click
    webhook_payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "254712345678",
                        "type": "interactive",
                        "interactive": {
                            "type": "button_reply",
                            "button_reply": {
                                "id": "pay_INV-TEST-003",
                                "title": "Pay with M-PESA"
                            }
                        }
                    }]
                }
            }]
        }]
    }

    response1 = await async_client.post("/whatsapp/webhook", json=webhook_payload)
    assert response1.status_code == 200

    # Reset mock
    mock_mpesa_api.reset()

    # Second button click (duplicate)
    response2 = await async_client.post("/whatsapp/webhook", json=webhook_payload)
    assert response2.status_code == 200

    # Verify only one Payment record
    payment_stmt = select(Payment).where(Payment.invoice_id == "INV-TEST-003")
    result = await db_session.execute(payment_stmt)
    payments = result.scalars().all()
    assert len(payments) == 1

    # Verify STK Push only called once
    assert not mock_mpesa_api.stk_push_called  # Second call should not trigger STK
```

**Dependencies:** Tasks 2.1-2.2, 4.1, 4.3

**Testing:**
- Run pytest: `pytest tests/integration/test_payment_flow.py`
- Verify all edge cases covered
- Verify mocks work correctly

### Task 5.3: End-to-end manual testing checklist

**Files:** Create `docs/TESTING_CHECKLIST.md`

**Required Changes:** Document manual testing steps:
```markdown
# Manual Testing Checklist

## One-Line Invoice Flow

### Happy Path
- [ ] Send one-line invoice: `invoice 254712345678 1000 Web design services`
- [ ] Verify invoice created in database with status PENDING
- [ ] Verify customer 254712345678 receives WhatsApp message
- [ ] Verify message includes invoice ID, amount, description, payment button
- [ ] Verify merchant receives confirmation message
- [ ] Verify invoice status updated to SENT

### Error Cases
- [ ] Invalid phone: `invoice 123456 1000 Services` → Error message
- [ ] Zero amount: `invoice 254712345678 0 Services` → Error message
- [ ] Missing description: `invoice 254712345678 1000` → Error message
- [ ] Short description: `invoice 254712345678 1000 ab` → Error message
- [ ] Long description: `invoice 254712345678 1000 <121+ chars>` → Error message

## Payment Button Flow

### Happy Path
- [ ] Customer clicks "Pay with M-PESA" button
- [ ] Verify Payment record created with status INITIATED
- [ ] Verify STK Push prompt appears on customer's phone
- [ ] Customer enters PIN and completes payment
- [ ] Verify callback received and processed
- [ ] Verify Payment status updated to SUCCESS
- [ ] Verify Invoice status updated to PAID
- [ ] Verify customer receives receipt message
- [ ] Verify merchant receives receipt message

### Error Cases
- [ ] Wrong customer clicks button → Error message (not your invoice)
- [ ] Duplicate button click → Message: already sent
- [ ] Customer cancels STK → Payment status FAILED, notifications sent
- [ ] M-PESA API timeout → Payment status FAILED, error message
- [ ] Already paid invoice → Message: already paid with receipt

## Edge Cases
- [ ] Multiple merchants creating invoices simultaneously
- [ ] Customer receives multiple invoices from same merchant
- [ ] Invoice forwarded to another person (who clicks pay button)
- [ ] Network interruption during STK Push
- [ ] Callback received before payment record created (race condition)
- [ ] Very long descriptions (exactly 120 characters)
- [ ] Special characters in description
- [ ] Phone numbers from different countries (if supported)

## Performance
- [ ] Create 10 invoices rapidly (within 1 minute)
- [ ] 5 customers click payment buttons within 10 seconds
- [ ] Verify all processed correctly
- [ ] Check database for consistency
- [ ] Review logs for errors

## Regression
- [ ] Guided flow still works (send "invoice" without parameters)
- [ ] Help command still works
- [ ] Health checks still work (/healthz, /readyz)
- [ ] Webhook verification still works (GET /whatsapp/webhook)
```

**Dependencies:** All previous tasks

**Testing:**
- Execute checklist in staging environment
- Document any failures
- Create bug fix tasks for issues found

---

## Phase 6: Documentation and Deployment

### Task 6.1: Update API documentation

**Files:** Update inline docstrings for modified functions

**Location:** Various files modified in previous tasks

**Required Changes:** Ensure all new/modified functions have complete docstrings with examples

**Dependencies:** All implementation tasks complete

**Testing:**
- Access FastAPI docs: `http://localhost:8000/docs`
- Verify all endpoints documented
- Test API calls via Swagger UI

### Task 6.2: Update PLAN.md progress

**Files:** `PLAN.md`

**Location:** Phase 6 section

**Required Changes:** Mark tasks complete:
```markdown
## Phase 6: Invoice Creation & Delivery (Day 3) ✅

- [x] Create src/app/routers/invoices.py with APIRouter setup
- [x] Implement POST /invoices endpoint (accepts InvoiceCreate schema, creates database record)
- [x] Add invoice ID generation logic (UUID or custom format like INV-{timestamp}-{random})
- [x] Implement send_invoice_to_customer function in whatsapp.py (formats message, sends via WhatsApp API with interactive buttons)
- [x] Add WhatsApp interactive button support for "Pay with M-PESA" action
- [x] Update invoice status from PENDING to SENT after successful delivery
- [x] Add error handling for WhatsApp API failures (log error, keep status PENDING)
- [x] Create message_log entry for each outbound message attempt
- [x] Update POST /whatsapp/webhook to handle button click responses (interactive message replies)
- [x] Implement merchant confirmation message after invoice sent (includes invoice ID and available commands)
- [x] Wire up guided flow completion to call POST /invoices internally
- [x] **COMPLETE:** Implement one-line invoice command (invoice <phone> <amount> <desc>)
- [x] **COMPLETE:** Connect payment button clicks to STK Push initiation
- [x] **COMPLETE:** Add customer phone verification for payment button clicks
- [x] **COMPLETE:** Add duplicate payment prevention
- [x] **COMPLETE:** Add payment failure notifications
- [x] Write integration test for full invoice creation flow in tests/integration/test_invoice_creation.py
- [x] Test with mock WhatsApp API to verify message format and button structure
```

**Dependencies:** All tasks complete

**Testing:** Review PLAN.md for accuracy

### Task 6.3: Update deployment documentation

**Files:** `docs/RUNBOOK.md` or `README.md`

**Location:** Add section on testing the complete flow

**Required Changes:**
```markdown
## Testing the Complete Invoice Flow

### Prerequisites
- Fly.io app deployed with HTTPS
- WhatsApp Business API configured (360 Dialog)
- M-PESA sandbox or production credentials
- Test phone numbers

### One-Line Invoice Test
1. Send WhatsApp message to your business number:
   ```
   invoice 254712345678 1000 Web development services
   ```
2. Verify customer receives invoice message
3. Verify merchant receives confirmation

### Payment Flow Test
1. Customer clicks "Pay with M-PESA" button
2. Customer enters M-PESA PIN on phone
3. Verify both parties receive receipt messages

### Troubleshooting
- **No message received:** Check message_log table for delivery status
- **STK not received:** Verify M-PESA callback URL registered
- **Payment stuck:** Check Payment.status in database
- **Errors:** Check application logs: `fly logs`
```

**Dependencies:** All tasks complete

**Testing:** Follow documented steps to verify

---

## Implementation Summary

### Total Estimated Time: 16-24 hours

**Phase 1: One-Line Invoice (6-8 hours)**
- Task 1.1: 3-4 hours (core implementation)
- Task 1.2: 1-2 hours (parser enhancements)
- Task 1.3: 1 hour (error handling)
- Testing: 1-2 hours

**Phase 2: Payment Button → STK (4-6 hours)**
- Task 2.1: 3-4 hours (STK integration)
- Task 2.2: 1 hour (logging)
- Testing: 1-2 hours

**Phase 3: Invoice Links (2-3 hours)**
- Task 3.1: 1 hour (verify route)
- Task 3.2: 30 minutes (update message)
- Task 3.3: 30 minutes (config)
- Testing: 1 hour

**Phase 4: Error Handling (3-4 hours)**
- Task 4.1: 1 hour (phone mismatch)
- Task 4.2: 1 hour (timeout handling)
- Task 4.3: 1 hour (duplicate prevention)
- Task 4.4: 1 hour (failure notifications)

**Phase 5: Testing (3-4 hours)**
- Task 5.1: 1-2 hours (integration tests)
- Task 5.2: 1-2 hours (payment tests)
- Task 5.3: 1 hour (manual testing)

**Phase 6: Documentation (1-2 hours)**
- Task 6.1: 30 minutes (API docs)
- Task 6.2: 30 minutes (update PLAN)
- Task 6.3: 30-60 minutes (deployment docs)

### Critical Path
1. Phase 1 (one-line invoice) → Phase 5 (testing)
2. Phase 2 (payment button) → Phase 5 (testing)
3. Phase 3 and 4 can run in parallel with Phase 1-2
4. Phase 5 must follow all implementation phases
5. Phase 6 can start when Phase 5 is well underway

### Success Criteria
- [ ] Merchant can create invoice with: `invoice 254712345678 1000 Description`
- [ ] Customer receives WhatsApp message with payment button
- [ ] Customer clicks button and receives STK Push prompt
- [ ] Payment completes and both parties receive receipts
- [ ] All error cases handled gracefully
- [ ] All integration tests pass
- [ ] Manual testing checklist complete
- [ ] Documentation updated

### Dependencies
- Existing guided flow implementation (already working)
- Existing M-PESA STK Push integration (already working)
- Existing WhatsApp message sending (already working)
- Existing invoice database models (already working)

### Notes
- The guided flow is already fully implemented and working
- The main work is implementing the one-line command variant
- Payment button handling exists but needs STK trigger added
- Most error handling patterns already exist and can be reused
- The architecture is solid - we're just completing the feature set