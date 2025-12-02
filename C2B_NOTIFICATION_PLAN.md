# C2B Payment Notification Feature - Implementation Plan

## Overview

Enable vendors to receive WhatsApp notifications when customers pay their Paybill/Till number, with automatic invoice matching and balance tracking.

---

## Phase 1: Database Schema Updates ✅ COMPLETED

### 1.1 Add Vendor Notification Preferences

**File**: `scripts/init_db.sql`

Add column to `invoices` table:

```sql
ALTER TABLE invoices ADD COLUMN c2b_notifications_enabled BOOLEAN DEFAULT FALSE;
```

Purpose: Track whether vendor wants C2B notifications for this invoice's payment method.

### 1.2 Add C2B Registration Tracking

**File**: `scripts/init_db.sql`

Create new table:

```sql
CREATE TABLE c2b_registrations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shortcode VARCHAR(20) NOT NULL,
    shortcode_type VARCHAR(10) NOT NULL, -- 'PAYBILL' or 'TILL'
    account_number VARCHAR(100), -- For PAYBILL only, NULL for TILL
    vendor_phone VARCHAR(20) NOT NULL,
    confirmation_url TEXT NOT NULL,
    registration_status VARCHAR(20) DEFAULT 'PENDING',
    daraja_response JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(shortcode, account_number)
);
```

Purpose: Track C2B URL registrations per shortcode/account combination.

---

## Phase 2: WhatsApp Invoice Flow Updates ✅ COMPLETED

### 2.1 Add New State

**File**: `src/app/services/whatsapp.py`

After `STATE_ASK_SAVE_PAYMENT_METHOD`, add:

```python
STATE_ASK_C2B_NOTIFICATIONS = "ASK_C2B_NOTIFICATIONS"
```

Update `STATE_BACK_MAP` to include navigation from this state.

### 2.2 Update State Machine Logic

**File**: `src/app/routers/whatsapp.py`

After handling `STATE_ASK_SAVE_PAYMENT_METHOD` response:

- If vendor chooses to save payment method → Ask about C2B notifications
- Present interactive buttons: "Yes, notify me" / "No thanks"
- Store preference in invoice data

Message format:

```
Would you like to receive WhatsApp notifications when customers pay to your [Paybill/Till]?

You'll get instant alerts with:
✓ Payment amount
✓ Customer details
✓ Outstanding balance

1 - Yes, notify me
2 - No thanks
```

### 2.3 Capture C2B Notification Preference

Store `c2b_notifications_enabled` flag when creating the invoice.

---

## Phase 3: M-PESA C2B Registration Service

### 3.1 Add C2B Registration Method

**File**: `src/app/services/mpesa.py`

Add new method:

```python
async def register_c2b_url(
    shortcode: str,
    shortcode_type: str,  # 'PAYBILL' or 'TILL'
    account_number: Optional[str] = None
) -> Dict[str, Any]:
```

Implementation:

- Get OAuth access token (reuse existing auth method)
- Construct request to `/mpesa/c2b/v2/registerurl`
- Request body:
  ```json
  {
    "ShortCode": shortcode,
    "ResponseType": "Completed",
    "ConfirmationURL": "https://api.invoiceiq.org/mpesa/c2b/confirmation"
  }
  ```
- Handle success/failure responses
- Return registration result

### 3.2 Handle Registration Timing

Call `register_c2b_url()` when:

- Invoice is created AND vendor enabled C2B notifications
- Check if shortcode+account is already registered (query `c2b_registrations` table)
- If not registered: Register with Daraja and store result
- If already registered: Skip registration, just enable notifications for this invoice

---

## Phase 4: C2B Confirmation Endpoint

### 4.1 Create Confirmation Handler

**File**: `src/app/routers/payments.py` (or new `src/app/routers/c2b.py`)

Add endpoint:

```python
@router.post("/mpesa/c2b/confirmation")
async def handle_c2b_confirmation(payload: Dict[str, Any]):
```

### 4.2 Parse C2B Callback Payload

Expected fields from M-PESA:

- `TransID`: M-PESA transaction ID
- `TransAmount`: Amount paid
- `BillRefNumber`: Account reference (matches invoice's `mpesa_account_number`)
- `MSISDN`: Customer phone number
- `BusinessShortCode`: The Paybill/Till number
- `OrgAccountBalance`: Organization balance after transaction

### 4.3 Match Payment to Invoice

Logic:

1. Extract `BusinessShortCode` and `BillRefNumber` from payload
2. Query invoices table:
   ```sql
   WHERE (mpesa_paybill_number = BusinessShortCode OR mpesa_till_number = BusinessShortCode)
   AND mpesa_account_number = BillRefNumber
   AND c2b_notifications_enabled = TRUE
   AND status IN ('SENT', 'PENDING', 'FAILED')
   ```
3. If no match found: Log and return 200 (acknowledge but don't process)
4. If match found: Continue to notification

### 4.4 Calculate Outstanding Balance

```python
amount_paid_cents = int(TransAmount * 100)  # Convert to cents
invoice_total_cents = invoice["amount_cents"]
outstanding_balance_cents = max(0, invoice_total_cents - amount_paid_cents)
```

### 4.5 Update Invoice Status

If `outstanding_balance_cents == 0`:

- Update invoice status to 'PAID'
- Set `paid_at` timestamp

If `outstanding_balance_cents > 0`:

- Invoice remains in current status (partial payment)
- Track payment in `payments` table

### 4.6 Create Payment Record

Insert into `payments` table:

```python
{
    "invoice_id": invoice_id,
    "checkout_request_id": TransID,  # M-PESA TransID
    "mpesa_receipt_number": TransID,
    "amount_cents": amount_paid_cents,
    "phone_number": MSISDN,
    "status": "SUCCESS",
    "result_desc": "C2B payment confirmation",
    "payment_method": "C2B",  # New payment method type
}
```

---

## Phase 5: WhatsApp Notification Service

### 5.1 Send Payment Confirmation

**File**: `src/app/services/whatsapp.py`

Add method:

```python
async def send_c2b_payment_notification(
    vendor_phone: str,
    customer_phone: str,
    amount_paid: int,  # in cents
    outstanding_balance: int,  # in cents
    invoice_id: str,
    trans_id: str
):
```

### 5.2 Message Format

```
💰 Payment Received!

Invoice: {invoice_id}
Customer: {customer_phone}
Amount: KES {amount_paid/100:,.2f}
M-PESA Ref: {trans_id}

{balance_status_message}
```

Where `balance_status_message` is:

- If fully paid: "✅ Invoice fully paid!"
- If partial: "Remaining: KES {outstanding_balance/100:,.2f}"

### 5.3 Send Notification

Call from C2B confirmation handler after processing payment.

---

## Phase 6: Error Handling & Edge Cases

### 6.1 Registration Failures

If C2B URL registration fails:

- Log error details
- Store failure in `c2b_registrations` table
- Don't block invoice creation
- Vendor can still use STK Push normally

### 6.2 Duplicate Registrations

Daraja error 500.003.1001 "URLs already registered":

- Check if registration exists in our database
- If yes: Mark as successful, continue
- If no: Log warning, may need manual intervention

### 6.3 Payment Matching Failures

If no invoice matches `BillRefNumber`:

- Log unmatched payment for reconciliation
- Return 200 to acknowledge
- Admin can manually match later

### 6.4 Multiple Partial Payments

Support multiple payments to same invoice:

- Track all payments in `payments` table
- Sum all successful payments
- Update outstanding balance accordingly

---

## Phase 7: Configuration

### 7.1 Environment Variables

**File**: `.env.example`

Add:

```bash
# C2B Confirmation URL
C2B_CONFIRMATION_URL=https://api.invoiceiq.org/mpesa/c2b/confirmation
```

### 7.2 Settings

**File**: `src/app/config.py`

Add:

```python
c2b_confirmation_url: str = Field(
    default="https://api.invoiceiq.org/mpesa/c2b/confirmation",
    env="C2B_CONFIRMATION_URL"
)
```

---

## Phase 8: Testing Checklist

### 8.1 Unit Tests

- C2B URL registration (success/failure)
- Payment matching logic
- Outstanding balance calculation
- WhatsApp notification formatting

### 8.2 Integration Tests (Sandbox)

1. Create invoice with C2B notifications enabled
2. Verify C2B URL registration call to Daraja
3. Simulate C2B payment using Daraja simulate endpoint
4. Verify confirmation callback received
5. Verify invoice matched correctly
6. Verify payment record created
7. Verify WhatsApp notification sent
8. Test partial payment scenario
9. Test full payment scenario

### 8.3 Edge Case Tests

- Registration failure handling
- Unmatched payment handling
- Multiple payments to same invoice
- Concurrent payments to different invoices

---

## Implementation Order

1. **Phase 1**: Database schema (prerequisite for all)
2. **Phase 2**: WhatsApp flow updates (user-facing)
3. **Phase 3**: M-PESA C2B registration (backend integration)
4. **Phase 4**: C2B confirmation endpoint (callback handling)
5. **Phase 5**: WhatsApp notifications (user notification)
6. **Phase 6**: Error handling (robustness)
7. **Phase 7**: Configuration (deployment)
8. **Phase 8**: Testing (validation)

---

## Deployment Steps

1. Run database migrations (add column + create table)
2. Deploy code changes
3. Update environment variables
4. Test with sandbox credentials
5. Verify C2B registration works
6. Test full flow end-to-end
7. Monitor logs for any issues

---

## Success Criteria

- [ ] Vendors can opt-in to C2B notifications during invoice creation
- [ ] Paybill/Till numbers are successfully registered with Daraja
- [ ] C2B confirmation callbacks are received and processed
- [ ] Payments are correctly matched to invoices
- [ ] Outstanding balance is calculated accurately
- [ ] WhatsApp notifications are sent promptly
- [ ] Partial payments are handled correctly
- [ ] Error cases are logged and handled gracefully
