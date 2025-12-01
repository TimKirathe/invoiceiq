# Payment Retry Implementation Guide

## Overview
This document describes the implementation of payment retry logic for InvoiceIQ, allowing customers to retry failed payments with proper rate limiting and retry count validation.

## Implementation Summary

### 1. Database Schema Changes
**File**: `/Users/timothykirathe/Documents/invoicing-product/invoiceiq/scripts/init_db.sql`

Added `retry_count` field to the `payments` table:
```sql
retry_count INTEGER DEFAULT 0,
```

**Database Migration Required**: Yes
- You need to run an ALTER TABLE statement on your Supabase database:
```sql
ALTER TABLE payments ADD COLUMN retry_count INTEGER DEFAULT 0;
```

### 2. Helper Functions Created
**File**: `/Users/timothykirathe/Documents/invoicing-product/invoiceiq/src/app/utils/payment_retry.py`

Created new utility module with the following functions:

#### `get_payment_by_invoice_id(invoice_id: str, supabase) -> Optional[Dict]`
- Gets the most recent payment record for an invoice
- Returns None if no payment found

#### `can_retry_payment(payment: Dict) -> Tuple[bool, Optional[str]]`
- Checks if payment retry is allowed based on:
  - **Retry count limit**: Maximum 1 retry (2 total attempts)
  - **Rate limit**: 90 seconds must pass since last failure
- Returns tuple of (can_retry: bool, error_message: Optional[str])
- Uses timezone-aware datetime comparisons

#### `increment_retry_count(payment_id: str, supabase) -> bool`
- Increments the retry_count for a payment record (currently not used, kept for potential future use)

#### `reset_invoice_to_pending(invoice_id: str, supabase) -> bool`
- Resets invoice status from FAILED to PENDING
- Required before initiating retry attempt

### 3. Updated invoice_view.py
**File**: `/Users/timothykirathe/Documents/invoicing-product/invoiceiq/src/app/routers/invoice_view.py`

**Changes Made**:
1. Added imports for retry helper functions
2. Modified `initiate_payment()` endpoint to handle FAILED invoices:
   - Checks if invoice status is FAILED
   - Retrieves existing payment record
   - Validates retry eligibility using `can_retry_payment()`
   - Resets invoice to PENDING if retry allowed
   - Increments retry_count on existing payment
   - Proceeds with normal STK Push flow

**Error Messages**:
- Max retries: "Maximum payment attempts reached. Please contact support."
- Rate limited: "Please wait {seconds_remaining} seconds before retrying payment."

### 4. Files Still Requiring Updates

#### A. `/Users/timothykirathe/Documents/invoicing-product/invoiceiq/src/app/routers/payments.py`

**Location**: `initiate_stk_push()` function (lines 161-360)

**Required Changes**:
1. Add import at top of file:
```python
from ..utils.payment_retry import (
    can_retry_payment,
    get_payment_by_invoice_id,
    reset_invoice_to_pending,
)
```

2. Replace the invoice status check (currently at line 231):

**REPLACE THIS**:
```python
# Validate invoice status
if invoice["status"] != "SENT":
    logger.warning(
        "Invalid invoice status for payment",
        extra={
            "invoice_id": invoice["id"],
            "status": invoice["status"],
            "expected_status": "SENT",
        },
    )
    raise HTTPException(
        status_code=400,
        detail=f"Invoice status must be SENT (current: {invoice['status']})",
    )
```

