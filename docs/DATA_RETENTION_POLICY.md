# Data Retention Policy

## Overview

This document outlines the data retention policy for InvoiceIQ, a privacy-first invoicing system. Our approach minimizes PII (Personally Identifiable Information) storage while maintaining operational visibility and compliance requirements.

## Privacy-First Philosophy

InvoiceIQ implements a **metadata-only logging strategy** where:
- Message content, phone numbers, customer names, and amounts are NOT stored in logs
- Only operational metadata (message IDs, statuses, timestamps, error types) is logged
- This approach minimizes GDPR/CCPA compliance burden and reduces liability in case of data breaches

## Data Categories and Retention Periods

### 1. Message Logs (`message_log` table)

**Purpose**: Operational debugging, delivery tracking, performance monitoring

**Data Stored**:
- Message ID (from WhatsApp/SMS provider)
- Event type (e.g., `invoice_sent`, `receipt_sent_customer`, `invoice_send_failed`)
- Channel (WHATSAPP or SMS)
- Direction (IN or OUT)
- Status code
- Error type (if applicable)
- Timestamp
- Associated invoice_id (foreign key)

**Retention Period**: **90 days**

**Rationale**:
- 90 days provides sufficient operational history for debugging recent issues
- Most delivery/payment issues surface within days, not months
- Balances debugging needs with privacy obligations

**Auto-Deletion Strategy**:
- Implement automated cleanup job (e.g., daily cron) to delete message_log entries older than 90 days
- Example SQL: `DELETE FROM message_log WHERE created_at < NOW() - INTERVAL '90 days'`

### 2. Payment Records (`payments` table)

**Purpose**: Financial audit trail, transaction reconciliation, compliance

**Data Stored**:
- Payment ID
- Invoice ID (foreign key)
- Payment method (MPESA)
- Status (INITIATED, SUCCESS, FAILED, EXPIRED)
- M-PESA receipt number (for successful payments)
- Amount (in cents)
- Idempotency key
- Raw request/callback payloads (JSON)
- Timestamps

**Retention Period**: **7 years**

**Rationale**:
- Financial regulations typically require 7 years of transaction history
- Tax audit requirements
- Dispute resolution

**PII Minimization**:
- Phone numbers in raw_request/raw_callback are acceptable as they're tied to financial transactions
- Consider encrypting raw payloads at rest for additional security

**Auto-Deletion Strategy**:
- Do NOT auto-delete (financial compliance)
- Only anonymize/delete upon explicit customer request (right to erasure)

### 3. Invoice Records (`invoices` table)

**Purpose**: Business records, payment reconciliation, customer service

**Data Stored**:
- Invoice ID
- Customer MSISDN (phone number)
- Customer name (optional)
- Amount (in cents)
- Description
- Status (PENDING, SENT, PAID, CANCELLED, FAILED)
- Payment reference
- Timestamps

**Retention Period**: **7 years** (same as payments)

**Rationale**:
- Business records required for financial compliance
- Linked to payment records

**PII Minimization**:
- Customer MSISDN and name are necessary for business operation
- Consider encrypting customer_name field at rest

**Auto-Deletion Strategy**:
- Do NOT auto-delete (financial compliance)
- Support right to erasure (see section below)

### 4. Application Logs (stdout/stderr, log files)

**Purpose**: Debugging, error tracking, performance monitoring

**Data Stored** (via structured JSON logging):
- Correlation IDs
- Event descriptions
- Error types and stack traces
- Service names, endpoints, status codes
- Duration metrics
- **NO PII** (filtered by log_event helper)

**Retention Period**: **30 days** (in log aggregation systems like CloudWatch, DataDog, etc.)

**Rationale**:
- Recent logs are most valuable for debugging
- Older logs rarely consulted
- Cost optimization

**Auto-Deletion Strategy**:
- Configure log aggregation system (e.g., CloudWatch Logs retention policy)
- Local log files rotated and deleted after 7 days

## Right to Erasure (GDPR Article 17)

### Customer Data Deletion Requests

When a customer requests data deletion:

1. **Anonymization Approach** (Recommended for MVP):
   - Replace `customer_name` with `"[REDACTED]"`
   - Replace `msisdn` with `"254700000000"` (anonymized number)
   - Keep invoice and payment records for financial audit purposes
   - Update `description` to `"[REDACTED]"` if it contains customer-specific details

2. **Hard Deletion Approach** (Post-MVP):
   - Only delete if no financial/legal obligation to retain
   - Delete associated message_log entries
   - Archive invoice/payment records to cold storage with encrypted PII

3. **Response Timeline**: Within **30 days** of request

### Implementation Example

```python
async def anonymize_customer_data(db: AsyncSession, msisdn: str) -> None:
    """
    Anonymize customer data for right to erasure compliance.

    Keeps financial records but removes identifying information.
    """
    # Find all invoices for this customer
    invoices = await db.execute(
        select(Invoice).where(Invoice.msisdn == msisdn)
    )

    for invoice in invoices.scalars():
        invoice.customer_name = "[REDACTED]"
        invoice.msisdn = "254700000000"  # Anonymized
        invoice.description = "[REDACTED]"

    await db.commit()

    logger.info(
        "Customer data anonymized",
        extra={"invoice_count": len(invoices)}
    )
```

## Data Backup Policies

### Backup Retention

- **Database Snapshots**: Retained for **30 days**
- **Incremental Backups**: Retained for **7 days**
- **Disaster Recovery Archives**: Retained for **90 days**

### Backup Considerations for Data Deletion

- When a customer requests data deletion, backups may still contain their data
- Document this in privacy policy: "Deleted data may persist in backups for up to 90 days"
- After 90 days, backups containing deleted data are cycled out

## Compliance Summary

| Data Type | Retention Period | Auto-Deletion | GDPR Compliance |
|-----------|------------------|---------------|-----------------|
| Message Logs | 90 days | Yes | Metadata only, no PII |
| Payments | 7 years | No | Anonymize on request |
| Invoices | 7 years | No | Anonymize on request |
| Application Logs | 30 days | Yes | PII-filtered |
| Database Backups | 90 days | Yes | Includes deleted data |

## Implementation Checklist

- [ ] Set up automated cleanup job for message_log (90-day retention)
- [ ] Set up automated cleanup job for application logs (30-day retention)
- [ ] Configure database backup retention policies (30/7/90 days)
- [ ] Implement customer data anonymization endpoint/script
- [ ] Document right to erasure process in privacy policy
- [ ] Add data retention info to user-facing privacy policy
- [ ] Test anonymization script with sample data
- [ ] Set up monitoring/alerting for failed cleanup jobs

## Future Enhancements (Post-MVP)

1. **Encryption at Rest**:
   - Encrypt `customer_name` and `raw_request`/`raw_callback` fields
   - Use application-level encryption with key rotation

2. **Data Access Logging**:
   - Log all accesses to invoice/payment records with customer data
   - Track who accessed what data and when

3. **Granular Retention Policies**:
   - Different retention for different invoice statuses (e.g., CANCELLED invoices deleted sooner)
   - Configurable retention periods per merchant (multi-tenant)

4. **Automated Compliance Reports**:
   - Weekly/monthly reports on data volumes, retention compliance
   - Alerts for data older than retention period

## Review and Updates

- **Review Frequency**: Annually, or when regulations change
- **Last Reviewed**: 2025-11-15
- **Next Review**: 2026-11-15

## Contact

For questions about this policy or data deletion requests, contact: [support@invoiceiq.example]