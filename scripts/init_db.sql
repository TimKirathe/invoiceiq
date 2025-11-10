-- InvoiceIQ Database Schema
-- Raw SQL schema for manual initialization or reference
-- This matches the SQLAlchemy models in src/app/models.py

-- invoices table
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

-- Indexes for common queries
CREATE INDEX idx_invoices_msisdn ON invoices(msisdn);
CREATE INDEX idx_invoices_status ON invoices(status);
CREATE INDEX idx_invoices_created_at ON invoices(created_at);

CREATE INDEX idx_payments_invoice_id ON payments(invoice_id);
CREATE INDEX idx_payments_idempotency_key ON payments(idempotency_key);
CREATE INDEX idx_payments_status ON payments(status);

CREATE INDEX idx_message_log_invoice_id ON message_log(invoice_id);
CREATE INDEX idx_message_log_channel ON message_log(channel);
CREATE INDEX idx_message_log_created_at ON message_log(created_at);