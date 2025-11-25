# InvoiceIQ

> WhatsApp-first invoicing system with M-PESA payment integration

InvoiceIQ is a minimal MVP that enables merchants to create and send invoices via WhatsApp (with SMS fallback) and receive payments through M-PESA STK Push. Built for the Kenyan market, it provides a seamless invoice-to-payment flow without requiring customers to install any app.

## Features

- **WhatsApp Bot Interface** - Create invoices through conversational commands
- **M-PESA Integration** - Instant payment via M-PESA STK Push
- **SMS Fallback** - Automatic SMS delivery when WhatsApp is unavailable
- **Real-time Status Tracking** - Monitor invoice and payment status
- **Privacy-First Logging** - Metadata-only storage with no PII in logs
- **Business Metrics** - Track conversion rates and payment times

## Tech Stack

- **Backend:** FastAPI (Python 3.11+)
- **Database:** PostgreSQL (via Supabase)
- **Messaging:** WhatsApp Business API (via 360 Dialog), SMS (Africa's Talking)
- **Payments:** M-PESA STK Push (Safaricom Daraja API)
- **Deployment:** Fly.io with automatic HTTPS

## Quick Start

### Prerequisites

- Python 3.11 or higher
- PostgreSQL 15+ (or SQLite for development)
- Docker (optional, for containerized development)

### Local Development Setup

1. **Clone the repository**

   ```bash
   git clone <repository-url> invoiceiq
   cd invoiceiq
   ```

2. **Create virtual environment**

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**

   ```bash
   cp .env.example .env
   # Edit .env and fill in your credentials
   ```

5. **Run database migrations**

   ```bash
   alembic upgrade head
   ```

6. **Start the development server**

   ```bash
   uvicorn src.app.main:app --reload --host 0.0.0.0 --port 8000
   ```

7. **Access the application**
   - API: http://localhost:8000
   - Interactive API docs: http://localhost:8000/docs
   - Health check: http://localhost:8000/healthz

### Docker Development Setup

1. **Build and run with Docker Compose**

   ```bash
   docker-compose up --build
   ```

2. **Access the application**
   - API: http://localhost:8000
   - PostgreSQL: localhost:5432 (local development only)

3. **Stop the containers**
   ```bash
   docker-compose down
   ```

**Note:** Docker Compose is for local development only. Production uses Supabase for managed PostgreSQL.

## Deployment

### Deploy to Fly.io

1. **Install Fly.io CLI**

   ```bash
   curl -L https://fly.io/install.sh | sh
   flyctl auth login
   ```

2. **Set up Supabase database**
   - Create a Supabase account at https://supabase.com
   - Create a new project
   - Get your database connection string from project settings
   - Format: `postgresql+asyncpg://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres?sslmode=require`

3. **Customize app name**
   - Edit `fly.toml` and set your app name

4. **Launch the app**

   ```bash
   flyctl launch
   ```

5. **Configure database secret**

   ```bash
   flyctl secrets set DATABASE_URL="postgresql+asyncpg://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres?sslmode=require"
   ```

6. **Configure other secrets**

   ```bash
   flyctl secrets set \
     WABA_TOKEN="your_token" \
     WABA_PHONE_ID="your_phone_id" \
     WABA_VERIFY_TOKEN="your_verify_token" \
     SMS_API_KEY="your_api_key" \
     MPESA_CONSUMER_KEY="your_key" \
     MPESA_CONSUMER_SECRET="your_secret" \
     MPESA_SHORTCODE="your_shortcode" \
     MPESA_PASSKEY="your_passkey" \
     MPESA_CALLBACK_URL="https://your-app.fly.dev/payments/stk/callback"
   ```

7. **Deploy**
   ```bash
   flyctl deploy
   ```

For detailed deployment instructions, see [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Environment Variables

See `.env.example` for a complete list of required environment variables.

### Required Configuration

| Variable                | Description                        | Example                                     |
| ----------------------- | ---------------------------------- | ------------------------------------------- |
| `WABA_TOKEN`            | WhatsApp Business API access token | `EAABsbCS1iHgBO...`                         |
| `WABA_PHONE_ID`         | WhatsApp phone number ID           | `123456789012345`                           |
| `WABA_VERIFY_TOKEN`     | Webhook verification token         | `your_random_token`                         |
| `SMS_API_KEY`           | SMS provider API key               | `atsk_abc123...`                            |
| `MPESA_CONSUMER_KEY`    | M-PESA consumer key                | `abc123...`                                 |
| `MPESA_CONSUMER_SECRET` | M-PESA consumer secret             | `xyz789...`                                 |
| `MPESA_SHORTCODE`       | M-PESA business shortcode          | `174379`                                    |
| `MPESA_PASSKEY`         | M-PESA Lipa Na M-PESA passkey      | `bfb279f9aa...`                             |
| `MPESA_CALLBACK_URL`    | M-PESA STK callback URL (HTTPS)    | `https://app.fly.dev/payments/stk/callback` |
| `DATABASE_URL`          | Database connection string         | `postgresql+asyncpg://...`                  |

## API Endpoints

### Health Checks

- `GET /healthz` - Liveness check
- `GET /readyz` - Readiness check (verifies database)

### WhatsApp Webhooks

- `GET /whatsapp/webhook` - Webhook verification
- `POST /whatsapp/webhook` - Receive messages and events

### SMS Webhooks

- `POST /sms/inbound` - Receive inbound SMS
- `POST /sms/status` - SMS delivery status callbacks

### Invoices

- `POST /invoices` - Create new invoice

### Payments

- `POST /payments/stk/initiate` - Initiate M-PESA STK Push
- `POST /payments/stk/callback` - M-PESA payment callbacks

### Metrics

- `GET /stats/summary` - Business metrics summary

For interactive API documentation, visit `/docs` when running the application.

## Bot Commands

### One-line Format

```
invoice <phone_or_name> <amount> <description>
remind <invoice_id>
cancel <invoice_id>
help
```

### Guided Flow

Send `invoice` or `new invoice` to start an interactive invoice creation flow.

### Examples

```
invoice John 500 Website design consultation
invoice 254712345678 1200 Monthly subscription
remind INV-12345
cancel INV-67890
```

## Testing

### Run all tests

```bash
pytest
```

### Run with coverage

```bash
pytest --cov=. --cov-report=html
```

### Run specific test file

```bash
pytest tests/test_validators.py
```

### Run integration tests

```bash
pytest tests/integration/
```

## Development Commands

### Linting and Formatting

```bash
# Format code
black .

# Lint code
ruff check .

# Type checking
mypy .
```

### Database Migrations

```bash
# Create new migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback migration
alembic downgrade -1
```

## Project Structure

```
invoiceiq/
├── src/
│   └── app/
│       ├── main.py              # FastAPI application entry point
│       ├── config.py            # Configuration management
│       ├── db.py                # Database connection and models
│       ├── models.py            # SQLAlchemy models
│       ├── schemas.py           # Pydantic schemas
│       ├── routers/             # API route handlers
│       │   ├── whatsapp.py
│       │   ├── sms.py
│       │   ├── invoices.py
│       │   └── payments.py
│       ├── services/            # Business logic
│       │   ├── whatsapp.py
│       │   ├── sms.py
│       │   ├── mpesa.py
│       │   └── idempotency.py
│       └── utils/               # Utility functions
│           ├── logging.py
│           └── phone.py
├── alembic/                     # Database migrations
├── tests/                       # Test suite
├── docs/                        # Documentation
│   ├── RUNBOOK.md              # Deployment and operations guide
│   └── DATA_RETENTION_POLICY.md
├── scripts/                     # Utility scripts
│   ├── run.sh                  # Application startup script
│   └── init_db.sql
├── Dockerfile                   # Docker build configuration
├── docker-compose.yml           # Local development with Docker
├── fly.toml                     # Fly.io deployment config
├── requirements.txt             # Python dependencies
├── alembic.ini                 # Alembic configuration
└── .env.example                # Environment variables template
```

## Architecture

### Core Flow

1. **Merchant** sends WhatsApp message to create invoice
2. **Bot** validates input and creates invoice record
3. **System** sends invoice to customer via WhatsApp/SMS
4. **Customer** receives payment link, clicks "Pay with M-PESA"
5. **M-PESA** sends STK Push to customer's phone
6. **Customer** enters M-PESA PIN to complete payment
7. **System** receives callback, updates invoice status
8. **Both parties** receive payment confirmation

### State Machine

```
IDLE → COLLECT → READY → SENT → PAYMENT_INIT → PAID/FAILED
```

### Data Model

Three core tables:

- **invoices** - Invoice records with customer info and status
- **payments** - Payment transactions with M-PESA details
- **message_log** - Audit trail for all messages (WhatsApp/SMS)

## Security

- **HTTPS Required** - All production endpoints use HTTPS
- **Webhook Validation** - Verify all incoming webhook requests
- **Non-root Container** - Docker runs as non-root user
- **Secret Management** - Secrets stored in Fly.io secrets (encrypted)
- **Privacy-First Logging** - No PII stored in application logs
- **Database Encryption** - Data encrypted at rest and in transit

## Observability

### Structured Logging

All logs are JSON-formatted with:

- Timestamp (ISO 8601)
- Log level (INFO, WARNING, ERROR)
- Correlation ID for request tracing
- Contextual metadata

### Metrics

- Invoice counts by status
- Conversion rate (paid/sent)
- Average payment time
- API response times

### Monitoring

Access logs and metrics:

```bash
# View logs
flyctl logs -a your-app-name

# View metrics
curl https://your-app-name.fly.dev/stats/summary
```

## Roadmap

### Current (MVP)

- ✅ WhatsApp bot for invoice creation
- ✅ M-PESA STK Push integration
- ✅ SMS fallback delivery
- ✅ Privacy-first logging
- ✅ Business metrics tracking

### Future Enhancements

- [ ] Multi-merchant/multi-tenant support
- [ ] Web dashboard for merchants
- [ ] PDF receipt generation
- [ ] Email delivery option
- [ ] Partial payment support
- [ ] Automated payment reminders
- [ ] Advanced analytics and reporting
- [ ] Customer management system

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'feat: add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Commit Convention

This project uses [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(scope): add new feature
fix(scope): fix bug
docs(scope): update documentation
chore(scope): update dependencies
```

## License

[Your License Here - e.g., MIT]

## Support

- **Documentation:** [docs/RUNBOOK.md](docs/RUNBOOK.md)
- **API Docs:** Visit `/docs` endpoint when running
- **Issues:** [GitHub Issues](your-repo-issues-url)

## Acknowledgments

- **WhatsApp Business API** - 360 Dialog (Meta WhatsApp BSP)
- **M-PESA API** - Safaricom PLC
- **SMS Provider** - Africa's Talking
- **Hosting** - Fly.io

---

Built with ❤️ for merchants in Kenya
