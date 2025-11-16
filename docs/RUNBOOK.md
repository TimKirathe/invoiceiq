# InvoiceIQ Deployment Runbook

This runbook provides comprehensive instructions for deploying and operating InvoiceIQ on Fly.io with HTTPS support.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Initial Setup](#initial-setup)
3. [Database Setup](#database-setup)
4. [Secrets Management](#secrets-management)
5. [Deployment](#deployment)
6. [Database Migrations](#database-migrations)
7. [Health Checks](#health-checks)
8. [Webhook Registration](#webhook-registration)
9. [Monitoring and Logs](#monitoring-and-logs)
10. [Scaling](#scaling)
11. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required Accounts

1. **Fly.io Account**
   - Sign up at https://fly.io/
   - Install flyctl CLI: `curl -L https://fly.io/install.sh | sh`
   - Login: `flyctl auth login`

2. **Meta Business Suite** (for WhatsApp)
   - WhatsApp Business API access
   - Business verification completed

3. **Safaricom Daraja Portal** (for M-PESA)
   - Developer account at https://developer.safaricom.co.ke/
   - App created and credentials obtained

4. **Africa's Talking** (for SMS fallback)
   - Account at https://africastalking.com/
   - API key obtained

### Required Tools

- **flyctl CLI** (Fly.io command-line tool)
- **Docker** (for local testing)
- **Git** (for version control)

### Verify Installation

```bash
# Check flyctl version
flyctl version

# Check Docker version
docker --version

# Login to Fly.io
flyctl auth login
```

---

## Initial Setup

### 1. Customize Application Name

Edit `fly.toml` and change the app name:

```toml
app = "your-app-name-here"
```

**Note:** App names must be globally unique across all Fly.io apps.

### 2. Launch the Application

```bash
# Initialize Fly.io app (run from project root)
flyctl launch

# Follow the prompts:
# - Use existing fly.toml? Yes
# - Copy configuration to new app? Yes
# - Choose region: nbo (Nairobi) or closest to Kenya
# - Would you like to set up a PostgreSQL database? No (we'll do this manually)
# - Would you like to deploy now? No (we need to set secrets first)
```

This creates your app on Fly.io without deploying it yet.

### 3. Verify App Creation

```bash
# List your apps
flyctl apps list

# Check app status
flyctl status -a your-app-name
```

---

## Database Setup

### Supabase PostgreSQL (Recommended for Production)

InvoiceIQ uses Supabase for managed PostgreSQL database with built-in backups, real-time capabilities, and easy scaling.

#### 1. Create Supabase Project

1. **Sign up/Login to Supabase**
   - Visit https://supabase.com
   - Create a new account or sign in

2. **Create New Project**
   - Click "New Project"
   - Choose your organization
   - Enter project name (e.g., "invoiceiq-prod")
   - Choose a strong database password
   - Select region: **Singapore** (closest to Kenya for low latency)
   - Click "Create new project"

3. **Wait for Project Initialization** (takes 1-2 minutes)

#### 2. Get Database Connection String

1. **Navigate to Project Settings**
   - Click the gear icon (Settings) in the sidebar
   - Go to "Database" section

2. **Copy Connection String**
   - Find "Connection String" section
   - Select "URI" tab
   - Copy the connection string (it looks like this):
     ```
     postgresql://postgres:[YOUR-PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres
     ```
   - Replace `[YOUR-PASSWORD]` with your actual database password

3. **Convert to AsyncPG Format**
   - InvoiceIQ uses AsyncPG driver
   - Convert the connection string to:
     ```
     postgresql+asyncpg://postgres:[YOUR-PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres?sslmode=require
     ```
   - Note: Add `+asyncpg` after `postgresql` and `?sslmode=require` at the end

#### 3. Set Database URL in Fly.io

```bash
# Set DATABASE_URL secret with your Supabase connection string
flyctl secrets set DATABASE_URL="postgresql+asyncpg://postgres:[YOUR-PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres?sslmode=require" -a your-app-name
```

#### 4. Verify Database Connection

```bash
# Check that DATABASE_URL secret is set
flyctl secrets list -a your-app-name

# After deployment, test the connection via readiness check
curl https://your-app-name.fly.dev/readyz
# Should return: {"status": "ready", "database": "connected"}
```

### Supabase Benefits

- **Automatic Backups:** Daily backups with point-in-time recovery
- **Connection Pooling:** Built-in connection pooling on port 6543 (optional)
- **SSL Encryption:** Data encrypted in transit and at rest
- **Dashboard Access:** Visual query editor and table browser
- **Real-time Logs:** View database logs in Supabase dashboard
- **Scaling:** Easy vertical scaling from dashboard
- **Extensions:** PostGIS, pg_stat_statements pre-installed

### Supabase Dashboard

Access your database dashboard at:
```
https://supabase.com/dashboard/project/[PROJECT-REF]
```

Features:
- **Table Editor:** View and edit data visually
- **SQL Editor:** Run custom SQL queries
- **Database Logs:** Monitor query performance
- **API Docs:** Auto-generated API documentation

### Connection Pooling (Optional)

For high-traffic scenarios, use Supabase's connection pooler:

```bash
# Use port 6543 for pooled connections (transaction mode)
flyctl secrets set DATABASE_URL="postgresql+asyncpg://postgres:[YOUR-PASSWORD]@db.[PROJECT-REF].supabase.co:6543/postgres?sslmode=require" -a your-app-name
```

**Note:** Connection pooling in transaction mode is recommended for serverless environments with many concurrent connections.

---

## Secrets Management

### Set All Required Secrets

```bash
# WhatsApp Business API
flyctl secrets set \
  WABA_TOKEN="your_whatsapp_token" \
  WABA_PHONE_ID="your_phone_id" \
  WABA_VERIFY_TOKEN="your_verify_token" \
  -a your-app-name

# SMS Provider (Africa's Talking)
flyctl secrets set \
  SMS_API_KEY="your_sms_api_key" \
  SMS_USERNAME="your_at_username" \
  SMS_SENDER_ID="your_sender_id" \
  SMS_USE_SANDBOX="false" \
  -a your-app-name

# M-PESA Configuration
flyctl secrets set \
  MPESA_CONSUMER_KEY="your_consumer_key" \
  MPESA_CONSUMER_SECRET="your_consumer_secret" \
  MPESA_SHORTCODE="your_shortcode" \
  MPESA_PASSKEY="your_passkey" \
  MPESA_CALLBACK_URL="https://your-app-name.fly.dev/payments/stk/callback" \
  MPESA_ENVIRONMENT="production" \
  -a your-app-name
```

### Important Notes

- **DATABASE_URL** is automatically set when you attach Fly Postgres
- **MPESA_CALLBACK_URL** must use your actual Fly.io app URL (HTTPS required)
- Replace `your-app-name` with your actual app name
- Secrets are encrypted at rest and in transit

### List All Secrets

```bash
# List configured secrets (values are hidden)
flyctl secrets list -a your-app-name
```

### Update a Secret

```bash
# Update a single secret
flyctl secrets set SECRET_NAME="new_value" -a your-app-name
```

### Remove a Secret

```bash
# Remove a secret
flyctl secrets unset SECRET_NAME -a your-app-name
```

---

## Deployment

### First Deployment

```bash
# Deploy the application
flyctl deploy -a your-app-name

# Monitor deployment progress
flyctl logs -a your-app-name
```

### Subsequent Deployments

```bash
# Deploy after making changes
flyctl deploy -a your-app-name

# Deploy with specific Dockerfile
flyctl deploy --dockerfile Dockerfile -a your-app-name
```

### Deployment Process

1. **Build Stage:** Docker image is built from Dockerfile
2. **Release Command:** Database migrations run (`alembic upgrade head`)
3. **Deploy Stage:** New machines are created and old ones are replaced
4. **Health Checks:** Application must pass health checks before accepting traffic

### Verify Deployment

```bash
# Check deployment status
flyctl status -a your-app-name

# View deployed machines
flyctl machines list -a your-app-name

# Test health endpoint
curl https://your-app-name.fly.dev/healthz

# Test readiness endpoint
curl https://your-app-name.fly.dev/readyz
```

---

## Database Migrations

### Automatic Migrations

Migrations run automatically on every deployment via the release command in `fly.toml`:

```toml
[deploy]
  release_command = "alembic upgrade head"
```

This ensures your Supabase database schema is always up-to-date with your application code.

### Manual Migration Run

If you need to run migrations manually:

```bash
# SSH into the running machine
flyctl ssh console -a your-app-name

# Inside the container, run:
alembic upgrade head

# Exit the SSH session
exit
```

### Running Migrations Locally Against Supabase

To test migrations against your Supabase database before deploying:

```bash
# Set DATABASE_URL to your Supabase connection string
export DATABASE_URL="postgresql+asyncpg://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres?sslmode=require"

# Run migrations
alembic upgrade head

# Check migration status
alembic current
```

**Important:** Only do this in a staging/test environment, not production!

### Check Migration Status

```bash
# SSH into the machine
flyctl ssh console -a your-app-name

# Check current migration version
alembic current

# View migration history
alembic history

# Exit
exit
```

### Rollback Migration

```bash
# SSH into the machine
flyctl ssh console -a your-app-name

# Rollback one version
alembic downgrade -1

# Rollback to specific version
alembic downgrade <revision_id>

# Exit
exit
```

### View Migrations in Supabase Dashboard

You can also view applied migrations in the Supabase dashboard:

1. Go to your Supabase project dashboard
2. Navigate to "SQL Editor"
3. Run this query to see migration history:
   ```sql
   SELECT * FROM alembic_version;
   ```

---

## Health Checks

### Health Check Endpoints

- **`/healthz`** - Basic liveness check (returns `{"status": "ok"}`)
- **`/readyz`** - Readiness check (verifies database connection)

### Check Health Status

```bash
# Liveness check
curl https://your-app-name.fly.dev/healthz

# Readiness check
curl https://your-app-name.fly.dev/readyz
```

### Health Check Configuration

Health checks are configured in `fly.toml`:

```toml
[[services.http_checks]]
  interval = "30s"
  timeout = "5s"
  grace_period = "10s"
  method = "get"
  path = "/healthz"
  protocol = "http"
```

---

## Webhook Registration

### WhatsApp Webhook Setup

1. **Log in to Meta Business Suite**
   - Navigate to WhatsApp > Configuration > Webhook

2. **Configure Webhook**
   - Callback URL: `https://your-app-name.fly.dev/whatsapp/webhook`
   - Verify Token: (use the value you set in `WABA_VERIFY_TOKEN`)

3. **Subscribe to Webhook Fields**
   - Select: `messages`, `message_status`

4. **Verify Webhook**
   - Click "Verify and Save"
   - Meta will send a verification request to your endpoint

5. **Test Webhook**
   ```bash
   # Send a test message to your WhatsApp number
   # Check logs to verify receipt
   flyctl logs -a your-app-name
   ```

### M-PESA Callback Setup

1. **Log in to Safaricom Daraja Portal**
   - Go to https://developer.safaricom.co.ke/

2. **Navigate to Your App**
   - Select your Lipa Na M-PESA Online app

3. **Set Callback URL**
   - Callback URL: `https://your-app-name.fly.dev/payments/stk/callback`
   - **Important:** Must be HTTPS (HTTP will be rejected)

4. **IP Whitelisting (If Required)**
   - Get Fly.io app IP addresses:
     ```bash
     flyctl ips list -a your-app-name
     ```
   - Add these IPs to Daraja Portal whitelist

5. **Test Callback**
   - Initiate a test STK Push
   - Check logs to verify callback receipt:
     ```bash
     flyctl logs -a your-app-name | grep "stk/callback"
     ```

### Webhook Testing Checklist

- [ ] WhatsApp webhook verified successfully
- [ ] WhatsApp test message received and logged
- [ ] M-PESA callback URL configured
- [ ] M-PESA test callback received and logged
- [ ] All webhook requests have HTTPS URLs
- [ ] Webhook signatures validated (if applicable)

---

## Monitoring and Logs

### View Application Logs

```bash
# Tail logs in real-time
flyctl logs -a your-app-name

# View last 100 lines
flyctl logs -a your-app-name --lines 100

# Filter logs by keyword
flyctl logs -a your-app-name | grep "ERROR"

# View logs for specific machine
flyctl logs -a your-app-name --instance <machine-id>
```

### Access Application Metrics

```bash
# View app metrics dashboard
flyctl dashboard -a your-app-name

# Check app status
flyctl status -a your-app-name
```

### Business Metrics Endpoint

Access business metrics at:

```bash
# Get summary statistics
curl https://your-app-name.fly.dev/stats/summary
```

Returns:
- Invoice counts by status
- Conversion rate (paid/sent)
- Average payment time

### Structured Logging

All application logs are JSON-structured with these fields:
- `timestamp` - ISO 8601 timestamp
- `level` - Log level (INFO, WARNING, ERROR, etc.)
- `message` - Log message
- `correlation_id` - Request correlation ID
- Additional context fields

### Log Retention

Fly.io retains logs for a limited time. For long-term retention:

1. **Set up Log Shipping**
   ```bash
   # Ship logs to external service (e.g., Papertrail, Logflare)
   flyctl extensions create logflare -a your-app-name
   ```

2. **Or use Fly.io's Log Shipper**
   - Configure in `fly.toml` or via Fly.io dashboard

---

## Scaling

### Horizontal Scaling (Add More Machines)

```bash
# Scale to 2 machines
flyctl scale count 2 -a your-app-name

# Scale to specific regions
flyctl scale count 2 --region nbo,jnb -a your-app-name
```

### Vertical Scaling (Increase Resources)

```bash
# Scale VM memory
flyctl scale memory 512 -a your-app-name

# Scale VM CPU
flyctl scale vm shared-cpu-2x -a your-app-name
```

### Auto-Scaling Configuration

Auto-scaling is configured in `fly.toml`:

```toml
[http_service]
  auto_stop_machines = true
  auto_start_machines = true
  min_machines_running = 1
```

### Check Current Scale

```bash
# View running machines
flyctl machines list -a your-app-name

# View app resources
flyctl status -a your-app-name
```

---

## Troubleshooting

### Common Issues and Solutions

#### 1. Application Won't Deploy

**Symptoms:** Deployment fails with build errors

**Solutions:**
```bash
# Check Dockerfile syntax
docker build -t invoiceiq .

# View deployment logs
flyctl logs -a your-app-name

# SSH into failed deployment for debugging
flyctl ssh console -a your-app-name
```

#### 2. Database Connection Fails

**Symptoms:** `503 Service Unavailable` on `/readyz`

**Solutions:**
```bash
# Check DATABASE_URL is set
flyctl secrets list -a your-app-name

# Verify the connection string format is correct (should include +asyncpg and ?sslmode=require)
# Example: postgresql+asyncpg://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres?sslmode=require

# Test database connection from app
flyctl ssh console -a your-app-name
# Inside container:
python -c "from src.app.db import engine; import asyncio; asyncio.run(engine.dispose())"

# Check Supabase project status
# Visit https://supabase.com/dashboard and check if your project is healthy

# Verify IP whitelisting (if configured in Supabase)
# Supabase allows all IPs by default, but check Network Restrictions in project settings
```

**Common Issues:**
- Missing `+asyncpg` in connection string
- Missing `?sslmode=require` parameter
- Incorrect password (check Supabase project settings)
- Database project paused (free tier projects pause after inactivity)
- Network connectivity issues (check Supabase status page)

#### 3. Webhooks Not Receiving Requests

**Symptoms:** WhatsApp/M-PESA callbacks not logged

**Solutions:**
```bash
# Verify app is running
flyctl status -a your-app-name

# Check webhook URLs are correct
echo "WhatsApp: https://your-app-name.fly.dev/whatsapp/webhook"
echo "M-PESA: https://your-app-name.fly.dev/payments/stk/callback"

# Test webhook endpoints
curl https://your-app-name.fly.dev/whatsapp/webhook
curl https://your-app-name.fly.dev/payments/stk/callback

# Check logs for webhook requests
flyctl logs -a your-app-name | grep webhook
```

#### 4. High Memory Usage

**Symptoms:** App crashes or restarts frequently

**Solutions:**
```bash
# Check memory usage
flyctl status -a your-app-name

# Increase memory allocation
flyctl scale memory 512 -a your-app-name

# Check for memory leaks in logs
flyctl logs -a your-app-name | grep "memory"
```

#### 5. SSL Certificate Issues

**Symptoms:** HTTPS not working, certificate errors

**Solutions:**
```bash
# Check certificate status
flyctl certs show your-app-name.fly.dev -a your-app-name

# Force certificate refresh
flyctl certs create your-app-name.fly.dev -a your-app-name
```

#### 6. Migration Failures

**Symptoms:** Deployment fails at release command

**Solutions:**
```bash
# Check migration status
flyctl ssh console -a your-app-name
alembic current
alembic history

# View migration error details
flyctl logs -a your-app-name | grep alembic

# Manually run migrations
flyctl ssh console -a your-app-name
alembic upgrade head
```

### Emergency Rollback

```bash
# List recent releases
flyctl releases -a your-app-name

# Rollback to previous version
flyctl releases rollback -a your-app-name

# Rollback to specific version
flyctl releases rollback <version> -a your-app-name
```

### Get Help

```bash
# Fly.io community forum
https://community.fly.io/

# Fly.io documentation
https://fly.io/docs/

# Open support ticket
flyctl support -a your-app-name
```

---

## Maintenance Tasks

### Regular Maintenance Checklist

- [ ] Monitor application logs weekly
- [ ] Review error rates and response times
- [ ] Check database size and performance
- [ ] Review and rotate secrets quarterly
- [ ] Update dependencies monthly
- [ ] Test backup and restore procedures
- [ ] Verify webhook deliveries
- [ ] Monitor cost and usage metrics

### Backup Database

#### Automatic Backups (Supabase)

Supabase automatically backs up your database:

- **Free Tier:** Daily backups (7-day retention)
- **Pro Tier:** Daily backups (30-day retention)
- **Point-in-time Recovery (Pro+):** Restore to any point in the last 30 days

#### Access Backups

1. **View Backups**
   - Go to https://supabase.com/dashboard/project/[PROJECT-REF]
   - Navigate to "Database" → "Backups"
   - View list of available backups

2. **Download Backup**
   - Click on a backup to download it
   - Format: SQL dump file

3. **Restore from Backup**
   - In Supabase dashboard, go to "Database" → "Backups"
   - Select the backup you want to restore
   - Click "Restore" (this will replace current data)

#### Manual Database Backup

For additional safety, create manual backups:

```bash
# Using pg_dump (requires PostgreSQL client tools)
pg_dump "postgresql://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres" > backup_$(date +%Y%m%d).sql

# Restore from manual backup
psql "postgresql://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres" < backup_20250116.sql
```

#### Backup Best Practices

- Monitor backup status weekly in Supabase dashboard
- Test restore procedure at least once before going live
- Consider upgrading to Pro tier for longer retention
- Export critical data regularly for offline storage

### Update Application

```bash
# Pull latest code
git pull origin main

# Update dependencies
pip install --upgrade -r requirements.txt

# Deploy updated application
flyctl deploy -a your-app-name
```

---

## Production Checklist

Before going live, ensure:

- [ ] All secrets configured correctly
- [ ] Database attached and migrations run
- [ ] Health checks passing
- [ ] WhatsApp webhook verified
- [ ] M-PESA callback URL registered
- [ ] SMS fallback tested
- [ ] End-to-end payment flow tested
- [ ] Monitoring and logging configured
- [ ] SSL certificates active (automatic with Fly.io)
- [ ] Domain configured (if using custom domain)
- [ ] Backup strategy in place
- [ ] Scaling limits configured
- [ ] Error alerting set up

---

## Support Contacts

- **Fly.io Support:** https://fly.io/support
- **Meta Developer Support:** https://developers.facebook.com/support
- **Safaricom Daraja Support:** daraja@safaricom.co.ke
- **Africa's Talking Support:** https://help.africastalking.com/
