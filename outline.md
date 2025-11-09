# InvoiceIQ MVP — WhatsApp-First Invoicing via STK Push (with SMS fallback)

> **Goal:** Ship a minimal, reliable MVP that lets a merchant create and send an invoice via WhatsApp (or SMS fallback) and get paid via M-PESA STK Push. Nothing else.

---

## 0) Scope Guardrails

**In-scope (MVP):**

- WhatsApp bot (Cloud API) for merchants to create/send invoices.
- Customer receives invoice on WhatsApp (or SMS fallback).
- “Pay with M-PESA” button → STK Push → payment callback → receipt.
- Basic persistence: invoices, payments, message logs.
- One admin/merchant user (single-tenant) with environment-guarded access.

---

## 1) Success Criteria (Ship/No-Ship)

- ✅ Merchant can create invoice with **one line** or step-by-step prompts.
- ✅ Customer receives invoice with **1-tap Pay** (STK).
- ✅ Payment callback marks invoice **PAID** and sends receipt to both parties.
- ✅ SMS fallback works if customer isn’t on WhatsApp.
- ✅ Logs exist for **every** message and **every** payment event.
- ✅ Basic rate limiting + idempotency (no duplicate STK).
- ✅ Deployed on a single server (or serverless) with environment config.

---

## 2) Primary Personas

- **Merchant (MVP: admin):** Initiates invoices via WhatsApp; sees bot confirmations.
- **Customer:** Receives invoice and pays via M-PESA.

---

## 3) Core User Flows

### 3.1 Merchant → Create Invoice (one-line)

**System:**

1. Parse command → validate phone/amount.
2. Create `invoice` with status `PENDING`.
3. Send customer the invoice message (WA preferred; fallback SMS).
4. Reply to merchant with a summary + buttons: `Send reminder`, `Cancel`.

### 3.2 Merchant → Step-by-step

If input is incomplete:

- Ask: “Customer phone?” → validate.
- Ask: “Amount (KES)?” → validate.
- Ask: “Description?” → create + send like above.

### 3.3 Customer → Pay

- Customer taps **Pay with M-PESA** (WA button) or link.
- Backend triggers STK Push (`msisdn`, `amount`, `invoice_id`).
- On callback `SUCCESS`: mark invoice `PAID`, send receipts.

### 3.4 SMS Fallback

- If WhatsApp delivery fails or no WA opt-in → send SMS with short link and/or “Reply PAY to confirm” (optional for future).

---

## 4) System Architecture (Minimal)

- **Frontend:** WhatsApp Cloud API (webhook + outbound) with interactive buttons; SMS fallback (Africa’s Talking/Twilio).
- **Backend:** FastAPI
  - Routes: WA webhook, SMS webhook, `POST /invoices`, `POST /payments/stk/initiate`, `POST /payments/stk/callback`.
- **Storage:** Supabase/Postgres (when ready).
- **Payments:** M-PESA STK Push (till/paybill or provider).
- **Observability:** structured logs; message & payment tables.
- **Deployment:** Single container (uvicorn) + reverse proxy (Caddy/Nginx) or serverless.

---

## 5) Data Model (SQL)

