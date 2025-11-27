-- Migration: Remove deprecated description field
-- Date: 2025-11-27
-- Description: Remove description column as it has been replaced by line_items JSONB
--
-- This migration removes the description field from the invoices table which is no longer
-- needed because invoice details are now stored in the line_items JSONB field.

-- Drop the CHECK constraint first (if it exists)
ALTER TABLE invoices DROP CONSTRAINT IF EXISTS invoices_description_check;

-- Drop the description column
ALTER TABLE invoices DROP COLUMN IF EXISTS description;