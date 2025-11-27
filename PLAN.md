# Implementation Plan: InvoiceIQ MVP - WhatsApp-First Invoicing System

## Architecture Decision: Single-Tenant MVP

**IMPORTANT:** This MVP is designed as a **single-tenant system** (one merchant per deployment instance). This means:

- One merchant's WhatsApp Business credentials (WABA_PHONE_ID, WABA_TOKEN) are configured via environment variables
- The entire application serves a single merchant/business owner
- All invoices created belong to this one merchant
- This approach allows rapid validation of the core business model without multi-tenancy complexity

**Post-MVP Multi-Tenancy Migration:**

Once the business model is proven (typically after 3+ paying merchants), consider refactoring to multi-tenant architecture:

1. **Quick Multi-Tenant Approach**: Use a shared WhatsApp number with merchant identification in messages
2. **Full Multi-Tenant Approach**: Add merchant registration, database-stored credentials, merchant authentication
3. **Deployment Options**:
   - Per-merchant deployments (simplest scaling path)
   - True multi-tenant SaaS platform (requires significant refactoring)

See Phase 16 for detailed multi-tenancy migration strategy and database schema changes needed.

---

## Phase 1: Project Foundation & Environment Setup (Day 1)

- [x] Create project directory structure (src/app/ with all subdirectories: utils/, services/, routers/)
- [x] Initialize Python virtual environment and install core dependencies (FastAPI, uvicorn, SQLAlchemy, Pydantic, httpx, python-dotenv)
- [x] Create requirements.txt with all dependencies including dev tools (pytest, black, ruff, mypy, pytest-cov)
- [x] Set up .env.example with all required environment variables (WABA*TOKEN, WABA_PHONE_ID, WABA_VERIFY_TOKEN, SMS_API_KEY, MPESA*\*, DATABASE_URL)
- [x] Create .gitignore file (if not exists, add .env, **pycache**, \*.pyc, .pytest_cache, htmlcov/, .venv/)
- [x] Initialize src/app/**init**.py as empty module marker
- [x] Create src/app/config.py with Pydantic BaseSettings for environment variable validation and loading
- [x] Create src/app/utils/**init**.py and src/app/utils/logging.py with structured JSON logging setup
- [x] Create src/app/utils/phone.py with MSISDN validation function (regex: ^2547\d{8}$) and normalization logic
- [x] Write unit tests for phone validation in tests/test_validators.py using pytest
- [x] Run tests to verify phone validation logic works correctly

**Sub-agent Usage:** Use **toby** to fetch latest FastAPI, SQLAlchemy, and Pydantic documentation for best practices.

**Testing Checkpoint:** Phone validation tests pass; config loads environment variables correctly.

---

## Phase 2: Database Layer & Models (Day 1-2)

**Note:** SQLAlchemy is the ORM layer that abstracts database operations. For local development, use SQLite or local PostgreSQL. The same SQLAlchemy code will work with Supabase Postgres in production (Phase 15) by simply changing the DATABASE_URL environment variable. SQLAlchemy provides database portability - no code changes needed when switching database providers.

- [x] Create src/app/db.py with SQLAlchemy engine, SessionLocal, and Base declarative class setup
- [x] Add database connection helper functions (get_db dependency for FastAPI)
- [x] Create src/app/models.py with Invoice model (id, customer_name, msisdn, amount_cents, currency, description, status, pay_ref, pay_link, timestamps)
- [x] Add Payment model to models.py (id, invoice_id, method, status, mpesa_receipt, amount_cents, raw_request, raw_callback, idempotency_key, timestamps)
- [x] Add MessageLog model to models.py (id, invoice_id, channel, direction, event, payload, timestamp)
- [x] Add CHECK constraints for status enums in models (Invoice.status: PENDING/SENT/PAID/CANCELLED/FAILED, Payment.status: INITIATED/SUCCESS/FAILED/EXPIRED)
- [x] Create scripts/init_db.sql with raw SQL schema for reference and manual initialization if needed
- [x] Create database initialization script in src/app/db.py (create_tables function using Base.metadata.create_all)
- [x] Add Alembic for migrations (alembic init alembic, configure env.py with models import)
- [x] Create initial migration with Alembic (alembic revision --autogenerate -m "Initial schema")
- [x] Write unit tests for model instantiation and validation in tests/test_models.py
- [x] Test database connection and table creation locally with SQLite

**Sub-agent Usage:** Use **toby** to get SQLAlchemy 2.0+ best practices and Alembic migration patterns.

**Testing Checkpoint:** Database tables created successfully; models instantiate correctly; Alembic migrations work.

---

## Phase 3: Pydantic Schemas & Validators (Day 2)

- [x] Create src/app/schemas.py with InvoiceCreate schema (msisdn, customer_name optional, amount_cents, description)
- [x] Add InvoiceResponse schema with all invoice fields including id, status, timestamps
- [x] Add PaymentCreate schema (invoice_id, idempotency_key)
- [x] Add PaymentResponse schema with all payment fields
- [x] Add WhatsAppWebhookEvent schema for inbound message parsing
- [x] Add custom Pydantic validators for MSISDN format (uses phone.py validation)
- [x] Add validator for amount_cents (must be >= 100, i.e., 1 KES minimum)
- [x] Add validator for description length (3-120 characters)
- [x] Add validator for customer_name length (2-60 characters when provided)
- [x] Write unit tests for all Pydantic validators in tests/test_schemas.py
- [x] Test edge cases: empty strings, boundary values, invalid formats

**Sub-agent Usage:** Use **jephthah** to generate comprehensive unit tests for schema validators.

**Testing Checkpoint:** All schema validators work correctly; edge cases handled; tests pass.

---

## Phase 4: WhatsApp Webhook Verification & Basic Routing (Day 1-2)

- [x] Create src/app/main.py with FastAPI app initialization and CORS middleware
- [x] Add health check endpoints (GET /healthz returns 200 OK, GET /readyz checks database connection)
- [x] Create src/app/routers/**init**.py as empty module marker
- [x] Create src/app/routers/whatsapp.py with APIRouter setup
- [x] Implement GET /whatsapp/webhook for webhook verification (validate hub.mode, hub.verify_token, return hub.challenge)
- [x] Implement POST /whatsapp/webhook stub that logs incoming payload and returns 200 OK
- [x] Register WhatsApp router in main.py with prefix /whatsapp
- [x] Add request logging middleware to log all incoming webhook requests to message_log table
- [x] Write integration test for webhook verification in tests/integration/test_whatsapp_webhook.py
- [x] Test webhook verification locally using curl or httpx test client

**Sub-agent Usage:** Use **toby** to fetch WhatsApp Cloud API webhook verification documentation.

**Testing Checkpoint:** Webhook verification works; POST webhook receives and logs messages; health checks pass.

---

## Phase 5: WhatsApp Message Parser & State Machine (Day 2-3)

- [x] Create src/app/services/**init**.py as empty module marker
- [x] Create src/app/services/whatsapp.py with WhatsAppService class
- [x] Implement parse_incoming_message function to extract text, sender MSISDN, message type from webhook payload
- [x] Create command parser for one-line invoice command (regex: invoice <phone_or_name> <amount> <desc...>)
- [x] Add parser support for other commands (remind <invoice_id>, cancel <invoice_id>, help)
- [x] Create state machine manager (in-memory dict or Redis for production) to track conversation state per user
  - **MVP Note:** Use in-memory dict for MVP. Redis migration is deferred to Phase 15 (production deployment).
- [x] Implement state transitions: IDLE � COLLECT_PHONE � COLLECT_NAME � COLLECT_AMOUNT � COLLECT_DESCRIPTION � READY � SENT
- [x] Add validation at each collection step (phone, name, amount, description) with error messages
- [x] Implement skip/cancel commands during guided flow
- [x] Add send_whatsapp_message function with WhatsApp Cloud API integration (POST to graph.facebook.com)
- [x] Write unit tests for command parser in tests/test_parser.py
- [x] Write unit tests for state machine transitions in tests/test_state_machine.py
- [x] Test guided flow with mock WhatsApp API responses

**Sub-agent Usage:** Use **jephthah** to generate unit tests for parser and state machine. Use **toby** for WhatsApp Cloud API message sending documentation.

**Testing Checkpoint:** Parser correctly identifies commands; state machine transitions work; validators applied at each step.

---

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
- [x] Write integration test for full invoice creation flow in tests/integration/test_invoice_creation.py
- [x] Test with mock WhatsApp API to verify message format and button structure

**Sub-agent Usage:** Used context7 MCP to get WhatsApp Cloud API interactive buttons documentation and message template formats.

**Testing Checkpoint:** Invoice created in database; WhatsApp message sent with buttons; merchant receives confirmation; status updated correctly.

---

## Phase 7: M-PESA STK Push Integration (Day 4)

- [x] Create src/app/services/mpesa.py with MPesaService class
- [x] Implement OAuth token generation for M-PESA API (using MPESA_CONSUMER_KEY and MPESA_CONSUMER_SECRET)
- [x] Add token caching logic with expiration (tokens valid for ~1 hour)
- [x] Implement STK Push initiate function (formats request with msisdn, amount, callback URL, account reference)
- [x] Add password generation for STK request (base64 encode: shortcode + passkey + timestamp)
- [x] Create src/app/routers/payments.py with APIRouter setup
- [x] Implement POST /payments/stk/initiate endpoint (requires invoice_id in body)
- [x] Add idempotency key validation (check if key exists in payments table, return cached response if duplicate)
- [x] Create Payment record with status INITIATED before sending STK request
- [x] Store raw M-PESA request payload in payments.raw_request JSON field
- [x] Handle M-PESA API errors gracefully (log error, update payment status to FAILED)
- [x] Write unit tests for password generation and request formatting in tests/test_mpesa.py
- [x] Write integration test for STK initiate in tests/integration/test_stk_push.py with mock M-PESA API

**Sub-agent Usage:** Use **toby** to fetch M-PESA Daraja API STK Push documentation and best practices.

**Testing Checkpoint:** STK Push request sent successfully; payment record created; idempotency works; errors handled.

---

## Phase 8: M-PESA Callback Handling & Payment Completion (Day 4)

- [x] Create src/app/services/idempotency.py with idempotency key generation and validation functions
- [x] Implement POST /payments/stk/callback endpoint in payments.py router
- [x] Add callback payload parsing (extract ResultCode, MerchantRequestID, CheckoutRequestID, M-PESA receipt)
- [x] Update Payment record with callback data (store in raw_callback, update status based on ResultCode)
- [x] Update Invoice status to PAID if payment SUCCESS, or FAILED if payment failed
- [x] Implement send_receipt_to_customer function in whatsapp.py (formats receipt message with invoice details and M-PESA receipt number)
- [x] Implement send_receipt_to_merchant function (notifies merchant of successful payment)
- [x] Add message_log entries for receipt messages
- [x] Handle callback validation (check signature or IP whitelist if required by M-PESA)
- [x] Add idempotency check for callback (prevent duplicate processing of same callback)
- [x] Write integration test for full payment flow in tests/integration/test_payment_flow.py (invoice creation � STK � callback � receipts)
- [x] Test callback with various ResultCodes (0 for success, non-zero for failures)

**Sub-agent Usage:** Use **toby** to get M-PESA callback payload structure and ResultCode meanings.

**Testing Checkpoint:** Callback processed correctly; invoice and payment statuses updated; receipts sent to both parties; duplicate callbacks ignored.

---

## Phase 9: SMS Fallback Integration (Day 5)

- [x] Create src/app/services/sms.py with SMSService class (choose provider: Africa's Talking or Twilio)
- [x] Implement send_sms function with provider API integration
- [x] Add SMS message formatting for invoice notification (include amount, invoice ID, payment link or shortcode)
- [x] Create src/app/routers/sms.py with APIRouter setup
- [x] Implement POST /sms/inbound endpoint for receiving SMS replies from customers
- [x] Implement POST /sms/status endpoint for delivery receipt callbacks
- [x] Add fallback logic in send_invoice_to_customer: try WhatsApp first, if fails then send SMS
- [x] Update message_log to record SMS channel for fallback messages
- [x] Add basic SMS command parsing for customer replies (e.g., "PAY" keyword for future use)
- [x] Write integration test for SMS fallback in tests/integration/test_sms_fallback.py
- [x] Test with SMS provider sandbox or mock API

**Sub-agent Usage:** Use **toby** to fetch Africa's Talking or Twilio SMS API documentation.

**Testing Checkpoint:** SMS sent successfully when WhatsApp fails; delivery receipts logged; inbound SMS received.

---

## Phase 10: Privacy-First Logging & Audit Trail (Day 6)

**Privacy Philosophy:** Store only operational metadata, NOT customer PII. This minimizes GDPR/data protection risks while maintaining debugging and monitoring capabilities.

- [x] Update all WhatsApp message sends to create message_log entries with metadata only (NO message content)
- [x] Update all SMS sends to create message_log entries with metadata only (NO message content)
- [x] Store only: message_id, status, event_type, timestamp, status_code, error_type
- [x] DO NOT store: message content, phone numbers, customer names, amounts, full API payloads
- [x] Add structured logging for API calls with metadata only (no request/response bodies containing PII)
- [x] Create helper function in logging.py for consistent privacy-compliant log formatting
- [x] Add error logging with stack traces (ensure no customer PII in error logs)
- [x] Implement log correlation IDs (generate UUID per request, include in all related logs)
- [x] Add logging for state machine transitions (log state changes, not message content)
- [x] Log all payment events: initiate, callback received, status updates (amounts are in invoice, no need to duplicate)
- [x] Write queries or scripts to analyze message_log table (delivery rates, channel distribution, performance metrics)
- [x] Document data retention policy (how long logs are kept, auto-deletion strategy)
- [x] Test log output format and ensure metadata-only approach is followed

**Privacy Benefits:**

- GDPR/CCPA compliant - minimal PII storage
- Lower liability in case of data breach
- Simpler compliance requirements
- Still maintains operational visibility for debugging

**Testing Checkpoint:** All messages logged with metadata only; no PII in logs; logs are structured and queryable; correlation IDs present.

---

## Phase 11: Basic Metrics & Observability (Day 6)

- [x] Create src/app/services/metrics.py with database query functions:
  - get_invoice_stats() - Returns total created, sent, paid, failed counts
  - get_conversion_rate() - Returns paid/sent ratio
  - get_average_payment_time() - Returns avg time from SENT to PAID
- [x] Add GET /stats/summary endpoint in main.py that returns JSON with above metrics
- [x] Ensure structured logs capture invoice state transitions and payment completions (already in Phase 10)
- [x] Write tests for metrics calculations in tests/test_metrics.py
- [x] Verify /stats/summary endpoint returns correct data

**Testing Checkpoint:** Stats endpoint accessible; calculations accurate; can monitor business health via simple API call.

---

## Phase 12: End-to-End Integration Testing (Day 6-7)

- [x] Set up pytest fixtures for test database (use SQLite in-memory or separate test DB)
- [x] Create mock services for external APIs (WhatsApp, SMS, M-PESA) using pytest-mock or responses library
- [x] Write end-to-end test: merchant sends one-line invoice command � invoice created � customer receives WhatsApp � customer clicks pay � STK sent � callback � receipts sent
- [x] Write end-to-end test for guided flow: step-by-step invoice creation with all validation steps
- [x] Write test for SMS fallback: WhatsApp fails � SMS sent � delivery receipt logged
- [x] Write test for payment failure: STK callback with non-zero ResultCode � invoice status FAILED � merchant notified
- [x] Write test for idempotency: duplicate STK request with same key � returns cached response, no duplicate charge
- [x] Write test for concurrent requests: multiple invoices created simultaneously � all processed correctly
- [x] Write test for invalid inputs at each step: malformed phone, negative amount, empty description
- [x] Run full test suite with coverage report (pytest --cov=. --cov-report=html)
- [x] Ensure minimum 80% code coverage on core business logic

**Sub-agent Usage:** Use **jephthah** to generate comprehensive integration tests.

**Testing Checkpoint:** All integration tests pass; coverage target met; edge cases handled correctly.

---

## Phase 13: Error Handling & Resilience (Day 5-6)

- [x] Add global exception handler in main.py to catch unhandled exceptions and return 500 with error ID
- [x] Implement retry logic for WhatsApp API calls (3 retries with exponential backoff)
- [x] Implement retry logic for SMS API calls
- [x] Implement retry logic for M-PESA token generation and STK requests
- [x] Add timeout configuration for all external HTTP requests (default 10 seconds)
- [x] Add validation for all webhook signatures (WhatsApp HMAC if available, or IP whitelist)
- [x] Add circuit breaker pattern for M-PESA API (stop sending requests if API is down)
- [x] Create custom exception classes for domain errors (InvoiceNotFound, PaymentFailed, InvalidMSISDN)
- [x] Add user-friendly error messages in bot responses (avoid exposing technical details)
- [x] Write tests for retry logic in tests/test_resilience.py
- [x] Test error scenarios: API timeouts, network errors, invalid responses

**Testing Checkpoint:** Retries work correctly; rate limiting enforced; errors logged and handled gracefully.

---

## Phase 14: Deployment Preparation - Fly.io (Day 7)

**Deployment Target:** Fly.io with automatic HTTPS support

**Important Notes:**

- M-PESA Daraja API requires HTTPS endpoints (ngrok no longer supported)
- Fly.io provides automatic SSL certificates for all deployments
- Testing webhooks requires deployment to Fly.io staging environment

### Fly.io Deployment Tasks:

- [x] Create Dockerfile for Fly.io deployment (Python 3.11+, install dependencies, expose port 8000)
- [x] Create fly.toml configuration file for Fly.io app settings
- [x] Configure Fly.io app with secrets (fly secrets set for all environment variables)
- [x] Create .dockerignore file to exclude unnecessary files from Docker build
- [x] Add docker-compose.yml for local development only (app service and postgres service)
- [x] Create scripts/run.sh for running the application with uvicorn (includes environment variable loading)
- [x] Write deployment runbook in docs/RUNBOOK.md (includes Fly.io setup, secrets management, database migrations, health checks)
- [x] Document webhook URL setup for WhatsApp Business API configuration (https://<app-name>.fly.dev/whatsapp/webhook)
- [x] Document M-PESA callback URL registration process (https://<app-name>.fly.dev/payments/stk/callback)
- [x] Add README.md with project overview, quick start guide, Fly.io deployment instructions
- [x] Create .env.example with all required variables and example values (with placeholders for secrets)
- [x] Test Docker build locally (docker build -t invoiceiq .)
- [x] Deploy to Fly.io staging environment (fly deploy)
- [x] Test database migrations on Fly.io (alembic upgrade head runs automatically on deploy)
- [x] Verify health checks work (https://<app-name>.fly.dev/healthz and /readyz)
- [ ] Register webhook URLs with WhatsApp Business API using Fly.io HTTPS URL
- [ ] Register M-PESA callback URL with Safaricom using Fly.io HTTPS URL
- [ ] Test end-to-end webhook delivery (WhatsApp and M-PESA callbacks to Fly.io app)

**Testing Checkpoint:** App deployed to Fly.io with HTTPS; database migrations work; WhatsApp and M-PESA webhooks receive callbacks successfully.

---

## Phase 15: Production Readiness & Pilot Testing (Day 7)

**Note:** The SQLAlchemy code written in Phase 2 requires NO changes for production. Simply update the DATABASE_URL environment variable to point to Supabase Postgres. SQLAlchemy's abstraction layer handles the connection - this is the benefit of using an ORM.

**State Machine Note:** For MVP, the in-memory state machine is sufficient for single-instance deployment on Fly.io. For future scaling with multiple instances, consider migrating to Redis or database-backed state storage.

- [x] Deploy application to Fly.io production (flyctl deploy)
- [x] Verify state machine works correctly in production (in-memory is acceptable for MVP single instance)
- [x] Configure production database (Supabase Postgres - create project and get connection string)
- [x] Run database migrations in production (alembic upgrade head - runs automatically via release_command)
- [x] Configure production environment variables in deployment platform
- [x] Set up reverse proxy with SSL (Caddy or Nginx) if using VPS deployment
- [x] Register webhook URLs with WhatsApp Business API (verify webhook using production URL)
- [ ] Register M-PESA callback URL with Safaricom or payment provider
- [x] Verify WhatsApp number is active and verified in Meta Business Suite
- [x] Perform smoke tests on production: health check, webhook verification, create test invoice
- [ ] Execute live end-to-end test: create real invoice � send to test customer number � initiate STK � complete payment � verify receipts
- [ ] Monitor logs during pilot test for any errors or warnings
- [ ] Document any issues found and create bug fix tasks
- [ ] Perform final security review: validate all webhooks, check secret exposure, test rate limits
- [ ] Create monitoring alerts for critical errors (payment failures, webhook delivery failures)
- [ ] Mark MVP as DONE if all acceptance criteria met

**Testing Checkpoint:** Live payment successfully completed; all webhooks working; no critical errors in production logs.

---

## Phase 16: Documentation & Handoff (Day 7)

- [ ] Complete RUNBOOK.md with operational procedures (start/stop service, check logs, manual invoice creation)
- [ ] Document troubleshooting guide for common issues (webhook failures, payment stuck, SMS not delivered)
- [ ] Add API documentation using FastAPI's automatic OpenAPI docs (accessible at /docs)
- [ ] Document WhatsApp bot commands and guided flow in user guide
- [ ] Create testing checklist for future releases
- [ ] Document known limitations and post-MVP roadmap items
- [ ] Document multi-tenancy migration strategy in POST_MVP_ROADMAP.md (see below)
- [ ] Add contributing guidelines if planning to open source or collaborate
- [ ] Review all TODO comments in code and create tasks for any remaining items
- [ ] Tag release version in git (v1.0.0-mvp)
- [ ] Create release notes summarizing MVP features and known issues

**Testing Checkpoint:** Documentation complete and accurate; runbook verified with deployment test; API docs accessible.

### Post-MVP: Multi-Tenancy Migration Strategy

**When to Consider Multi-Tenancy:**

- You have 3+ merchants requesting to use the system
- Per-merchant deployment costs become prohibitive
- Merchants request white-label or branded solutions
- Business model validated with paying customers

**Migration Option 1: Per-Merchant Deployments (Recommended First Step)**

Deploy separate instances for each merchant:

- Merchant A: `merchant-a.invoiceiq.com` with their WABA credentials in .env
- Merchant B: `merchant-b.invoiceiq.com` with their WABA credentials in .env
- Easiest scaling path, minimal code changes
- Higher infrastructure cost but isolated failures
- Best for 3-10 merchants

**Migration Option 2: Shared WhatsApp Number (Quick Multi-Tenant)**

Use one WhatsApp number for all merchants:

- Add `merchants` table: `id, business_name, identifier_code, owner_email`
- Add `merchant_id` foreign key to `invoices` table
- Include merchant identifier in messages: "Invoice from [BusinessName]: ..."
- Authenticate merchants via simple API key or JWT
- Pros: Fast implementation, low infrastructure cost
- Cons: Less professional, all messages from one number

**Migration Option 3: Full Multi-Tenant SaaS (Long-term Goal)**

Database Schema Changes:

```sql
-- Add merchants table
CREATE TABLE merchants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_name VARCHAR(100) NOT NULL,
    waba_phone_id VARCHAR(50) NOT NULL,
    waba_token_encrypted TEXT NOT NULL,  -- Encrypt at rest
    waba_verify_token VARCHAR(100),
    owner_email VARCHAR(255) UNIQUE NOT NULL,
    is_active BOOLEAN DEFAULT true,
    subscription_tier VARCHAR(20),  -- FREE, BASIC, PRO
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Add merchant_id to existing tables
ALTER TABLE invoices ADD COLUMN merchant_id UUID REFERENCES merchants(id);
ALTER TABLE payments ADD COLUMN merchant_id UUID REFERENCES merchants(id);
ALTER TABLE message_log ADD COLUMN merchant_id UUID REFERENCES merchants(id);

-- Add merchant users for multi-user access
CREATE TABLE merchant_users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    merchant_id UUID REFERENCES merchants(id),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role VARCHAR(20),  -- OWNER, ADMIN, STAFF
    created_at TIMESTAMP DEFAULT NOW()
);
```

Application Changes Required:

1. **Authentication System**: Add JWT-based auth for merchant users
2. **Merchant Context Middleware**: Extract merchant_id from auth token, inject into requests
3. **Credential Management**:
   - Encrypt WhatsApp tokens at rest
   - Load merchant-specific credentials per request
   - Support credential rotation
4. **Webhook Routing**: Route incoming webhooks to correct merchant based on phone number
5. **Multi-Merchant Admin Panel**:
   - Merchant registration/onboarding
   - Credential management UI
   - Invoice/payment dashboard per merchant
6. **Billing System**: Track usage, implement subscription tiers

Code Refactoring Needed:

```python
# Before (MVP - single tenant)
waba_token = config.WABA_TOKEN

# After (Multi-tenant)
async def get_merchant_credentials(merchant_id: UUID) -> MerchantCredentials:
    merchant = await db.query(Merchant).filter(Merchant.id == merchant_id).first()
    return MerchantCredentials(
        waba_token=decrypt(merchant.waba_token_encrypted),
        waba_phone_id=merchant.waba_phone_id
    )
```

Estimated Development Time: 2-3 weeks for full multi-tenant refactor

**Recommended Path:**

1. Complete MVP and validate with 1 merchant (Phases 1-16)
2. Scale to 2-5 merchants using per-merchant deployments
3. If traction continues, build full multi-tenant SaaS
4. Add admin panel, billing, and subscription management

---

## Dependencies & Notes

**Phase Dependencies:**

- Phase 2 depends on Phase 1 (database requires config and logging)
- Phase 3 depends on Phase 2 (schemas use models)
- Phase 4 can run in parallel with Phase 3
- Phase 5 depends on Phase 4 (webhook handler needs routing)
- Phase 6 depends on Phases 2, 3, 5 (invoice creation needs models, schemas, parser)
- Phase 7 depends on Phase 6 (STK needs invoice context)
- Phase 8 depends on Phase 7 (callback handler needs STK implementation)
- Phase 9 can run in parallel with Phase 7-8
- Phase 10-11 can run in parallel after Phase 8
- Phase 12 requires all feature phases complete (1-11)
- Phase 13 can be integrated throughout development but finalized before Phase 14
- Phase 14 depends on all features complete
- Phase 15 depends on Phase 14
- Phase 16 runs in parallel with Phase 15

**Critical Implementation Notes:**

- Idempotency is CRITICAL for payment handling - must be implemented before STK testing
- MSISDN validation must be strict (E.164 format without +) to prevent delivery failures
- All external API calls must have timeouts and retry logic
- State machine must persist across server restarts for production (use Redis or database, not in-memory)
- WhatsApp interactive buttons require specific JSON format - consult API docs carefully
- M-PESA password generation is time-sensitive - generate fresh for each request
- Message templates may need Meta approval - use session messages for MVP to avoid delays

**Sub-Agent Summary:**

- **toby**: Use for fetching documentation (FastAPI, SQLAlchemy, WhatsApp API, M-PESA Daraja API, SMS providers)
- **jephthah**: Use for generating unit tests (validators, parsers, state machine) and integration tests
- **wanderer**: Use if codebase exploration needed during development (currently N/A for greenfield project)

**Testing Strategy:**

- Unit tests after each service/utility implementation
- Integration tests after each major feature (invoice creation, payment flow, SMS fallback)
- End-to-end tests before deployment
- Live pilot test as final acceptance test

**MVP Success Criteria (Revalidated):**

- [ ] Merchant can create invoice with one-line command OR step-by-step guided flow
- [ ] Customer receives invoice via WhatsApp with "Pay with M-PESA" button
- [ ] Customer can tap button and complete STK Push payment
- [ ] Both merchant and customer receive payment receipts upon successful payment
- [ ] SMS fallback works when WhatsApp delivery fails
- [ ] All messages and payment events logged to database
- [ ] Idempotency prevents duplicate charges
- [ ] System deployed and publicly accessible with valid HTTPS webhooks
- [ ] At least 1 successful live end-to-end payment completed
- [ ] Operational runbook written and tested