**WITH THIS**:
```python
# Validate invoice status
if invoice["status"] == "PAID":
    logger.warning(
        "Invoice already paid",
        extra={"invoice_id": invoice["id"]},
    )
    raise HTTPException(status_code=400, detail="Invoice already paid")

if invoice["status"] == "CANCELLED":
    logger.warning(
        "Invoice cancelled",
        extra={"invoice_id": invoice["id"]},
    )
    raise HTTPException(status_code=400, detail="Invoice has been cancelled")

# Handle FAILED status with retry logic
if invoice["status"] == "FAILED":
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
            status_code=404,
            detail="Payment record not found for this invoice"
        )

    # Check if retry is allowed
    can_retry, error_message = can_retry_payment(existing_payment)

    if not can_retry:
        logger.info(
            "Payment retry blocked",
            extra={"invoice_id": invoice["id"], "reason": error_message},
        )
        raise HTTPException(status_code=400, detail=error_message)

    # Retry is allowed - reset invoice status to PENDING
    if not reset_invoice_to_pending(invoice["id"], supabase):
        logger.error(
            "Failed to reset invoice status for retry",
            extra={"invoice_id": invoice["id"]},
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to reset invoice status"
        )

    # Update invoice object for processing below
    invoice["status"] = "PENDING"

    # Increment retry count on existing payment
    current_retry_count = existing_payment.get("retry_count", 0)
    supabase.table("payments").update(
        {"retry_count": current_retry_count + 1}
    ).eq("id", existing_payment["id"]).execute()

    logger.info(
        "Payment retry approved - proceeding with STK Push",
        extra={"invoice_id": invoice["id"], "retry_count": current_retry_count + 1},
    )

# If invoice is still not SENT or PENDING at this point, reject
if invoice["status"] not in ["SENT", "PENDING"]:
    logger.warning(
        "Invalid invoice status for payment",
        extra={
            "invoice_id": invoice["id"],
            "status": invoice["status"],
        },
    )
    raise HTTPException(
        status_code=400,
        detail=f"Invalid invoice status: {invoice['status']}"
    )
```

#### B. `/Users/timothykirathe/Documents/invoicing-product/invoiceiq/src/app/routers/whatsapp.py`

**Location**: Payment button click handler (lines 256-417)

**Required Changes**:
1. Add import at top of file:
```python
from ..utils.payment_retry import (
    can_retry_payment,
    get_payment_by_invoice_id,
    reset_invoice_to_pending,
)
```

2. Find the section that handles invoice status validation for payment (around line 233-241):

**REPLACE THIS**:
```python
# Validate invoice status allows payment
elif invoice["status"] not in ["SENT", "PENDING"]:
    logger.warning(
        "Invalid invoice status for payment",
        extra={"invoice_id": invoice_id, "status": invoice["status"]},
    )
    response_text = (
        f"Invoice {invoice_id} cannot be paid (status: {invoice['status']}). "
        f"Please contact the merchant."
    )
```

**WITH THIS**:
```python
# Validate invoice status allows payment
elif invoice["status"] == "CANCELLED":
    logger.warning(
        "Invoice cancelled",
        extra={"invoice_id": invoice_id},
    )
    response_text = (
        f"Invoice {invoice_id} has been cancelled and cannot be paid. "
        f"Please contact the merchant."
    )
# Handle FAILED status with retry logic
elif invoice["status"] == "FAILED":
    logger.info(
        "Attempting payment retry for FAILED invoice from WhatsApp button",
        extra={"invoice_id": invoice_id},
    )

    # Get existing payment record
    existing_payment = get_payment_by_invoice_id(invoice_id, supabase)

    if not existing_payment:
        logger.warning(
            "No payment record found for FAILED invoice",
            extra={"invoice_id": invoice_id},
        )
        response_text = (
            "Payment record not found. Please contact the merchant."
        )
    else:
        # Check if retry is allowed
        can_retry, error_message = can_retry_payment(existing_payment)

        if not can_retry:
            logger.info(
                "Payment retry blocked from WhatsApp",
                extra={"invoice_id": invoice_id, "reason": error_message},
            )
            response_text = error_message
        else:
            # Retry is allowed - reset invoice status to PENDING
            if not reset_invoice_to_pending(invoice_id, supabase):
                logger.error(
                    "Failed to reset invoice status for retry",
                    extra={"invoice_id": invoice_id},
                )
                response_text = (
                    "Failed to reset invoice status. Please try again."
                )
            else:
                # Update invoice object for processing below
                invoice["status"] = "PENDING"

                # Increment retry count on existing payment
                current_retry_count = existing_payment.get("retry_count", 0)
                try:
                    supabase.table("payments").update(
                        {"retry_count": current_retry_count + 1}
                    ).eq("id", existing_payment["id"]).execute()

                    logger.info(
                        "Payment retry approved from WhatsApp - proceeding with STK Push",
                        extra={
                            "invoice_id": invoice_id,
                            "retry_count": current_retry_count + 1
                        },
                    )
                    # Don't set response_text - allow normal flow to continue
                except Exception as retry_error:
                    logger.error(
                        "Failed to increment retry_count",
                        extra={
                            "error": str(retry_error),
                            "payment_id": existing_payment["id"]
                        },
                        exc_info=True,
                    )
                    response_text = (
                        "Failed to update payment retry count. Please try again."
                    )
elif invoice["status"] not in ["SENT", "PENDING"]:
    logger.warning(
        "Invalid invoice status for payment",
        extra={"invoice_id": invoice_id, "status": invoice["status"]},
    )
    response_text = (
        f"Invoice {invoice_id} cannot be paid (status: {invoice['status']}). "
        f"Please contact the merchant."
    )
```

