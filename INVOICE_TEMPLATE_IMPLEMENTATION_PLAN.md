# Invoice Template Implementation Plan

**Date Created:** 2025-11-26
**Status:** Planning Phase
**Objective:** Migrate from interactive messages to Meta-approved WhatsApp invoice template

---

## Table of Contents

1. [Overview](#overview)
2. [Current State vs Target State](#current-state-vs-target-state)
3. [Database Schema Changes](#database-schema-changes)
4. [Invoice Creation Flow Changes](#invoice-creation-flow-changes)
5. [WhatsApp Template Integration](#whatsapp-template-integration)
6. [M-PESA Payment Configuration](#mpesa-payment-configuration)
7. [Implementation Phases](#implementation-phases)
8. [Testing Strategy](#testing-strategy)
9. [Rollback Plan](#rollback-plan)

---

## 1. Overview

### Problem Statement

The current implementation sends **custom interactive messages** for invoices, but Meta has approved a specific **WhatsApp template** (`invoice_alert`) that must be used. The current flow lacks several critical fields required by the template.

### Solution Summary

1. Extend the database schema to capture all template-required fields
2. Update the guided invoice creation flow to collect all necessary information
3. Migrate from interactive messages to WhatsApp template messages
4. Implement line item parsing and calculation logic
5. Add M-PESA payment method selection and storage

---

## 2. Current State vs Target State

### Current State

**Message Type:** Interactive message with reply button
**Fields Sent:**

- Invoice ID
- Amount (total only)
- Description (single line)
- Invoice link
- "Pay with M-PESA" reply button

**Missing:**

- Merchant/Business name
- Line items (itemized breakdown)
- VAT (calculated but not sent)
- Due date
- M-PESA payment details (Paybill/Till/Phone)
- URL button for payment

### Target State

**Message Type:** WhatsApp Template (`invoice_alert`)
**Template Parameters (7 body + 1 button):**

| Param      | Field          | Example                                    |
| ---------- | -------------- | ------------------------------------------ |
| {{1}}      | Invoice ID     | `INV-2025-00045`                           |
| {{2}}      | Invoice From   | `SparkleHome Cleaning Services`            |
| {{3}}      | Invoice For    | Line items formatted                       |
| {{4}}      | VAT            | `KES 880.00`                               |
| {{5}}      | Invoice Total  | `KES 6,380.00`                             |
| {{6}}      | Invoice Due    | `In 7 days (5 Dec 2025)`                   |
| {{7}}      | M-PESA Details | `Paybill: 654321; Account: INV-2025-00045` |
| Button URL | Payment Link   | `https://pay.invoiceiq.org/{invoice_id}`   |

---

## 3. Database Schema Changes

### 3.1 New Fields for `invoices` Table

```sql
-- Merchant Information
ALTER TABLE invoices ADD COLUMN merchant_name TEXT;

-- Line Items (stored as JSON)
ALTER TABLE invoices ADD COLUMN line_items JSONB;
-- Structure: [{"name": "Item", "unit_price_cents": 150000, "quantity": 3}, ...]

-- Due Date
ALTER TABLE invoices ADD COLUMN due_date TEXT;
-- Examples: "Due on receipt", "In 7 days (5 Dec 2025)"

-- M-PESA Payment Method
ALTER TABLE invoices ADD COLUMN mpesa_method TEXT CHECK (mpesa_method IN ('PAYBILL', 'TILL', 'PHONE'));
ALTER TABLE invoices ADD COLUMN mpesa_paybill_number TEXT;
ALTER TABLE invoices ADD COLUMN mpesa_account_number TEXT;
ALTER TABLE invoices ADD COLUMN mpesa_till_number TEXT;
ALTER TABLE invoices ADD COLUMN mpesa_phone_number TEXT;
```

### 3.2 New Table: `merchant_payment_methods`

For storing saved M-PESA payment methods per merchant.

```sql
CREATE TABLE merchant_payment_methods (
  id TEXT PRIMARY KEY,
  merchant_msisdn TEXT NOT NULL,
  method_type TEXT NOT NULL CHECK (method_type IN ('PAYBILL', 'TILL', 'PHONE')),
  paybill_number TEXT,
  account_number TEXT,
  till_number TEXT,
  phone_number TEXT,
  is_default BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_merchant_payment_methods_merchant ON merchant_payment_methods(merchant_msisdn);
```

### 3.3 Migration via Supabase

**Method:** Direct SQL execution in Supabase SQL Editor

- Execute ALTER TABLE statements to add new columns with NULL defaults
- Create `merchant_payment_methods` table via CREATE TABLE
- All existing invoices will have NULL values for new fields (handled gracefully in code)

**Migration File (for reference):** `scripts/add_invoice_template_fields.sql`

---

## 4. Invoice Creation Flow Changes

### 4.1 Updated State Machine Flow

**New States:**

```
IDLE
  → COLLECT_MERCHANT_NAME (NEW)
  → COLLECT_LINE_ITEMS (NEW)
  → COLLECT_DUE_DATE (NEW)
  → COLLECT_CUSTOMER_PHONE
  → COLLECT_CUSTOMER_NAME
  → COLLECT_MPESA_METHOD (NEW)
    → COLLECT_PAYBILL_NUMBER (NEW, conditional)
    → COLLECT_ACCOUNT_NUMBER (NEW, conditional)
    → COLLECT_TILL_NUMBER (NEW, conditional)
    → COLLECT_MPESA_PHONE (NEW, conditional)
  → READY (preview with all fields)
  → CONFIRMED
  → SENT
```

### 4.2 Merchant Name Collection

**State:** `COLLECT_MERCHANT_NAME`

**Prompt:**

```
Welcome! Let's create an invoice.

First, what is your business/merchant name?

Example: SparkleHome Cleaning Services
```

**Validation:**

- 2-100 characters
- Store in state data as `merchant_name`

---

### 4.3 Line Items Collection

**State:** `COLLECT_LINE_ITEMS`

**Prompt:** (from `invoice_line_items.txt`)

```
Please enter your line items in the following format:

Item - Unit Price - Quantity

Example:
Full Home Deep Clean - 1500 - 3
Kitchen Deep Clean - 800 - 1
Bathroom Scrub - 600 - 1

Send all items in one message.
```

**Parsing Logic:**

```python
def parse_line_items(message: str) -> List[Dict]:
    """
    Parse line items from merchant input.

    Format: Item - Unit Price - Quantity
    Each line is separated by newline.

    Returns:
        [
            {
                "name": "Full Home Deep Clean",
                "unit_price_cents": 150000,  # 1500 * 100
                "quantity": 3,
                "subtotal_cents": 450000
            },
            ...
        ]
    """
    lines = message.strip().split('\n')
    items = []

    for line in lines:
        parts = [p.strip() for p in line.split('-')]
        if len(parts) != 3:
            raise ValueError(f"Invalid format: {line}")

        name, price, quantity = parts

        # Validate price and quantity
        try:
            unit_price_kes = float(price)
            qty = int(quantity)
        except ValueError:
            raise ValueError(f"Invalid price or quantity in: {line}")

        unit_price_cents = int(unit_price_kes * 100)
        subtotal_cents = unit_price_cents * qty

        items.append({
            "name": name,
            "unit_price_cents": unit_price_cents,
            "quantity": qty,
            "subtotal_cents": subtotal_cents
        })

    return items
```

**Calculations:**

```python
def calculate_invoice_totals(line_items: List[Dict]) -> Dict:
    """
    Calculate subtotal, VAT, and total from line items.

    VAT is 16% of the total amount (inclusive).
    Formula: VAT = (total * 16) / 116
    """
    subtotal_cents = sum(item["subtotal_cents"] for item in line_items)
    vat_cents = int((subtotal_cents * 16) / 116)
    total_cents = subtotal_cents

    return {
        "subtotal_cents": subtotal_cents,
        "vat_cents": vat_cents,
        "total_cents": total_cents,
        "line_items": line_items
    }
```

**Display Format for Preview:**

```
1) Full Home Deep Clean – 1500 × 3 = KES 4,500.00
2) Carpet Wash – 500 × 2 = KES 1,000.00
```

---

### 4.4 Due Date Collection

**State:** `COLLECT_DUE_DATE`

**Prompt:** (from `invoice_due_date.txt`)

```
When is this invoice due?

Reply with one of:
0 = Due on receipt
7 = In 7 days
14 = In 14 days
30 = In 30 days
N = In N days (where N is a number)

Or send a date like: 30/11 or 30/11/2025.
```

**Parsing Logic:**

```python
def parse_due_date(input: str) -> str:
    """
    Parse due date input and return formatted string.

    Examples:
        "0" → "Due on receipt"
        "7" → "In 7 days (5 Dec 2025)"
        "30/11" → "30 Nov 2025"
        "30/11/2025" → "30 Nov 2025"
    """
    from datetime import datetime, timedelta

    input = input.strip()

    # Option 1: "0" = Due on receipt
    if input == "0":
        return "Due on receipt"

    # Option 2: Number of days (e.g., "7", "14", "30")
    if input.isdigit():
        days = int(input)
        due_datetime = datetime.now() + timedelta(days=days)
        formatted_date = due_datetime.strftime("%d %b %Y")
        return f"In {days} days ({formatted_date})"

    # Option 3: Date format "DD/MM" or "DD/MM/YYYY"
    try:
        if "/" in input:
            parts = input.split("/")
            day = int(parts[0])
            month = int(parts[1])
            year = int(parts[2]) if len(parts) == 3 else datetime.now().year

            due_datetime = datetime(year, month, day)
            return due_datetime.strftime("%d %b %Y")
    except (ValueError, IndexError):
        raise ValueError("Invalid date format. Use DD/MM or DD/MM/YYYY")

    raise ValueError("Invalid input. Use 0, a number, or DD/MM format.")
```

**Display Format:**

```
Invoice Due: In 7 days (5 Dec 2025)
```

---

### 4.5 M-PESA Payment Method Collection

**State:** `COLLECT_MPESA_METHOD`

**Prompt:** (from `invoice_mpesa_details.txt`)

```
How would you like the customer to pay via M-PESA?
Reply with:

1 – Paybill
2 – Till Number
3 – Phone Number (Send Money)
```

**Flow Branching:**

#### Option 1: Paybill

**Sub-State:** `COLLECT_PAYBILL_NUMBER`

**Check for saved methods:**

```python
# Query merchant_payment_methods table
saved_paybills = db.table("merchant_payment_methods").select("*").eq(
    "merchant_msisdn", merchant_msisdn
).eq("method_type", "PAYBILL").execute()
```

**Prompt (if saved methods exist):**

```
Select the paybill you want to use (Note: These are example values and should not be hardcoded. The merchant could have saved any kind of valid paybill or account number(s)):

1 - Paybill Number: 654321; Account Number: INV-{invoice_id}
2 - Paybill Number: 789012; Account Number: CUST-{invoice_id}

Or, please enter the paybill number you want to use:

Example: 645781
```

**Prompt (if no saved methods):**

```
Please enter your paybill number:

Example: 645781
```

**Sub-State:** `COLLECT_ACCOUNT_NUMBER`

**Prompt:**

```
Enter the account number the customer should use:

Example: 30891788
```

**Prompt to Save:**

```
Would you like to save this paybill & account number for future invoices?

Reply: yes / no
```

#### Option 2: Till Number

**Sub-State:** `COLLECT_TILL_NUMBER`

**Check for saved methods:**

```python
saved_tills = db.table("merchant_payment_methods").select("*").eq(
    "merchant_msisdn", merchant_msisdn
).eq("method_type", "TILL").execute()
```

**Prompt (if saved methods exist):**

```
Select the till you want to use:

1 - Till Number: 3454467
2 - Till Number: 9876543

Or, please enter the till number you want to use:

Example: 9985601
```

**Prompt (if no saved methods):**

```
Please enter your till number:

Example: 9985601
```

**Prompt to Save:**

```
Would you like to save this till for future invoices?

Reply: yes / no
```

#### Option 3: Phone Number (Send Money)

**Sub-State:** `COLLECT_MPESA_PHONE`

**Check for saved methods:**

```python
saved_phones = db.table("merchant_payment_methods").select("*").eq(
    "merchant_msisdn", merchant_msisdn
).eq("method_type", "PHONE").execute()
```

**Prompt (if saved methods exist):**

```
Select the phone number you want to use:

1 - Phone Number: 254712345678
2 - Phone Number: 254798765432

Or, please enter the phone number you want to use (format: 2547XXXXXXXX):

Example: 254766909811
```

**Prompt (if no saved methods):**

```
Please enter your phone number (format: 2547XXXXXXXX):

Example: 254766909811
```

**Validation:**

- Use `validate_phone_number()` from `src/app/utils/phone.py`

**Prompt to Save:**

```
Would you like to save this phone number for future invoices?

Reply: yes / no
```

---

### 4.6 Invoice Preview (READY State)

**Updated Preview Format:**

```
📄 Invoice Preview

Invoice ID: INV-1732567890-1234
Invoice From: SparkleHome Cleaning Services
Invoice For:
1) Full Home Deep Clean – 1,500.00 × 3 = KES 4,500.00
2) Carpet Wash – 500.00 × 2 = KES 1,000.00

Subtotal: KES 5,500.00
VAT (16%): KES 880.00
Total: KES 6,380.00

Invoice Due: In 7 days (5 Dec 2025)

M-PESA Payment Details:
Paybill: 654321
Account: INV-1732567890-1234

Customer: John Doe (254712345678)

---
Reply 'confirm' to send or 'cancel' to start over.
```

---

## 5. WhatsApp Template Integration

### 5.1 Template Message Payload

**File to Update:** `src/app/services/whatsapp.py`

**Function:** `send_invoice_to_customer()`

**New Signature:**

```python
async def send_invoice_to_customer(
    self,
    invoice_id: str,
    merchant_name: str,  # NEW
    line_items: List[Dict],  # NEW
    vat_cents: int,  # NEW
    total_cents: int,  # Replaces amount_cents
    due_date: str,  # NEW
    mpesa_details: str,  # NEW (formatted string)
    customer_msisdn: str,
    customer_name: Optional[str],
    db_session: Any,
) -> bool:
```

**Template Payload Structure:**

Based on `send_invoice_template.json`:

```python
def build_template_payload(
    customer_msisdn: str,
    invoice_id: str,
    merchant_name: str,
    line_items_formatted: str,
    vat_formatted: str,
    total_formatted: str,
    due_date: str,
    mpesa_details: str,
) -> Dict:
    """
    Build WhatsApp template message payload for invoice_alert template.
    """
    return {
        "to": customer_msisdn,
        "messaging_product": "whatsapp",
        "type": "template",
        "template": {
            "name": "invoice_alert",
            "language": {
                "policy": "deterministic",
                "code": "en"
            },
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": invoice_id},  # {{1}}
                        {"type": "text", "text": merchant_name},  # {{2}}
                        {"type": "text", "text": line_items_formatted},  # {{3}}
                        {"type": "text", "text": vat_formatted},  # {{4}}
                        {"type": "text", "text": total_formatted},  # {{5}}
                        {"type": "text", "text": due_date},  # {{6}}
                        {"type": "text", "text": mpesa_details},  # {{7}}
                    ]
                },
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": "0",
                    "parameters": [
                        {
                            "type": "text",
                            "text": invoice_id  # URL suffix
                        }
                    ]
                }
            ]
        }
    }
```

### 5.2 Formatting Helpers

```python
def format_line_items_for_template(line_items: List[Dict]) -> str:
    """
    Format line items for WhatsApp template.

    Output example:
    "Full Home Deep Cleaning – KES 1,500.00 (x3)"
    """
    if len(line_items) == 1:
        item = line_items[0]
        unit_price_kes = item["unit_price_cents"] / 100
        return f"{item['name']} – KES {unit_price_kes:,.2f} (x{item['quantity']})"

    # Multiple items: show first item + count
    first_item = line_items[0]
    unit_price_kes = first_item["unit_price_cents"] / 100
    remaining = len(line_items) - 1

    return f"{first_item['name']} – KES {unit_price_kes:,.2f} (x{first_item['quantity']}) +{remaining} more"


def format_mpesa_details(
    method: str,
    paybill_number: Optional[str] = None,
    account_number: Optional[str] = None,
    till_number: Optional[str] = None,
    phone_number: Optional[str] = None
) -> str:
    """
    Format M-PESA payment details for template.

    Examples:
        "Paybill: 654321; Account: INV-2025-00045"
        "Till Number: 3454467"
        "Phone Number: 254712345678"
    """
    if method == "PAYBILL":
        return f"Paybill: {paybill_number}; Account: {account_number}"
    elif method == "TILL":
        return f"Till Number: {till_number}"
    elif method == "PHONE":
        return f"Phone Number: {phone_number}"
    else:
        return "M-PESA details not configured"
```

### 5.3 API Endpoint

**360Dialog Template Endpoint:**

```
POST https://waba-v2.360dialog.io/messages
```

**Headers:**

```
D360-API-KEY: {api_key}
Content-Type: application/json
```

---

## 6. M-PESA Payment Configuration

### 6.1 Environment Variables

**Add to `.env` and `.env.example`:**

```bash
# M-PESA Payment Configuration (optional - can be per-invoice)
# DEFAULT_MPESA_METHOD=PAYBILL  # Options: PAYBILL, TILL, PHONE
# DEFAULT_MPESA_PAYBILL_NUMBER=654321
# DEFAULT_MPESA_ACCOUNT_PREFIX=INV-  # Will be appended with invoice_id
```

### 6.2 Saving Payment Methods

**Function:** `save_payment_method()`

```python
async def save_payment_method(
    merchant_msisdn: str,
    method_type: str,
    paybill_number: Optional[str] = None,
    account_number: Optional[str] = None,
    till_number: Optional[str] = None,
    phone_number: Optional[str] = None,
    db_session: Any = None
) -> Dict:
    """
    Save a merchant's M-PESA payment method for future use.
    """
    method_id = str(uuid4())

    method_data = {
        "id": method_id,
        "merchant_msisdn": merchant_msisdn,
        "method_type": method_type,
        "paybill_number": paybill_number,
        "account_number": account_number,
        "till_number": till_number,
        "phone_number": phone_number,
        "is_default": False,  # Can be updated later
    }

    response = db_session.table("merchant_payment_methods").insert(method_data).execute()
    return response.data[0]
```

---

## 7. Implementation Phases

### Phase 1: Database Schema Updates ⏱️ 1 hour ✅

**Tasks:**

1. Create SQL migration script for new columns
2. Create `merchant_payment_methods` table
3. Execute SQL in Supabase SQL Editor
4. Verify schema changes

**Files to Create/Modify:**

- `scripts/add_invoice_template_fields.sql` (NEW - migration SQL)
- `scripts/init_db.sql` (update for reference)

**Acceptance Criteria:**

- All new columns exist in `invoices` table in Supabase
- `merchant_payment_methods` table created in Supabase
- SQL executes without errors
- Schema visible in Supabase Table Editor

---

### Phase 2: Line Items Parsing & Calculation ⏱️ 4 hours ✅

**Tasks:**

1. Implement `parse_line_items()` function (Note: It must be able to parse decimal numbers to two decimal places)
2. Implement `calculate_invoice_totals()` function
3. Implement `format_line_items_for_template()` function
4. Implement `format_line_items_for_preview()` function
5. Write unit tests for all parsing/calculation logic

**Files to Create/Modify:**

- `src/app/utils/invoice.py` (NEW - utility functions)
- `tests/test_invoice_utils.py` (NEW - unit tests)

**Acceptance Criteria:**

- Correctly parses multi-line item input
- Handles edge cases (invalid format, negative prices, etc.)
- Calculations match expected totals
- All unit tests pass

---

### Phase 3: Due Date Parsing ⏱️ 2 hours ✅

**Tasks:**

1. Implement `parse_due_date()` function
2. Handle all input formats (0, N, DD/MM, DD/MM/YYYY)
3. Write unit tests

**Files to Create/Modify:**

- `src/app/utils/invoice.py` (add function)
- `tests/test_invoice_utils.py` (add tests)

**Acceptance Criteria:**

- Correctly parses all due date formats
- Returns properly formatted strings
- Handles invalid input gracefully
- All unit tests pass

---

### Phase 4: M-PESA Payment Method Storage ⏱️ 4 hours ✅

**Tasks:**

1. Implement `save_payment_method()` function
2. Implement `get_saved_payment_methods()` function
3. Implement `format_mpesa_details()` function
4. Add database queries for saved methods
5. Write unit tests

**Files to Create/Modify:**

- `src/app/services/payment_methods.py` (NEW)
- `tests/test_payment_methods.py` (NEW)

**Acceptance Criteria:**

- Can save payment methods to database
- Can retrieve saved methods by merchant
- Formatting matches template requirements
- All unit tests pass

---

### Phase 5: State Machine Updates ⏱️ 8 hours ✅

**Tasks:**

1. Add new states to `ConversationStateManager`
2. Implement merchant name collection
3. Implement line items collection
4. Implement due date collection
5. Implement M-PESA method selection and collection
6. Update preview format
7. Update confirmation logic
8. Write integration tests for full flow

**Files to Modify:**

- `src/app/services/whatsapp.py`
  - Add new state constants
  - Update `handle_guided_flow()`
  - Add state handlers for each new state
- `tests/integration/test_guided_flow.py` (NEW)

**Acceptance Criteria:**

- All new states transition correctly
- Validation works for each input
- Preview shows all collected information
- Can save payment methods when requested
- Integration tests pass

---

### Phase 6: WhatsApp Template Integration ⏱️ 6 hours ✅

**Tasks:**

1. Update `send_invoice_to_customer()` signature
2. Implement template payload builder
3. Update API call to use template endpoint
4. Update all callers to pass new parameters
5. Add logging for template sends
6. Write integration tests with mocked 360Dialog API

**Files to Modify:**

- `src/app/services/whatsapp.py`
  - `send_invoice_to_customer()` function
  - `build_template_payload()` helper (NEW)
- `src/app/routers/whatsapp.py`
  - Update invoice creation to pass new params
- `tests/integration/test_template_sending.py` (NEW)

**Acceptance Criteria:**

- Template payload matches required format
- All 7 body parameters populated correctly
- Button URL parameter includes invoice ID
- API call succeeds with mocked endpoint
- Integration tests pass

---

### Phase 7: Invoice Creation Flow Updates ⏱️ 4 hours ✅

**Tasks:**

1. Update invoice creation in `whatsapp.py` router
2. Store all new fields in database
3. Update invoice preview endpoint to show new fields
4. Update merchant confirmation message

**Files to Modify:**

- `src/app/routers/whatsapp.py`
  - One-line command handler
  - Guided flow completion handler
- `src/app/routers/invoice_view.py`
  - Update HTML template to show line items

**Acceptance Criteria:**

- All new fields saved to database
- One-line command flow still works (with defaults)
- Guided flow collects all fields
- Invoice view page displays correctly

---

### Phase 8: Testing & Bug Fixes ⏱️ 6 hours

**Tasks:**

1. Run full test suite
2. Fix any failing tests
3. Test end-to-end flow manually
4. Test with real WhatsApp account
5. Verify template sends correctly
6. Document any issues found

**Acceptance Criteria:**

- All unit tests pass
- All integration tests pass
- Manual testing completes successfully
- Template message received and displays correctly
- Payment flow still works

---

### Phase 9: Documentation Updates ⏱️ 2 hours

**Tasks:**

1. Update README with new flow
2. Update PLAN.md to mark completed items
3. Add usage examples for new features
4. Document M-PESA payment method management

**Files to Modify:**

- `README.md`
- `PLAN.md`
- `docs/RUNBOOK.md`

**Acceptance Criteria:**

- Documentation is clear and complete
- Examples are accurate
- PLAN.md reflects current state

---

## 8. Testing Strategy

### 8.1 Unit Tests

**Coverage Areas:**

- Line item parsing (valid and invalid inputs)
- Due date parsing (all formats)
- Total calculations (subtotal, VAT, total)
- M-PESA details formatting
- Payment method storage and retrieval

**Test Files:**

- `tests/test_invoice_utils.py`
- `tests/test_payment_methods.py`
- `tests/test_whatsapp_template.py`

### 8.2 Integration Tests

**Coverage Areas:**

- Full guided invoice creation flow
- State transitions for all new states
- Template payload generation
- Database operations (save/retrieve payment methods)
- End-to-end flow from command to template send

**Test Files:**

- `tests/integration/test_guided_flow_extended.py`
- `tests/integration/test_template_integration.py`
- `tests/integration/test_payment_method_management.py`

### 8.3 Manual Testing Checklist

- [ ] Merchant name collection works
- [ ] Line items parsing works with various formats
- [ ] Line items preview displays correctly
- [ ] Due date collection works for all formats
- [ ] M-PESA method selection works
- [ ] Saved payment methods display and select correctly
- [ ] New payment method save prompt works
- [ ] Invoice preview shows all new fields
- [ ] Confirmation sends WhatsApp template
- [ ] Template received on customer WhatsApp
- [ ] Payment URL button works
- [ ] Invoice view page displays correctly
- [ ] Payment flow still works after template send

---

## 9. Rollback Plan

### If Issues Occur During Deployment

**Option 1: Database Rollback (Manual)**

```sql
-- Execute in Supabase SQL Editor
ALTER TABLE invoices DROP COLUMN merchant_name;
ALTER TABLE invoices DROP COLUMN line_items;
ALTER TABLE invoices DROP COLUMN due_date;
ALTER TABLE invoices DROP COLUMN mpesa_method;
ALTER TABLE invoices DROP COLUMN mpesa_paybill_number;
ALTER TABLE invoices DROP COLUMN mpesa_account_number;
ALTER TABLE invoices DROP COLUMN mpesa_till_number;
ALTER TABLE invoices DROP COLUMN mpesa_phone_number;
DROP TABLE merchant_payment_methods;
```

**Option 2: Feature Flag**

Add environment variable:

```bash
USE_WHATSAPP_TEMPLATE=false  # Use old interactive messages
```

Update code to check flag:

```python
if settings.use_whatsapp_template:
    # Use new template flow
    return await send_invoice_template(...)
else:
    # Use old interactive message flow
    return await send_invoice_interactive(...)
```

**Option 3: Gradual Rollout**

- Deploy to staging first
- Test thoroughly
- Deploy to production
- Monitor logs for errors
- Keep old code path available for 1 week

---

## 10. File Structure Summary

### New Files to Create

```
src/app/utils/invoice.py                    # Line items & due date parsing
src/app/services/payment_methods.py          # Payment method management
tests/test_invoice_utils.py                  # Unit tests for invoice utils
tests/test_payment_methods.py                # Unit tests for payment methods
tests/integration/test_guided_flow_extended.py  # Extended flow tests
tests/integration/test_template_integration.py  # Template sending tests
scripts/add_invoice_template_fields.sql      # Supabase migration SQL
```

### Files to Modify

```
src/app/services/whatsapp.py                 # State machine + template sending
src/app/routers/whatsapp.py                  # Invoice creation handlers
src/app/routers/invoice_view.py              # View template updates
scripts/init_db.sql                          # Schema reference
.env.example                                 # New env vars
README.md                                    # Documentation
PLAN.md                                      # Progress tracking
```

---

## 11. Estimated Timeline

| Phase                         | Duration | Dependencies    |
| ----------------------------- | -------- | --------------- |
| Phase 1: Database Schema      | 2 hours  | None            |
| Phase 2: Line Items           | 4 hours  | Phase 1         |
| Phase 3: Due Date             | 2 hours  | None (parallel) |
| Phase 4: Payment Methods      | 4 hours  | Phase 1         |
| Phase 5: State Machine        | 8 hours  | Phases 2, 3, 4  |
| Phase 6: Template Integration | 6 hours  | Phase 5         |
| Phase 7: Flow Updates         | 4 hours  | Phase 6         |
| Phase 8: Testing              | 6 hours  | Phase 7         |
| Phase 9: Documentation        | 2 hours  | Phase 8         |

**Total Estimated Time:** 38 hours (~5 working days)

---

## 12. Success Criteria

The implementation will be considered successful when:

1. ✅ Database schema includes all new fields
2. ✅ Guided flow collects all template-required information
3. ✅ Line items are parsed and calculated correctly
4. ✅ Due dates are parsed and formatted correctly
5. ✅ M-PESA payment methods can be saved and retrieved
6. ✅ WhatsApp template message is sent instead of interactive message
7. ✅ Template includes all 7 body parameters + URL button
8. ✅ Payment flow still works end-to-end
9. ✅ All unit tests pass
10. ✅ All integration tests pass
11. ✅ Manual testing confirms correct behavior
12. ✅ Documentation is updated

---

## 13. Notes & Considerations

### WhatsApp Template Approval

- Template `invoice_alert` is already approved by Meta
- Any changes to template text require re-approval
- Template name and structure must match exactly

### Backward Compatibility

- Existing invoices in database will have NULL values for new fields
- Need to handle NULL gracefully in views
- Consider default values for missing data

### Performance

- Line item parsing should be fast (< 100ms)
- Payment method queries should use indexes
- Template API calls may be slower than interactive messages

### Security

- Validate all user inputs (line items, due dates, payment details)
- Sanitize inputs before storing in database
- Don't expose merchant payment methods to other users

### Future Enhancements

- Edit existing payment methods
- Set default payment method
- Multiple currency support
- Itemized VAT per line item
- Discount codes
- Recurring invoices

---

**End of Implementation Plan**
