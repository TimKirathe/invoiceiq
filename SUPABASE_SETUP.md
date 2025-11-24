# Supabase Database Setup

This document contains the SQL schema for the InvoiceIQ database. Run these commands in the Supabase SQL Editor to create all required tables.

## Prerequisites

1. Create a Supabase project at https://supabase.com
2. Navigate to the SQL Editor in your Supabase dashboard
3. Run the SQL commands below

## Database Schema

### 1. Invoices Table

```sql
CREATE TABLE IF NOT EXISTS invoices (
    -- Primary key
    id TEXT PRIMARY KEY,

    -- Customer information
    customer_name TEXT NULL CHECK (LENGTH(customer_name) <= 60),
    msisdn TEXT NOT NULL CHECK (LENGTH(msisdn) = 12),

    -- Merchant information
    merchant_msisdn TEXT NOT NULL CHECK (LENGTH(merchant_msisdn) = 12),

    -- Invoice details
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 100),
    vat_amount INTEGER NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'KES' CHECK (LENGTH(currency) = 3),
    description TEXT NOT NULL CHECK (LENGTH(description) >= 3 AND LENGTH(description) <= 120),

    -- Status and payment info
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'SENT', 'PAID', 'CANCELLED', 'FAILED')),
    pay_ref TEXT NULL,
    pay_link TEXT NULL,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Create index on status for faster queries
CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);

-- Create index on merchant for faster merchant-specific queries
CREATE INDEX IF NOT EXISTS idx_invoices_merchant ON invoices(merchant_msisdn);

-- Create index on customer MSISDN for faster customer lookups
CREATE INDEX IF NOT EXISTS idx_invoices_msisdn ON invoices(msisdn);

-- Create trigger to auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_invoices_updated_at
    BEFORE UPDATE ON invoices
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
```

### 2. Payments Table

```sql
CREATE TABLE IF NOT EXISTS payments (
    -- Primary key
    id TEXT PRIMARY KEY,

    -- Foreign key to invoice
    invoice_id TEXT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,

    -- Payment details
    method TEXT NOT NULL DEFAULT 'MPESA_STK' CHECK (method IN ('MPESA_STK')),
    status TEXT NOT NULL DEFAULT 'INITIATED' CHECK (status IN ('INITIATED', 'SUCCESS', 'FAILED', 'EXPIRED')),
    mpesa_receipt TEXT NULL,
    amount_cents INTEGER NOT NULL,

    -- Request/callback payloads (stored as JSONB)
    raw_request JSONB NULL,
    raw_callback JSONB NULL,

    -- Idempotency
    idempotency_key TEXT NOT NULL UNIQUE,

    -- M-PESA identifiers for callback matching
    checkout_request_id TEXT NULL,
    merchant_request_id TEXT NULL,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Create index on invoice_id for faster joins
CREATE INDEX IF NOT EXISTS idx_payments_invoice_id ON payments(invoice_id);

-- Create index on status for faster status queries
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);

-- Create index on checkout_request_id for M-PESA callback matching
CREATE INDEX IF NOT EXISTS idx_payments_checkout_request_id ON payments(checkout_request_id);

-- Create index on idempotency_key for duplicate prevention
CREATE INDEX IF NOT EXISTS idx_payments_idempotency_key ON payments(idempotency_key);

-- Create trigger to auto-update updated_at
CREATE TRIGGER update_payments_updated_at
    BEFORE UPDATE ON payments
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
```

### 3. Message Log Table

```sql
CREATE TABLE IF NOT EXISTS message_log (
    -- Primary key
    id TEXT PRIMARY KEY,

    -- Foreign key to invoice (nullable - not all messages are invoice-related)
    invoice_id TEXT NULL REFERENCES invoices(id) ON DELETE SET NULL,

    -- Message metadata
    channel TEXT NOT NULL CHECK (channel IN ('WHATSAPP', 'SMS')),
    direction TEXT NOT NULL CHECK (direction IN ('IN', 'OUT')),
    event TEXT NULL,

    -- Message payload (stored as JSONB)
    payload JSONB NULL,

    -- Timestamp
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Create index on invoice_id for faster invoice-related message lookups
CREATE INDEX IF NOT EXISTS idx_message_log_invoice_id ON message_log(invoice_id);

-- Create index on channel for faster channel-specific queries
CREATE INDEX IF NOT EXISTS idx_message_log_channel ON message_log(channel);

-- Create index on direction for faster direction-specific queries
CREATE INDEX IF NOT EXISTS idx_message_log_direction ON message_log(direction);

-- Create index on created_at for time-based queries
CREATE INDEX IF NOT EXISTS idx_message_log_created_at ON message_log(created_at DESC);
```

## Post-Setup Verification

After running the SQL above, verify the tables were created correctly:

```sql
-- List all tables
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public';

-- Check invoices table structure
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'invoices';

-- Check payments table structure
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'payments';

-- Check message_log table structure
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'message_log';
```

## Row-Level Security (RLS)

**Note:** RLS is NOT required for this MVP since all database access is through the backend using the `service_role` key. Users (merchants and customers) never interact with Supabase directly.

If you want to enable RLS for additional security (optional):

```sql
-- Enable RLS on all tables
ALTER TABLE invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE message_log ENABLE ROW LEVEL SECURITY;

-- Since we're using service_role key, we don't need specific policies
-- The service_role key bypasses RLS automatically
```

## Environment Variables

After creating the tables, set these environment variables in your Fly.io app:

```bash
# Get these from Supabase Dashboard > Project Settings > API
fly secrets set SUPABASE_URL=<your-supabase-url>
fly secrets set SUPABASE_SECRET_KEY=<your-service-role-key>
```

**IMPORTANT:** Use the `service_role` key (NOT the `anon` key) for backend operations.

## Migration from SQLAlchemy

If you have existing data in a SQLAlchemy database, you'll need to export and import:

1. Export data from existing database
2. Transform to match Supabase schema (TIMESTAMPTZ format, etc.)
3. Import into Supabase using SQL INSERT statements or CSV upload

For this MVP, there's no existing production data to migrate.