```sql
-- invoices
CREATE TABLE invoices (
  id TEXT PRIMARY KEY,
  customer_name TEXT,
  msisdn TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  currency TEXT NOT NULL DEFAULT 'KES',
  description TEXT,
  status TEXT NOT NULL CHECK (status IN ('PENDING','SENT','PAID','CANCELLED','FAILED')),
  pay_ref TEXT,
  pay_link TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- payments
CREATE TABLE payments (
  id TEXT PRIMARY KEY,
  invoice_id TEXT NOT NULL REFERENCES invoices(id),
  method TEXT NOT NULL CHECK (method IN ('MPESA_STK')),
  status TEXT NOT NULL CHECK (status IN ('INITIATED','SUCCESS','FAILED','EXPIRED')),
  mpesa_receipt TEXT,
  amount_cents INTEGER NOT NULL,
  raw_request JSON,
  raw_callback JSON,
  idempotency_key TEXT UNIQUE,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- message log
CREATE TABLE message_log (
  id TEXT PRIMARY KEY,
  invoice_id TEXT REFERENCES invoices(id),
  channel TEXT NOT NULL CHECK (channel IN ('WHATSAPP','SMS')),
  direction TEXT NOT NULL CHECK (direction IN ('IN','OUT')),
  event TEXT,
  payload JSON,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

## 6) API Endpoints (Backend)

### 6.1 WhatsApp Webhook

- GET /whatsapp/webhook → verify token.
- POST /whatsapp/webhook → receive messages & button clicks.

### 6.2 SMS Webhook

- POST /sms/inbound → inbound SMS.
- POST /sms/status → delivery receipts.

### 6.3 Internal

- POST /invoices
- POST /payments/stk/initiate
- POST /payments/stk/callback

## 7) Bot Command Grammar & Prompts

### 7.1 Bot Commands (One-Line)

- invoice <phone_or_name> <amount> <desc...>
- remind <invoice_id>
- cancel <invoice_id>
- help

### 7.2 Bot Validation:

- Phone: normalize to 2547XXXXXXXX.
- Amount: integer >= 1.
- Description: 3–120 chars.

### 7.3 Bot Prompts

> Use this guided flow when the merchant doesn’t use the one-line `invoice <phone_or_name> <amount> <desc...>` command.

#### Entry Points

- Merchant sends: `invoice` **or** `new invoice`
- (Optional) Shortcut buttons in the bot home: **New Invoice**

#### 7.3.1 — Customer Phone (MSISDN)

**Bot:**
`What is the customer's phone number? Use format 2547XXXXXXXX.`
_Buttons:_ `Cancel`

**Accepts:** `2547XXXXXXXX` only (E.164 without `+`)
**On invalid:**
`Invalid phone. Please use format 2547XXXXXXXX.` (retry same step)
**Commands available:** `cancel`, `help`

#### 7.3.2 — Customer Name (Optional, but recommended)

**Bot:**
`Customer name? (Optional) Reply with the name or send '-' to skip.`
_Buttons:_ `Skip`, `Cancel`

**Accepts:** 2–60 chars, trims whitespace
**On invalid:**
`Name is too short/long. Please enter 2–60 characters, or send '-' to skip.`
**Commands available:** `skip`, `cancel`, `help`

#### 7.3.3 — Amount (KES)

**Bot:**
`Amount in KES? (Whole number, e.g., 2500)`
_Buttons:_ `Cancel`

**Accepts:** Integer `>= 1`
**On invalid:**
`Invalid amount. Enter a whole number in KES (e.g., 2500).`
**Commands available:** `cancel`, `help`

#### 7.3.4 — Description

**Bot:**
`Short description of the invoice (3–120 chars).`
_Buttons:_ `Cancel`

**Examples:**

- `Cleaning service (Wed 3pm)`
- `Logo design first milestone`

**On invalid:**
`Please enter 3–120 characters.`
**Commands available:** `cancel`, `help`

#### 7.3.5 — Confirm & Send

**Bot (preview):**

```

Review invoice:
• To: 2547XXXXXXXX ({{name or '—'}})
• Amount: KES {{amount}}
• Item: {{description}}

Send to customer now?

```

_Buttons:_ `Send`, `Edit Amount`, `Edit Description`, `Cancel`

**Edit actions:** return to the chosen field, then back to Confirm.

**On Send (success path):**

- Create invoice with status `PENDING` → send to customer (WhatsApp preferred; SMS fallback).
- Mark status `SENT`.
  **Bot (to merchant):**
  `Invoice #{{id}} sent to 2547XXXXXXXX. Use 'remind {{id}}' to nudge or 'cancel {{id}}' to cancel.`

#### 7.3.6 Customer Message (Outbound Template / Session)

**To customer:**

```

InvoiceIQ — Invoice #{{id}}
Amount: KES {{amount}}
For: {{name or 'Customer'}}
Item: {{description}}

```

_Buttons:_ `Pay with M-PESA` (reply id: `pay_now`), `View details`

#### 7.3.7 Global Commands During Flow

- `cancel` → aborts flow: `Okay, cancelled. No invoice was created.`
- `help` → `You can create an invoice step-by-step. At any time, send 'cancel' to stop.`
- `skip` (only at Name step) → proceeds with no name.

#### 7.3.8 Validation Rules (Applied Inline)

- **Phone:** must match `^2547\d{8}$`
- **Amount:** integer `>= 1`
- **Description:** length `3–120`
- **Name (optional):** length `2–60` (or `-` / `skip`)

