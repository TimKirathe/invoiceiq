# Supabase Migration Status

## Summary

This document tracks the progress of migrating from SQLAlchemy to Supabase. The migration is **PARTIALLY COMPLETE** and requires finishing the router files and services.

## ✅ Completed

1. **Configuration (config.py)**
   - Removed `database_url` field
   - Added `supabase_url` and `supabase_secret_key` fields
   - Status: ✅ DONE

2. **Database Client (db.py)**
   - Removed SQLAlchemy engine and session factory
   - Created Supabase client with service_role key
   - Updated `get_db()` to `get_supabase()` dependency
   - Status: ✅ DONE

3. **Models (models.py)**
   - Deleted SQLAlchemy models entirely
   - Pydantic schemas in `schemas.py` handle type validation
   - Status: ✅ DONE

4. **Main Application (main.py)**
   - Removed SQLAlchemy imports
   - Removed `create_tables()` and `engine.dispose()` from lifespan
   - Updated readiness check to use Supabase
   - Updated stats endpoint to use Supabase
   - Status: ✅ DONE

5. **Metrics Service (services/metrics.py)**
   - Replaced all SQLAlchemy queries with Supabase client queries
   - Updated function signatures to accept `Client` instead of `AsyncSession`
   - Status: ✅ DONE

6. **Idempotency Service (services/idempotency.py)**
   - Replaced SQLAlchemy queries with Supabase client queries
   - Updated function signatures to accept `Client` instead of `AsyncSession`
   - Return dicts instead of ORM objects
   - Status: ✅ DONE

7. **Dependencies (requirements.txt)**
   - Removed: `sqlalchemy`, `alembic`, `asyncpg`, `aiosqlite`
   - Added: `supabase==2.10.0`
   - Status: ✅ DONE

8. **Alembic**
   - Deleted `alembic/` directory
   - Deleted `alembic.ini` file
   - Removed `release_command` from `fly.toml`
   - Status: ✅ DONE

9. **Database Schema Documentation (SUPABASE_SETUP.md)**
   - Created complete SQL schema with all tables, indexes, and triggers
   - Documented setup instructions
   - Status: ✅ DONE

## ⚠️ Remaining Work

The following files still need to be migrated from SQLAlchemy to Supabase:

### High Priority (Required for MVP to work)

1. **routers/invoices.py**
   - Endpoints: `GET /invoices/{invoice_id}`, `POST /invoices`
   - Change: Replace `AsyncSession` with `Client`, convert SQL queries to Supabase queries
   - Estimated effort: 30-40 minutes

3. **routers/payments.py**
   - Endpoints: `POST /payments/stk/initiate`, `POST /payments/stk/callback`
   - Change: Replace all database operations with Supabase queries
   - Critical: Payment callback handler is complex
   - Estimated effort: 45-60 minutes

4. **routers/whatsapp.py**
   - Endpoints: `GET /whatsapp/webhook`, `POST /whatsapp/webhook`
   - Change: Replace database operations in message handler
   - Estimated effort: 40-50 minutes

5. **routers/sms.py**
   - Endpoints: `POST /sms/inbound`, `POST /sms/status`
   - Change: Replace database operations
   - Estimated effort: 20-30 minutes

6. **routers/invoice_view.py**
   - Endpoints: `GET /{invoice_id}`, `GET /pay/{invoice_id}`
   - Change: Replace SQLAlchemy select queries with Supabase queries
   - Estimated effort: 25-35 minutes

**Total estimated effort for remaining work: 3-4 hours**

## Migration Pattern Reference

### Common SQLAlchemy → Supabase Conversions

```python
# SELECT single record
# SQLAlchemy:
stmt = select(Invoice).where(Invoice.id == invoice_id)
result = await db.execute(stmt)
invoice = result.scalar_one_or_none()

# Supabase:
response = supabase.table("invoices").select("*").eq("id", invoice_id).execute()
invoice_data = response.data[0] if response.data else None

# INSERT
# SQLAlchemy:
invoice = Invoice(id=id, msisdn=msisdn, amount_cents=amount)
db.add(invoice)
await db.commit()
await db.refresh(invoice)

# Supabase:
response = supabase.table("invoices").insert({
    "id": id,
    "msisdn": msisdn,
    "amount_cents": amount
}).execute()
invoice_data = response.data[0]

# UPDATE
# SQLAlchemy:
invoice.status = "PAID"
await db.commit()

# Supabase:
response = supabase.table("invoices").update({
    "status": "PAID"
}).eq("id", invoice_id).execute()

# DELETE
# SQLAlchemy:
await db.delete(invoice)
await db.commit()

# Supabase:
supabase.table("invoices").delete().eq("id", invoice_id).execute()

# WHERE IN
# SQLAlchemy:
stmt = select(Invoice).where(Invoice.status.in_(["SENT", "PAID"]))

# Supabase:
response = supabase.table("invoices").select("*").in_("status", ["SENT", "PAID"]).execute()

# JOIN
# SQLAlchemy:
stmt = select(Invoice).join(Payment).where(Payment.status == "SUCCESS")

# Supabase:
response = supabase.table("invoices").select("*, payments!inner(status)").eq("payments.status", "SUCCESS").execute()
```

### Important Notes

1. **No Async Methods**: The Supabase Python client doesn't use `await`. Remove all `await` keywords from Supabase operations.

2. **Data Access**: Supabase returns `response.data` as a list of dictionaries, not ORM objects.

3. **Single vs Multiple**:
   - `response.data[0]` for single record
   - `response.data` for list of records
   - Check `if response.data` before accessing

4. **Timestamps**: Supabase returns ISO format strings. Parse with:
   ```python
   from datetime import datetime
   dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
   ```

5. **Error Handling**: Supabase raises exceptions on errors. Wrap in try/except as before.

## Next Steps

1. **Complete Router Migrations**: Work through each router file systematically
2. **Test Each Endpoint**: After migrating each router, test the endpoints
3. **Run Linter**: `ruff check .` after all changes
4. **Deploy to Fly.io**: Once all routers are migrated and tested

## Deployment Checklist

Before deploying:

- [ ] All router files migrated to Supabase
- [ ] All service files migrated to Supabase
- [ ] Supabase database schema created (run SUPABASE_SETUP.md SQL)
- [ ] Environment variables set in Fly.io (`SUPABASE_URL`, `SUPABASE_SECRET_KEY`)
- [ ] Linter passes (`ruff check .`)
- [ ] Local testing completed
- [ ] Ready for deployment

## Fly.io Deployment Commands

```bash
# Set Supabase secrets
fly secrets set SUPABASE_URL=https://your-project.supabase.co
fly secrets set SUPABASE_SECRET_KEY=your-service-role-key

# Deploy
fly deploy
```