# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**InvoiceIQ** is a WhatsApp-first invoicing system that enables merchants to create and send invoices via WhatsApp and receive payments through M-PESA STK Push. This is a minimal MVP focused on core invoice-to-payment flow.

## Tech Stack

- **Backend:** FastAPI (Python)
- **Database:** Supabase/Postgres
- **Messaging:** WhatsApp Business API (via 360 Dialog)
- **Payments:** M-PESA STK Push
- **Deployment:** Single container (uvicorn) + reverse proxy or serverless

## Git

### Commit style

- Use the 'Conventional Commits' convention for all your git commits.
- Ensure that you commit code frequently.
- Make sure that you group tasks which are logically related into a single commit.
- Perform git commits every time a task or set of related tasks are completed.
- **Always ask for approval before making commits.**
- **Don't add attributions to yourself to the commits**

#### Conventional Commits Format

- Use scope in format: `type(scope): description` (e.g., `feat(contact): add email validation`)
- Only include body/footer content if additional explanation is needed to explain the why behind what was done or to make the description understandable
- Breaking changes notation:
  - Option 1: Add `!` after type or scope (e.g., `feat!: remove deprecated login endpoint` or `feat(auth)!: drop support for legacy token system`)
  - Option 2: Include `BREAKING CHANGE:` in commit footer

#### Grouping Changes

- **Commit small related fixes together if:**
  - They are tightly related (e.g., fixing typos in the same function or fixing a bug and its related test)
  - The group of changes forms a logical unit that's easier to understand as a whole
  - Splitting them would add noise rather than clarity
  - Think: "Would someone reviewing or reverting this benefit from it being one commit?"

- **Commit them separately if:**
  - Each fix addresses a different issue, concern, or component
  - You want clear commit history for blame, review, or rollback
  - The changes, while small, stand alone logically
  - Think: "Could I describe each change in a commit message without referencing the others?"

## Development Commands

### Running the Application

```bash
# Install dependencies
pip install -r requirements.txt

# Run development server
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Run with environment variables
DATABASE_URL=sqlite:///./data.db uvicorn main:app --reload
```

### Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=. --cov-report=html

# Run specific test file
pytest tests/test_validators.py

# Run integration tests
pytest tests/integration/
```

### Linting & Formatting

```bash
# Format code
black .

# Lint
ruff check .

# Type checking
mypy .
```

### Database

```bash
# Apply migrations
alembic upgrade head

# Create new migration
alembic revision --autogenerate -m "description"