## Testing Checklist

After implementing the changes in `payments.py` and `whatsapp.py`:

### 1. Database Migration
- [ ] Run ALTER TABLE statement on Supabase database
- [ ] Verify retry_count column exists with DEFAULT 0

### 2. Unit Tests (if applicable)
- [ ] Test `can_retry_payment()` with retry_count = 0 (should allow)
- [ ] Test `can_retry_payment()` with retry_count = 1 (should block)
- [ ] Test `can_retry_payment()` with recent failure (< 90s, should block)
- [ ] Test `can_retry_payment()` with old failure (> 90s, should allow)

### 3. Integration Tests
- [ ] Create invoice and let payment fail
- [ ] Verify invoice status is FAILED
- [ ] Attempt retry immediately (should be blocked by rate limit)
- [ ] Wait 90+ seconds and retry (should succeed)
- [ ] Let second payment fail
- [ ] Attempt third retry (should be blocked by max retry count)

### 4. Manual Testing via Web Interface
- [ ] Navigate to invoice with FAILED status
- [ ] Click "Pay with M-PESA" before 90 seconds (should show wait message)
- [ ] Wait 90 seconds and retry (should initiate STK Push)
- [ ] Let payment fail again
- [ ] Attempt third payment (should show max attempts reached)

### 5. Manual Testing via WhatsApp
- [ ] Click payment button on FAILED invoice before 90 seconds
- [ ] Verify wait message is sent
- [ ] Click payment button after 90 seconds
- [ ] Verify STK Push is initiated
- [ ] Let payment fail
- [ ] Click payment button (should show max attempts message)

## Configuration Constants

Located in `/Users/timothykirathe/Documents/invoicing-product/invoiceiq/src/app/utils/payment_retry.py`:

- `MAX_RETRY_COUNT = 1` - Total of 2 attempts (1 original + 1 retry)
- `RETRY_RATE_LIMIT_SECONDS = 90` - 90 seconds between retry attempts

To change these values, edit the constants in the file.

## Error Handling

The implementation includes comprehensive error handling:
- All database operations wrapped in try-except blocks
- Failed retry count increments logged but don't block retry
- Missing timestamps handled gracefully (allows retry)
- Timezone-aware datetime comparisons prevent UTC/local timezone issues

## Logging

All retry operations are logged with structured logging:
- Retry attempts logged at INFO level
- Blocked retries logged at INFO level with reason
- Errors logged at ERROR level with full stack traces
- All logs include relevant context (invoice_id, payment_id, retry_count, etc.)

## Implementation Notes

1. **Option A (Reuse Payment Record)** was chosen as specified
2. **updated_at** field from payments table is used for rate limiting
3. Retry count is incremented BEFORE initiating the retry STK Push
4. Invoice status is reset to PENDING before retry
5. All three entry points (invoice_view.py, payments.py, whatsapp.py) implement the same retry logic

## Deployment Steps

1. Apply database migration (ALTER TABLE)
2. Deploy code changes
3. Monitor logs for any retry-related errors
4. Test with real failed payments
5. Adjust rate limit/retry count if needed based on real-world usage

## Future Enhancements

Potential improvements not included in this implementation:
- Exponential backoff for rate limiting
- Different retry counts for different failure reasons
- Admin override to reset retry count
- Retry history/audit trail
- Automatic retry after X seconds (background job)