#### 7.3.9 Error & Retry Copy (Short)

- Phone: `Invalid phone. Use 2547XXXXXXXX.`
- Amount: `Invalid amount. Use a whole number (e.g., 2500).`
- Description: `3–120 characters, please.`
- Name: `2–60 characters or '-' to skip.`

#### 7.3.10 Timeouts (UX)

- If no reply for **5 minutes** during flow:
  **Bot:** `Still there? Reply to continue or send 'cancel' to stop.`
- If no reply for **15 minutes**:
  **Bot:** `Session closed. Send 'new invoice' to start again.`

## 8) WhatsApp Templates & Messages

### 8.1 Template: invoice_notification

- Variables: {{name}}, {{amount}}, {{invoice_id}}, {{pay_url}}
- Receipt:

```

Paid ✔ — Invoice #{{invoice_id}} (KES {{amount}})
Receipt: {{mpesa_receipt}}
Thank you!

```

## 9) STK Push Flow

1. Create payments row.
2. Send STK request.
3. On callback: update statuses, send receipts.
4. Handle retries/idempotency.

## 10) State Machine (Chat)

- IDLE → (receive invoice …) → COLLECT
- COLLECT → READY
- READY → SENT
- SENT → PAYMENT_INIT
- PAYMENT_INIT → PAID or FAILED

## 11) Config & Secrets

```

WABA_TOKEN=
WABA_PHONE_ID=
WABA_VERIFY_TOKEN=
SMS_API_KEY=
MPESA_CONSUMER_KEY=
MPESA_CONSUMER_SECRET=
MPESA_SHORTCODE=
MPESA_PASSKEY=
MPESA_CALLBACK_URL=https://<host>/payments/stk/callback
DATABASE_URL=sqlite:///./data.db

```

## 12) Observability & Ops

- Structured logs.
- /healthz, /readyz endpoints.
- Retry failed sends.
- Manual reconcile endpoint (future).

## 13) Security & Compliance

- Validate MSISDNs.
- HMAC verify callbacks or IP whitelist.
- Minimal PII storage.
- Secret rotation.

## 14) Metrics

- m_invoices_created
- m_invoices_sent
- m_invoices_paid
- Conversion = paid/sent
- Time-to-pay = callback - sent

## 15) Test Plan

### 15.1 Tests

- Unit: parser, validators, idempotency.
- Integration: WA inbound → STK → callback → receipt.
- Edge: duplicates, network errors, invalid inputs.

### 15.2 Acceptance:

- One-line invoice creation.
- STK push + callback works.
- SMS fallback works.
- Receipts sent correctly.

## 16) Delivery Plan (7 Days)

- Day 1: Repo scaffold, webhook verify.
- Day 2: Parser + schema.
- Day 3: Create + send invoice.
- Day 4: STK + callback.
- Day 5: SMS fallback.
- Day 6: Logs + metrics.
- Day 7: Pilot & fixes.

## 17) Risks & Mitigations

- Template approval delays → use session messages.
- Callback issues → idempotency + retry.
- Phone errors → strict validation.
- Provider outages → fallback + retry.

## 18) Cut-Line (Drop If Delayed)

- Drop reminders.
- Drop cancel command.
- Drop pay_link (use STK only).
- Keep one WhatsApp style.

## 19) Post-MVP

- Multi-merchant support.
- PDF/email receipts.
- Partial payments.
- Web dashboard.
- Reminders, analytics.

## 20) Definition of Done

- Publicly reachable endpoint.
- WA number active.
- At least 1 live payment end-to-end.
- Written runbook.

### 20.1 Appendices

A) Curl Smoke Tests

```

# Health

curl -sS http://localhost:8000/healthz

# Create invoice

curl -sS -X POST http://localhost:8000/invoices \
-H "Content-Type: application/json" \
-d '{"msisdn":"2547XXXXXXXX","amount_cents":250000,"description":"Cleaning"}'

# STK initiate

curl -sS -X POST http://localhost:8000/payments/stk/initiate \
-H "Idempotency-Key: test-123" \
-H "Content-Type: application/json" \
-d '{"invoice_id":"<id>"}'

```

B) Copy Standards

- All texts ≤ 2 lines.
- Always include invoice # and amount.
- Use “Pay with M-PESA”.