# Rollback migration
alembic downgrade -1
```

## Repository Tree

./
├─ README.md
├─ .env.example
├─ requirements.txt
├─ scripts/
│ ├─ run.sh
│ └─ init_db.sql
└─ src/
└─ app/
├─ init.py
├─ main.py
├─ config.py
├─ db.py
├─ models.py
├─ schemas.py
├─ utils/
│ ├─ init.py
│ ├─ logging.py
│ └─ phone.py
├─ services/
│ ├─ init.py
│ ├─ whatsapp.py
│ ├─ mpesa.py
│ └─ idempotency.py
└─ routers/
├─ init.py
├─ whatsapp.py
├─ invoices.py
└─ payments.py

## Architecture

### Core Components

1. **WhatsApp Bot Handler** (`/whatsapp/webhook`)
   - Receives messages and button clicks from merchants and customers
   - Implements state machine for guided invoice creation flow
   - Validates inputs (phone, amount, description) inline
   - Supports step-by-step prompts for invoice creation

2. **Invoice Service** (`POST /invoices`)
   - Creates invoices with status tracking (PENDING → SENT → PAID/FAILED/CANCELLED)
   - Sends invoice to customer via WhatsApp
   - Handles merchant confirmations and follow-up actions

3. **Payment Service** (`/payments/stk/*`)
   - Initiates M-PESA STK Push requests
   - Handles payment callbacks with idempotency
   - Updates invoice status and sends receipts to both parties

### State Machine (Chat Flow)

```
IDLE → COLLECT → READY → SENT → PAYMENT_INIT → PAID/FAILED
```

- **IDLE:** Waiting for merchant command
- **COLLECT:** Gathering invoice details step-by-step (phone → name → amount → description)
- **READY:** Preview/confirmation before sending
- **SENT:** Invoice delivered to customer
- **PAYMENT_INIT:** STK Push initiated
- **PAID/FAILED:** Payment completed or failed

### Data Model

Three core tables:

1. **invoices** - Invoice records with status, customer info, amount
2. **payments** - Payment transactions with M-PESA details, idempotency keys
3. **message_log** - Audit trail for all WhatsApp messages (IN/OUT)

Refer to `outline.md` section 5 for complete schema.

### Bot Commands

```
invoice / new invoice - Start guided invoice creation flow
remind <invoice_id> - Send payment reminder
cancel <invoice_id> - Cancel invoice
help - Show available commands
```

**Guided flow:** Triggered by `invoice` or `new invoice`.

### Validation Rules

- **Phone (MSISDN):** Must match `^2547\d{8}$` (E.164 without `+`)
- **Amount:** Integer >= 1 (whole KES)
- **Description:** 3-120 characters
- **Customer Name (optional):** 2-60 characters or `-` to skip

### Environment Configuration

Required secrets (see `outline.md` section 11):

```
D360_API_KEY=            # 360 Dialog API key
WEBHOOK_VERIFY_TOKEN=    # Webhook verification token
MPESA_CONSUMER_KEY=      # M-PESA app consumer key
MPESA_CONSUMER_SECRET=   # M-PESA app consumer secret
MPESA_SHORTCODE=         # M-PESA business shortcode
MPESA_PASSKEY=           # M-PESA passkey
MPESA_CALLBACK_URL=      # STK callback URL (https://<host>/payments/stk/callback)
SUPABASE_URL=            # Supabase project URL
SUPABASE_SECRET_KEY=     # Supabase secret key
```

## API Endpoints

### WhatsApp Webhooks

- `GET /whatsapp/webhook` - Verify webhook token
- `POST /whatsapp/webhook` - Receive messages & button clicks

### Internal APIs

- `POST /invoices` - Create invoice
- `POST /payments/stk/initiate` - Initiate STK Push
- `POST /payments/stk/callback` - M-PESA callback handler

### Health Checks

- `GET /healthz` - Health check
- `GET /readyz` - Readiness check

## Key Implementation Notes

### Idempotency

- All STK Push requests must include `Idempotency-Key` header
- Duplicate requests with same key return cached response
- Prevents duplicate charges from retries

### Error Handling

- Strict MSISDN validation to prevent delivery failures
- Structured logging for all message and payment events
- Retry logic for transient failures

### Security

- Validate all webhook requests (HMAC signatures or IP whitelist)
- Minimal PII storage
- Support for secret rotation
- Single-tenant MVP with environment-guarded merchant access

### Message Copy Standards

- All customer-facing texts ≤ 2 lines
- Always include invoice # and amount in messages
- Use "Pay with M-PESA" for payment CTAs
- Keep bot responses concise and actionable

## Testing Strategy

### Unit Tests

- Command parser logic (help, remind, cancel, guided flow)
- Validator functions (phone, amount, description)
- Idempotency key handling
- State machine transitions

### Integration Tests

- Full flow: WhatsApp inbound → invoice creation → STK initiate → callback → receipt
- Payment callback handling with various statuses

### Edge Cases

- Duplicate STK requests (idempotency)
- Network errors and timeouts
- Invalid inputs at each step
- Concurrent operations on same invoice
- Callback race conditions

## Observability

### Metrics to Track

- `m_invoices_created` - Total invoices created
- `m_invoices_sent` - Total invoices successfully delivered
- `m_invoices_paid` - Total successful payments
- Conversion rate: `paid / sent`
- Time-to-pay: `callback_time - sent_time`

### Logging

- Log all WhatsApp messages to `message_log` table
- Log all payment events with full request/callback payloads
- Use structured logging (JSON) for easy parsing

## MVP Scope Guardrails

**In-scope:**

- WhatsApp bot for invoice creation/delivery
- Customer payment via STK Push
- Basic persistence and audit logging
- Single merchant/admin user

**Out-of-scope (Post-MVP):**

- Multi-merchant/multi-tenant support
- PDF/email receipts
- Partial payments
- Web dashboard
- Analytics and reporting
- Automated reminders

## Definition of Done

Before considering MVP complete:

- Publicly reachable webhook endpoint
- WhatsApp number active and verified
- At least 1 live end-to-end payment test
- Written runbook for deployment and operations
- All acceptance tests passing (see `outline.md` section 15.2)

## CRUCIAL GUIDELINES THAT YOU MUST ADHERE TO

### SUB-AGENT USAGE IS MANDATORY - CONTEXT WINDOW PRESERVATION

**CRITICAL: You MUST use sub-agents whenever their purpose matches your task - even if you have to create a temporary sub-agent to fulfill that task only. This preserves the main context window for the conversation.**

When the user uses `/with-suffix`. The primary reason is **context window preservation** - sub-agents operate in isolated contexts, preventing the main conversation from being cluttered with file contents and search results.

**Why this matters:**

1. Sub-agents don't consume the main context window with file contents
2. They can perform extensive searches without affecting our conversation history
3. They return summarized, relevant information instead of raw file dumps
4. The main conversation stays focused on high-level decisions and coordination

### Other Critical Guidelines

- If you are unsure about anything that the user asks for, you must ask clarifying questions. Never make implicit assumptions about what the user has asked for.
- Upon implementing something that the user has requested do not remove, or change, existing functionality unless it is directly related to the change you are making.
- Use the documentation-retrieval-server mcp to get up-to-date information concerning the python libraries that you use in this project.
- YOU MUST RUN THE CODE YOU WRITE THROUGH THE LINTER WHEN YOU FINISH WRITING IT.
- You MUST use the fly mcp for any of its supported operations when interacting with the Fly.io application associated with this project.
