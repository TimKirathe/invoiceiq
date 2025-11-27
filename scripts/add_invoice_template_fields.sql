-- Migration: Add Invoice Template Fields
-- Date: 2025-11-26
-- Description: Add new columns to invoices table and create merchant_payment_methods table
--              to support WhatsApp template message requirements

-- ============================================================================
-- PART 1: Add new columns to invoices table
-- ============================================================================

-- Add merchant information
-- This stores the business/merchant name shown to customers
ALTER TABLE invoices ADD COLUMN merchant_name TEXT;

-- Add line items storage (JSON structure)
-- Structure: [{"name": "Item", "unit_price_cents": 150000, "quantity": 3, "subtotal_cents": 450000}, ...]
ALTER TABLE invoices ADD COLUMN line_items JSONB;

-- Add due date information
-- Examples: "Due on receipt", "In 7 days (5 Dec 2025)", "30 Nov 2025"
ALTER TABLE invoices ADD COLUMN due_date TEXT;

-- Add M-PESA payment method type
-- Determines which M-PESA payment method the customer should use
ALTER TABLE invoices ADD COLUMN mpesa_method TEXT CHECK (mpesa_method IN ('PAYBILL', 'TILL', 'PHONE'));

-- Add M-PESA Paybill details (used when mpesa_method = 'PAYBILL')
ALTER TABLE invoices ADD COLUMN mpesa_paybill_number TEXT;
ALTER TABLE invoices ADD COLUMN mpesa_account_number TEXT;

-- Add M-PESA Till Number (used when mpesa_method = 'TILL')
ALTER TABLE invoices ADD COLUMN mpesa_till_number TEXT;

-- Add M-PESA Phone Number (used when mpesa_method = 'PHONE')
-- Format: 2547XXXXXXXX (E.164 without +)
ALTER TABLE invoices ADD COLUMN mpesa_phone_number TEXT;

-- ============================================================================
-- PART 2: Create merchant_payment_methods table
-- ============================================================================

-- This table stores saved M-PESA payment methods for merchants
-- Merchants can save their payment details for reuse across invoices
CREATE TABLE merchant_payment_methods (
  id TEXT PRIMARY KEY,
  merchant_msisdn TEXT NOT NULL,
  method_type TEXT NOT NULL CHECK (method_type IN ('PAYBILL', 'TILL', 'PHONE')),

  -- Paybill fields (populated when method_type = 'PAYBILL')
  paybill_number TEXT,
  account_number TEXT,

  -- Till Number field (populated when method_type = 'TILL')
  till_number TEXT,

  -- Phone Number field (populated when method_type = 'PHONE')
  -- Format: 2547XXXXXXXX (E.164 without +)
  phone_number TEXT,

  -- Default flag (allows merchants to set a preferred payment method)
  is_default BOOLEAN DEFAULT FALSE,

  -- Timestamps
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- PART 3: Create indexes
-- ============================================================================

-- Index for looking up payment methods by merchant
-- This optimizes queries when displaying saved payment methods to merchants
CREATE INDEX idx_merchant_payment_methods_merchant ON merchant_payment_methods(merchant_msisdn);

-- ============================================================================
-- MIGRATION NOTES
-- ============================================================================
-- 1. All new columns in invoices table allow NULL values to handle existing records
-- 2. Existing invoices will have NULL values for new fields (handled gracefully in code)
-- 3. The merchant_payment_methods table is new and will be empty after migration
-- 4. This migration is forward-compatible and does not break existing functionality
-- 5. To rollback, see the rollback plan in INVOICE_TEMPLATE_IMPLEMENTATION_PLAN.md