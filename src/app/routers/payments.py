"""
Payment API router for M-PESA STK Push operations.

This module provides API endpoints for initiating M-PESA STK Push payments
and handling payment callbacks.
"""

from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request

from ..config import settings
from ..db import get_supabase
from ..schemas import PaymentCreate, PaymentResponse
from ..services.idempotency import check_callback_processed
from ..services.mpesa import MPesaService
from ..services.whatsapp import WhatsAppService
from ..utils.logging import get_logger
from ..utils.payment_retry import (
    can_retry_payment,
    get_payment_by_invoice_id,
    reset_invoice_to_pending,
)

logger = get_logger(__name__)

router = APIRouter()


def parse_callback_payload(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Parse M-PESA STK Push callback payload.

    Extracts relevant fields from the callback structure:
    - Body.stkCallback.MerchantRequestID
    - Body.stkCallback.CheckoutRequestID
    - Body.stkCallback.ResultCode
    - Body.stkCallback.ResultDesc
    - Body.stkCallback.CallbackMetadata.Item (if ResultCode == 0)

    Args:
        payload: Raw callback payload from M-PESA

    Returns:
        Parsed dict with extracted fields, or None if parsing fails

    Example successful callback:
        {
            "Body": {
                "stkCallback": {
                    "MerchantRequestID": "29115-34620561-1",
                    "CheckoutRequestID": "ws_CO_191220191020363925",
                    "ResultCode": 0,
                    "ResultDesc": "The service request is processed successfully.",
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "Amount", "Value": 1.00},
                            {"Name": "MpesaReceiptNumber", "Value": "NLJ7RT61SV"},
                            {"Name": "TransactionDate", "Value": 20191219102115},
                            {"Name": "PhoneNumber", "Value": 254708374149}
                        ]
                    }
                }
            }
        }

    Example failed callback:
        {
            "Body": {
                "stkCallback": {
                    "MerchantRequestID": "92334-77894064-1",
                    "CheckoutRequestID": "ws_CO_04112024174011655708374149",
                    "ResultCode": 1032,
                    "ResultDesc": "Request cancelled by user"
                }
            }
        }
    """
    try:
        # Navigate to stkCallback
        body = payload.get("Body", {})
        stk_callback = body.get("stkCallback", {})

        if not stk_callback:
            logger.warning("No stkCallback found in payload")
            return None

        # Extract required fields
        merchant_request_id = stk_callback.get("MerchantRequestID")
        checkout_request_id = stk_callback.get("CheckoutRequestID")
        result_code = stk_callback.get("ResultCode")
        result_desc = stk_callback.get("ResultDesc")

        if not checkout_request_id:
            logger.warning("No CheckoutRequestID in callback")
            return None

        if result_code is None:
            logger.warning("No ResultCode in callback")
            return None

        parsed = {
            "merchant_request_id": merchant_request_id,
            "checkout_request_id": checkout_request_id,
            "result_code": result_code,
            "result_desc": result_desc,
        }

        # If successful (ResultCode == 0), extract CallbackMetadata
        if result_code == 0:
            callback_metadata = stk_callback.get("CallbackMetadata", {})
            items = callback_metadata.get("Item", [])

            # Parse Item array into dict
            metadata_dict = {}
            for item in items:
                name = item.get("Name")
                value = item.get("Value")
                if name:
                    metadata_dict[name] = value

            parsed["amount"] = metadata_dict.get("Amount")
            parsed["mpesa_receipt"] = metadata_dict.get("MpesaReceiptNumber")
            parsed["transaction_date"] = metadata_dict.get("TransactionDate")
            parsed["phone_number"] = metadata_dict.get("PhoneNumber")

            logger.info(
                "Parsed successful callback",
                extra={
                    "checkout_request_id": checkout_request_id,
                    "mpesa_receipt": parsed["mpesa_receipt"],
                },
            )
        else:
            logger.info(
                "Parsed failed callback",
                extra={
                    "checkout_request_id": checkout_request_id,
                    "result_code": result_code,
                    "result_desc": result_desc,
                },
            )

        return parsed

    except (KeyError, TypeError, AttributeError) as e:
        logger.error(
            "Failed to parse callback payload",
            extra={"error": str(e), "payload_keys": list(payload.keys())},
            exc_info=True,
        )
        return None


def get_mpesa_service() -> MPesaService:
    """
    Dependency to get MPesaService instance.

    Returns:
        MPesaService instance configured with environment from settings
    """
    return MPesaService(environment=settings.mpesa_environment)


@router.post("/stk/initiate", response_model=PaymentResponse, status_code=200)
async def initiate_stk_push(
    payment_request: PaymentCreate,
    mpesa_service: MPesaService = Depends(get_mpesa_service),
) -> PaymentResponse:
    """
    Initiate M-PESA STK Push payment.

    Validates invoice exists and has status "SENT", checks idempotency key
    to prevent duplicate charges, creates payment record, and initiates
    STK Push request to customer's phone.

    Args:
        payment_request: Payment creation request with invoice_id and idempotency_key
        mpesa_service: M-PESA service instance

    Returns:
        PaymentResponse with payment details

    Raises:
        HTTPException 404: If invoice not found
        HTTPException 400: If invoice status is not "SENT"
        HTTPException 500: If STK Push initiation fails
    """
    supabase = get_supabase()

    logger.info(
        "Received STK Push initiate request",
        extra={
            "invoice_id": payment_request.invoice_id,
            "idempotency_key": payment_request.idempotency_key,
        },
    )

    # Check idempotency: if payment with same key exists, return cached response
    existing_payment_response = (
        supabase.table("payments")
        .select("*")
        .eq("idempotency_key", payment_request.idempotency_key)
        .execute()
    )
    existing_payment = existing_payment_response.data[0] if existing_payment_response.data else None

    if existing_payment:
        logger.info(
            "Duplicate STK Push request detected - returning cached response",
            extra={
                "idempotency_key": payment_request.idempotency_key,
                "payment_id": existing_payment["id"],
            },
        )
        return PaymentResponse.model_validate(existing_payment)

    # Retrieve invoice
    invoice_response = (
        supabase.table("invoices")
        .select("*")
        .eq("id", payment_request.invoice_id)
        .execute()
    )
    invoice = invoice_response.data[0] if invoice_response.data else None

    if not invoice:
        logger.warning(
            "Invoice not found",
            extra={"invoice_id": payment_request.invoice_id},
        )
        raise HTTPException(status_code=404, detail="Invoice not found")

    # Validate invoice status - allow SENT and FAILED (for retries)
    invoice_status = invoice["status"]

    if invoice_status not in ["SENT", "FAILED"]:
        logger.warning(
            "Invalid invoice status for payment",
            extra={
                "invoice_id": invoice["id"],
                "status": invoice_status,
                "expected_statuses": ["SENT", "FAILED"],
            },
        )
        raise HTTPException(
            status_code=400,
            detail=f"Invoice status must be SENT or FAILED (current: {invoice_status})",
        )

    # Handle FAILED status with retry logic
    if invoice_status == "FAILED":
        logger.info(
            "Attempting payment retry for FAILED invoice",
            extra={"invoice_id": invoice["id"]},
        )

        # Get existing payment record
        existing_payment = get_payment_by_invoice_id(invoice["id"], supabase)

        if not existing_payment:
            logger.warning(
                "No payment record found for FAILED invoice",
                extra={"invoice_id": invoice["id"]},
            )
            raise HTTPException(
                status_code=400,
                detail="Payment record not found. Please contact support.",
            )

        # Check if retry is allowed
        can_retry, error_message = can_retry_payment(existing_payment)

        if not can_retry:
            logger.info(
                "Payment retry blocked",
                extra={"invoice_id": invoice["id"], "reason": error_message},
            )
            raise HTTPException(
                status_code=400,
                detail=error_message,
            )

        # Increment retry count on existing payment
        current_retry_count = existing_payment.get("retry_count", 0)
        try:
            from datetime import datetime, timezone

            supabase.table("payments").update({
                "retry_count": current_retry_count + 1,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", existing_payment["id"]).execute()

            logger.info(
                "Incremented payment retry_count for retry attempt",
                extra={
                    "payment_id": existing_payment["id"],
                    "new_retry_count": current_retry_count + 1,
                },
            )
        except Exception as retry_error:
            logger.error(
                "Failed to increment retry_count",
                extra={
                    "error": str(retry_error),
                    "payment_id": existing_payment["id"],
                },
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail="Failed to update payment retry count. Please try again.",
            )

        # Reset invoice status to PENDING for retry
        if not reset_invoice_to_pending(invoice["id"], supabase):
            logger.error(
                "Failed to reset invoice status for retry",
                extra={"invoice_id": invoice["id"]},
            )
            raise HTTPException(
                status_code=500,
                detail="Failed to reset invoice status. Please try again.",
            )

        # Update local invoice object for processing below
        invoice["status"] = "PENDING"

        logger.info(
            "Payment retry approved - proceeding with STK Push",
            extra={
                "invoice_id": invoice["id"],
                "retry_count": current_retry_count + 1,
            },
        )

    # Convert amount from cents to whole KES
    amount_kes = round(invoice["amount_cents"] / 100)

    logger.info(
        "Creating payment record",
        extra={
            "invoice_id": invoice["id"],
            "amount_cents": invoice["amount_cents"],
            "amount_kes": amount_kes,
            "customer_msisdn": invoice["msisdn"],
        },
    )

    # Create Payment record with status INITIATED
    payment_data = {
        "id": str(uuid4()),
        "invoice_id": invoice["id"],
        "method": "MPESA_STK",
        "status": "INITIATED",
        "amount_cents": invoice["amount_cents"],
        "idempotency_key": payment_request.idempotency_key,
        "raw_request": {},  # Will be populated before STK call
        "raw_callback": None,
        "mpesa_receipt": None,
    }

    payment_response = supabase.table("payments").insert(payment_data).execute()
    payment = payment_response.data[0]

    logger.info(
        "Payment record created",
        extra={"payment_id": payment["id"], "status": payment["status"]},
    )

    # Prepare STK Push request
    # Determine account_reference based on payment method
    payment_method = invoice.get("mpesa_method")
    if payment_method == "PAYBILL":
        # For PAYBILL: use the merchant's paybill account number
        account_reference = invoice.get("mpesa_account_number", invoice["id"][:20])
    else:
        # For TILL (or fallback): use invoice ID
        account_reference = invoice["id"][:20]

    # Ensure account_reference is max 20 characters
    account_reference = account_reference[:20]
    transaction_desc = invoice["description"][:20]  # Max 20 characters

    try:
        # Initiate STK Push
        # Pass payment method from invoice (PAYBILL or TILL)
        payment_method = invoice.get("mpesa_method")
        stk_response = await mpesa_service.initiate_stk_push(
            phone_number=invoice["msisdn"],
            amount=amount_kes,
            account_reference=account_reference,
            transaction_desc=transaction_desc,
            payment_method=payment_method,
        )

        # Update payment with raw request and response
        updated_payment_data = {
            "raw_request": {
                "phone_number": invoice["msisdn"],
                "amount": amount_kes,
                "account_reference": account_reference,
                "transaction_desc": transaction_desc,
                "stk_response": stk_response,
            },
            "checkout_request_id": stk_response.get("CheckoutRequestID"),
            "merchant_request_id": stk_response.get("MerchantRequestID"),
        }

        supabase.table("payments").update(updated_payment_data).eq("id", payment["id"]).execute()
        payment.update(updated_payment_data)

        logger.info(
            "STK Push initiated successfully",
            extra={
                "payment_id": payment["id"],
                "invoice_id": invoice["id"],
                "stk_response": stk_response,
            },
        )

        return PaymentResponse.model_validate(payment)

    except Exception as e:
        # Update payment status to FAILED
        failed_payment_data = {
            "status": "FAILED",
            "raw_request": {
                "phone_number": invoice["msisdn"],
                "amount": amount_kes,
                "account_reference": account_reference,
                "transaction_desc": transaction_desc,
                "error": str(e),
            },
        }

        supabase.table("payments").update(failed_payment_data).eq("id", payment["id"]).execute()

        logger.error(
            "STK Push initiation failed",
            extra={
                "payment_id": payment["id"],
                "invoice_id": invoice["id"],
                "error": str(e),
            },
            exc_info=True,
        )

        raise HTTPException(
            status_code=500,
            detail=f"Failed to initiate STK Push: {str(e)}",
        )


@router.post("/stk/callback", status_code=200)
async def handle_stk_callback(
    request: Request,
) -> Dict[str, str]:
    """
    Handle M-PESA STK Push callback.

    Receives payment result from M-PESA after customer completes or cancels payment.
    Updates payment and invoice status, sends receipts to customer and merchant.

    IMPORTANT: Must return 200 OK within 30 seconds to prevent M-PESA retries.

    Args:
        request: FastAPI request object containing callback payload

    Returns:
        Success response dict: {"ResultCode": "0", "ResultDesc": "Accepted"}

    Callback Payload Structure:
        Success (ResultCode == 0):
            {
                "Body": {
                    "stkCallback": {
                        "MerchantRequestID": "...",
                        "CheckoutRequestID": "...",
                        "ResultCode": 0,
                        "ResultDesc": "Success",
                        "CallbackMetadata": {
                            "Item": [
                                {"Name": "Amount", "Value": 1.00},
                                {"Name": "MpesaReceiptNumber", "Value": "NLJ7RT61SV"},
                                {"Name": "TransactionDate", "Value": 20191219102115},
                                {"Name": "PhoneNumber", "Value": 254708374149}
                            ]
                        }
                    }
                }
            }

        Failure (ResultCode != 0):
            {
                "Body": {
                    "stkCallback": {
                        "MerchantRequestID": "...",
                        "CheckoutRequestID": "...",
                        "ResultCode": 1032,
                        "ResultDesc": "Request cancelled by user"
                    }
                }
            }
    """
    supabase = get_supabase()

    # Always return 200 OK to M-PESA, regardless of processing outcome
    success_response = {"ResultCode": "0", "ResultDesc": "Accepted"}

    try:
        # Parse request body
        payload = await request.json()

        logger.info(
            "Received STK Push callback",
            extra={"payload_keys": list(payload.keys())},
        )

        # Parse callback payload
        parsed = parse_callback_payload(payload)

        if not parsed:
            logger.error("Failed to parse callback payload")
            return success_response

        checkout_request_id = parsed["checkout_request_id"]
        result_code = parsed["result_code"]

        # Check for duplicate callback (idempotency)
        existing_processed = await check_callback_processed(checkout_request_id, supabase)
        if existing_processed:
            logger.info(
                "Duplicate callback detected - already processed",
                extra={
                    "checkout_request_id": checkout_request_id,
                    "payment_status": existing_processed["status"],
                },
            )
            return success_response

        # Find payment record by CheckoutRequestID
        payment_response = (
            supabase.table("payments")
            .select("*")
            .eq("checkout_request_id", checkout_request_id)
            .execute()
        )
        payment = payment_response.data[0] if payment_response.data else None

        if not payment:
            logger.warning(
                "Payment not found for CheckoutRequestID",
                extra={"checkout_request_id": checkout_request_id},
            )
            return success_response

        # Load related invoice
        invoice_response = (
            supabase.table("invoices")
            .select("*")
            .eq("id", payment["invoice_id"])
            .execute()
        )
        invoice = invoice_response.data[0] if invoice_response.data else None

        if not invoice:
            logger.error(
                "Invoice not found for payment",
                extra={"payment_id": payment["id"], "invoice_id": payment["invoice_id"]},
            )
            return success_response

        # Update payment and invoice based on result
        if result_code == 0:
            # Payment successful
            payment_update_data = {
                "status": "SUCCESS",
                "mpesa_receipt": parsed.get("mpesa_receipt"),
                "raw_callback": payload,
            }
            supabase.table("payments").update(payment_update_data).eq("id", payment["id"]).execute()
            payment.update(payment_update_data)

            invoice_update_data = {
                "status": "PAID",
                "pay_ref": parsed.get("mpesa_receipt"),
            }
            supabase.table("invoices").update(invoice_update_data).eq("id", invoice["id"]).execute()
            invoice.update(invoice_update_data)

            logger.info(
                "Payment successful",
                extra={
                    "payment_id": payment["id"],
                    "invoice_id": invoice["id"],
                    "mpesa_receipt": payment["mpesa_receipt"],
                },
            )

            # Send receipts to customer and merchant
            whatsapp_service = WhatsAppService()

            # Convert amount from cents to KES
            amount_kes = invoice["amount_cents"] / 100

            try:
                # Send receipt to customer
                await whatsapp_service.send_receipt_to_customer(
                    customer_msisdn=invoice["msisdn"],
                    invoice_id=invoice["id"],
                    amount_kes=amount_kes,
                    mpesa_receipt=payment.get("mpesa_receipt") or "N/A",
                    db_session=supabase,
                )

                # Send receipt to merchant
                await whatsapp_service.send_receipt_to_merchant(
                    merchant_msisdn=invoice["merchant_msisdn"],
                    invoice_id=invoice["id"],
                    customer_msisdn=invoice["msisdn"],
                    amount_kes=amount_kes,
                    mpesa_receipt=payment.get("mpesa_receipt") or "N/A",
                    db_session=supabase,
                )

                logger.info(
                    "Receipts sent successfully",
                    extra={"invoice_id": invoice["id"], "payment_id": payment["id"]},
                )

            except Exception as e:
                logger.error(
                    "Failed to send receipts",
                    extra={
                        "error": str(e),
                        "invoice_id": invoice["id"],
                        "payment_id": payment["id"],
                    },
                    exc_info=True,
                )
                # Don't fail callback due to notification errors

        else:
            # Payment failed
            payment_update_data = {
                "status": "FAILED",
                "raw_callback": payload,
            }
            supabase.table("payments").update(payment_update_data).eq("id", payment["id"]).execute()
            payment.update(payment_update_data)

            invoice_update_data = {
                "status": "FAILED",
            }
            supabase.table("invoices").update(invoice_update_data).eq("id", invoice["id"]).execute()
            invoice.update(invoice_update_data)

            logger.info(
                "Payment failed",
                extra={
                    "payment_id": payment["id"],
                    "invoice_id": invoice["id"],
                    "result_code": result_code,
                    "result_desc": parsed.get("result_desc"),
                },
            )

            # Notify merchant and customer of payment failure (Task 4.4)
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
                    f"Payment failed for invoice {invoice['id']}\n"
                    f"Customer: {invoice['msisdn']}\n"
                    f"Reason: {failure_reason}"
                )
                await whatsapp_service.send_message(
                    invoice["merchant_msisdn"],
                    merchant_message
                )

                # Notify customer
                customer_message = (
                    f"Payment for invoice {invoice['id']} was not completed.\n"
                    f"Reason: {failure_reason}\n"
                    f"You can try again by clicking the Pay button in the invoice message."
                )
                await whatsapp_service.send_message(
                    invoice["msisdn"],
                    customer_message
                )

                logger.info(
                    "Payment failure notifications sent",
                    extra={"invoice_id": invoice["id"]},
                )

            except Exception as notify_error:
                logger.error(
                    "Failed to send payment failure notifications",
                    extra={
                        "error": str(notify_error),
                        "invoice_id": invoice["id"],
                    },
                    exc_info=True,
                )

        return success_response

    except Exception as e:
        logger.error(
            "Error processing STK callback",
            extra={"error": str(e)},
            exc_info=True,
        )
        # Still return 200 OK to prevent retries
        return success_response


@router.post("/mpesa/c2b/confirmation", status_code=200)
async def handle_c2b_confirmation(
    request: Request,
) -> Dict[str, str]:
    """
    Handle M-PESA C2B confirmation callback.

    Receives payment notifications from M-PESA when customers pay to a registered
    Paybill or Till number. Matches payments to invoices based on shortcode and
    account reference, calculates outstanding balance, and updates invoice status.

    IMPORTANT: Must return 200 OK to M-PESA to acknowledge receipt, even on errors.

    Args:
        request: FastAPI request object containing C2B confirmation payload

    Returns:
        Success response dict: {"ResultCode": "0", "ResultDesc": "Accepted"}

    Expected C2B Payload Structure:
        {
            "TransID": "NLJ7RT61SV",
            "TransAmount": "100.00",
            "BillRefNumber": "account123",
            "MSISDN": "254708374149",
            "BusinessShortCode": "600984",
            "OrgAccountBalance": "1000.00",
            "ThirdPartyTransID": "",
            "TransTime": "20191219102115",
            "FirstName": "John",
            "MiddleName": "",
            "LastName": "Doe"
        }
    """
    supabase = get_supabase()

    # Always return 200 OK to M-PESA, regardless of processing outcome
    success_response = {"ResultCode": "0", "ResultDesc": "Accepted"}

    try:
        # Parse request body
        payload = await request.json()

        logger.info(
            "Received C2B confirmation callback",
            extra={"payload": payload},
        )

        # Extract required fields from C2B payload
        trans_id = payload.get("TransID")
        trans_amount = payload.get("TransAmount")
        bill_ref_number = payload.get("BillRefNumber")
        msisdn = payload.get("MSISDN")
        business_shortcode = payload.get("BusinessShortCode")

        # Validate required fields
        if not trans_id:
            logger.error("Missing TransID in C2B payload")
            return success_response

        if not trans_amount:
            logger.error("Missing TransAmount in C2B payload", extra={"trans_id": trans_id})
            return success_response

        if not bill_ref_number:
            logger.error("Missing BillRefNumber in C2B payload", extra={"trans_id": trans_id})
            return success_response

        if not msisdn:
            logger.error("Missing MSISDN in C2B payload", extra={"trans_id": trans_id})
            return success_response

        if not business_shortcode:
            logger.error("Missing BusinessShortCode in C2B payload", extra={"trans_id": trans_id})
            return success_response

        # Convert TransAmount to cents
        try:
            amount_paid_cents = int(float(trans_amount) * 100)
        except (ValueError, TypeError) as e:
            logger.error(
                "Invalid TransAmount format",
                extra={"trans_id": trans_id, "trans_amount": trans_amount, "error": str(e)},
            )
            return success_response

        # Convert BusinessShortCode to string for comparison
        business_shortcode_str = str(business_shortcode)

        logger.info(
            "Parsed C2B confirmation",
            extra={
                "trans_id": trans_id,
                "amount_paid_cents": amount_paid_cents,
                "bill_ref_number": bill_ref_number,
                "msisdn": msisdn,
                "business_shortcode": business_shortcode_str,
            },
        )

        # Check for duplicate payment (idempotency) - use TransID as unique identifier
        existing_payment_response = (
            supabase.table("payments")
            .select("*")
            .eq("checkout_request_id", trans_id)
            .execute()
        )
        existing_payment = existing_payment_response.data[0] if existing_payment_response.data else None

        if existing_payment:
            logger.info(
                "Duplicate C2B payment detected - already processed",
                extra={
                    "trans_id": trans_id,
                    "payment_id": existing_payment["id"],
                    "payment_status": existing_payment["status"],
                },
            )
            return success_response

        # Match payment to invoice
        # Query invoices where:
        # - (mpesa_paybill_number OR mpesa_till_number) matches BusinessShortCode
        # - mpesa_account_number matches BillRefNumber
        # - c2b_notifications_enabled = TRUE
        # - status IN ('SENT', 'PENDING', 'FAILED')
        # Order by created_at DESC, take most recent match

        # Build query using Supabase OR filter
        invoice_response = (
            supabase.table("invoices")
            .select("*")
            .eq("mpesa_account_number", bill_ref_number)
            .eq("c2b_notifications_enabled", True)
            .in_("status", ["SENT", "PENDING", "FAILED"])
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        # Filter results by shortcode match (Supabase doesn't support complex OR in Python client)
        matching_invoice = None
        for inv in invoice_response.data:
            paybill = inv.get("mpesa_paybill_number")
            till = inv.get("mpesa_till_number")
            if (paybill and str(paybill) == business_shortcode_str) or \
               (till and str(till) == business_shortcode_str):
                matching_invoice = inv
                break

        if not matching_invoice:
            logger.warning(
                "No matching invoice found for C2B payment",
                extra={
                    "trans_id": trans_id,
                    "business_shortcode": business_shortcode_str,
                    "bill_ref_number": bill_ref_number,
                },
            )
            # Log unmatched payment for reconciliation but acknowledge to M-PESA
            return success_response

        logger.info(
            "Matched C2B payment to invoice",
            extra={
                "trans_id": trans_id,
                "invoice_id": matching_invoice["id"],
                "invoice_status": matching_invoice["status"],
            },
        )

        # Calculate outstanding balance
        invoice_total_cents = matching_invoice["amount_cents"]
        outstanding_balance_cents = max(0, invoice_total_cents - amount_paid_cents)

        logger.info(
            "Calculated payment balance",
            extra={
                "trans_id": trans_id,
                "invoice_id": matching_invoice["id"],
                "invoice_total_cents": invoice_total_cents,
                "amount_paid_cents": amount_paid_cents,
                "outstanding_balance_cents": outstanding_balance_cents,
            },
        )

        # Create payment record
        from datetime import datetime, timezone
        from uuid import uuid4

        payment_id = str(uuid4())
        payment_data = {
            "id": payment_id,
            "invoice_id": matching_invoice["id"],
            "method": "C2B",
            "status": "SUCCESS",
            "mpesa_receipt": trans_id,
            "amount_cents": amount_paid_cents,
            "checkout_request_id": trans_id,  # Use TransID as unique identifier
            "raw_request": {},
            "raw_callback": payload,
            "idempotency_key": f"c2b-{trans_id}",  # Generate idempotency key from TransID
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            payment_response = supabase.table("payments").insert(payment_data).execute()
            payment = payment_response.data[0] if payment_response.data else None

            if not payment:
                logger.error(
                    "Failed to create payment record",
                    extra={"trans_id": trans_id, "invoice_id": matching_invoice["id"]},
                )
                return success_response

            logger.info(
                "Created C2B payment record",
                extra={
                    "payment_id": payment["id"],
                    "invoice_id": matching_invoice["id"],
                    "trans_id": trans_id,
                },
            )

        except Exception as payment_error:
            logger.error(
                "Error creating C2B payment record",
                extra={
                    "trans_id": trans_id,
                    "invoice_id": matching_invoice["id"],
                    "error": str(payment_error),
                },
                exc_info=True,
            )
            return success_response

        # Update invoice status if fully paid
        if outstanding_balance_cents == 0:
            try:
                invoice_update_data = {
                    "status": "PAID",
                    "pay_ref": trans_id,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                supabase.table("invoices").update(invoice_update_data).eq("id", matching_invoice["id"]).execute()

                logger.info(
                    "Invoice marked as PAID (full payment received)",
                    extra={
                        "invoice_id": matching_invoice["id"],
                        "trans_id": trans_id,
                        "amount_paid_cents": amount_paid_cents,
                    },
                )

            except Exception as invoice_error:
                logger.error(
                    "Error updating invoice status to PAID",
                    extra={
                        "invoice_id": matching_invoice["id"],
                        "trans_id": trans_id,
                        "error": str(invoice_error),
                    },
                    exc_info=True,
                )
                # Don't return error - payment record was created successfully
        else:
            logger.info(
                "Partial payment received - invoice remains in current status",
                extra={
                    "invoice_id": matching_invoice["id"],
                    "trans_id": trans_id,
                    "amount_paid_cents": amount_paid_cents,
                    "outstanding_balance_cents": outstanding_balance_cents,
                    "invoice_status": matching_invoice["status"],
                },
            )

        logger.info(
            "C2B confirmation processed successfully",
            extra={
                "trans_id": trans_id,
                "invoice_id": matching_invoice["id"],
                "payment_id": payment_id,
                "fully_paid": outstanding_balance_cents == 0,
            },
        )

        return success_response

    except Exception as e:
        logger.error(
            "Error processing C2B confirmation callback",
            extra={"error": str(e)},
            exc_info=True,
        )
        # Still return 200 OK to prevent retries
        return success_response