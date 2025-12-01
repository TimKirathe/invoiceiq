-- InvoiceIQ Database Schema
-- Raw SQL schema for manual initialization or reference
-- This matches the SQLAlchemy models in src/app/models.py

-- invoices table
CREATE TABLE invoices (
  id TEXT PRIMARY KEY,
  customer_name TEXT,
  msisdn TEXT NOT NULL CHECK (LENGTH(msisdn) = 12),
  merchant_msisdn TEXT NOT NULL CHECK (LENGTH(merchant_msisdn) = 12),
  amount_cents INTEGER NOT NULL,
  vat_amount INTEGER NOT NULL DEFAULT 0,
  currency TEXT NOT NULL DEFAULT 'KES',
  status TEXT NOT NULL CHECK (status IN ('PENDING','SENT','PAID','CANCELLED','FAILED')),
  pay_ref TEXT,
  pay_link TEXT,
  -- Invoice template fields (added 2025-11-26)
  merchant_name TEXT,
  line_items JSONB,
  due_date TEXT,
  mpesa_method TEXT CHECK (mpesa_method IN ('PAYBILL', 'TILL', 'PHONE')),
  mpesa_paybill_number TEXT,
  mpesa_account_number TEXT,
  mpesa_till_number TEXT,
  mpesa_phone_number TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- payments table
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
  retry_count INTEGER DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- message_log table
CREATE TABLE message_log (
  id TEXT PRIMARY KEY,
  invoice_id TEXT REFERENCES invoices(id),
  channel TEXT NOT NULL CHECK (channel IN ('WHATSAPP','SMS')),
  direction TEXT NOT NULL CHECK (direction IN ('IN','OUT')),
  event TEXT,
  payload JSON,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- merchant_payment_methods table (added 2025-11-26)
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

-- Indexes for common queries
CREATE INDEX idx_invoices_msisdn ON invoices(msisdn);
CREATE INDEX idx_invoices_merchant ON invoices(merchant_msisdn);
CREATE INDEX idx_invoices_status ON invoices(status);
CREATE INDEX idx_invoices_created_at ON invoices(created_at);

CREATE INDEX idx_payments_invoice_id ON payments(invoice_id);
CREATE INDEX idx_payments_idempotency_key ON payments(idempotency_key);
CREATE INDEX idx_payments_status ON payments(status);

CREATE INDEX idx_message_log_invoice_id ON message_log(invoice_id);
CREATE INDEX idx_message_log_channel ON message_log(channel);
CREATE INDEX idx_message_log_created_at ON message_log(created_at);

CREATE INDEX idx_merchant_payment_methods_merchant ON merchant_payment_methods(merchant_msisdn);