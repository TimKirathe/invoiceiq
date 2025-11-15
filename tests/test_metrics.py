"""
Unit tests for metrics service.

Tests database query functions for invoice statistics, conversion rate,
and average payment time calculations.
"""

import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.db import Base
from src.app.models import Invoice, Payment
from src.app.services.metrics import (
    get_average_payment_time,
    get_conversion_rate,
    get_invoice_stats,
)


# Test Fixtures


@pytest_asyncio.fixture
async def async_engine():
    """Create an async in-memory SQLite engine for testing."""
    from sqlalchemy import event

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )

    # Enable foreign keys for SQLite
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Use sync_engine for event listening
    event.listen(engine.sync_engine, "connect", set_sqlite_pragma)

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    # Cleanup
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine):
    """Create an async session for testing."""
    async_session_factory = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session_factory() as session:
        yield session
        await session.rollback()


# Test get_invoice_stats


@pytest.mark.asyncio
async def test_get_invoice_stats_empty_database(async_session: AsyncSession):
    """Test invoice stats with no invoices in database."""
    stats = await get_invoice_stats(async_session)

    assert stats["total"] == 0
    assert stats["pending"] == 0
    assert stats["sent"] == 0
    assert stats["paid"] == 0
    assert stats["failed"] == 0
    assert stats["cancelled"] == 0


@pytest.mark.asyncio
async def test_get_invoice_stats_single_invoice(async_session: AsyncSession):
    """Test invoice stats with a single invoice."""
    # Create a pending invoice
    invoice = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254712345679",
        amount_cents=10000,
        description="Test invoice",
        status="PENDING",
    )
    async_session.add(invoice)
    await async_session.commit()

    stats = await get_invoice_stats(async_session)

    assert stats["total"] == 1
    assert stats["pending"] == 1
    assert stats["sent"] == 0
    assert stats["paid"] == 0
    assert stats["failed"] == 0
    assert stats["cancelled"] == 0


@pytest.mark.asyncio
async def test_get_invoice_stats_multiple_statuses(async_session: AsyncSession):
    """Test invoice stats with invoices in various statuses."""
    # Create invoices with different statuses
    invoices = [
        Invoice(
            msisdn="254712345678",
            merchant_msisdn="254712345679",
            amount_cents=10000,
            description="Pending invoice",
            status="PENDING",
        ),
        Invoice(
            msisdn="254712345678",
            merchant_msisdn="254712345679",
            amount_cents=20000,
            description="Sent invoice",
            status="SENT",
        ),
        Invoice(
            msisdn="254712345678",
            merchant_msisdn="254712345679",
            amount_cents=30000,
            description="Paid invoice 1",
            status="PAID",
        ),
        Invoice(
            msisdn="254712345678",
            merchant_msisdn="254712345679",
            amount_cents=40000,
            description="Paid invoice 2",
            status="PAID",
        ),
        Invoice(
            msisdn="254712345678",
            merchant_msisdn="254712345679",
            amount_cents=50000,
            description="Failed invoice",
            status="FAILED",
        ),
        Invoice(
            msisdn="254712345678",
            merchant_msisdn="254712345679",
            amount_cents=60000,
            description="Cancelled invoice",
            status="CANCELLED",
        ),
    ]

    for invoice in invoices:
        async_session.add(invoice)
    await async_session.commit()

    stats = await get_invoice_stats(async_session)

    assert stats["total"] == 6
    assert stats["pending"] == 1
    assert stats["sent"] == 1
    assert stats["paid"] == 2
    assert stats["failed"] == 1
    assert stats["cancelled"] == 1


# Test get_conversion_rate


@pytest.mark.asyncio
async def test_get_conversion_rate_no_sent_invoices(async_session: AsyncSession):
    """Test conversion rate with no sent invoices."""
    # Create only pending invoices
    invoice = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254712345679",
        amount_cents=10000,
        description="Pending invoice",
        status="PENDING",
    )
    async_session.add(invoice)
    await async_session.commit()

    conversion_rate = await get_conversion_rate(async_session)

    assert conversion_rate == 0.0


@pytest.mark.asyncio
async def test_get_conversion_rate_all_paid(async_session: AsyncSession):
    """Test conversion rate when all sent invoices are paid."""
    invoices = [
        Invoice(
            msisdn="254712345678",
            merchant_msisdn="254712345679",
            amount_cents=10000,
            description="Paid invoice 1",
            status="PAID",
        ),
        Invoice(
            msisdn="254712345678",
            merchant_msisdn="254712345679",
            amount_cents=20000,
            description="Paid invoice 2",
            status="PAID",
        ),
    ]

    for invoice in invoices:
        async_session.add(invoice)
    await async_session.commit()

    conversion_rate = await get_conversion_rate(async_session)

    assert conversion_rate == 100.0


@pytest.mark.asyncio
async def test_get_conversion_rate_partial_conversion(async_session: AsyncSession):
    """Test conversion rate with partial conversion."""
    # Create 3 sent, 2 paid, 1 failed
    invoices = [
        Invoice(
            msisdn="254712345678",
            merchant_msisdn="254712345679",
            amount_cents=10000,
            description="Sent invoice",
            status="SENT",
        ),
        Invoice(
            msisdn="254712345678",
            merchant_msisdn="254712345679",
            amount_cents=20000,
            description="Paid invoice 1",
            status="PAID",
        ),
        Invoice(
            msisdn="254712345678",
            merchant_msisdn="254712345679",
            amount_cents=30000,
            description="Paid invoice 2",
            status="PAID",
        ),
        Invoice(
            msisdn="254712345678",
            merchant_msisdn="254712345679",
            amount_cents=40000,
            description="Failed invoice",
            status="FAILED",
        ),
    ]

    for invoice in invoices:
        async_session.add(invoice)
    await async_session.commit()

    conversion_rate = await get_conversion_rate(async_session)

    # 2 paid out of 4 sent/paid/failed = 50%
    assert conversion_rate == 50.0


@pytest.mark.asyncio
async def test_get_conversion_rate_excludes_pending(async_session: AsyncSession):
    """Test that conversion rate excludes pending invoices."""
    invoices = [
        Invoice(
            msisdn="254712345678",
            merchant_msisdn="254712345679",
            amount_cents=10000,
            description="Pending invoice 1",
            status="PENDING",
        ),
        Invoice(
            msisdn="254712345678",
            merchant_msisdn="254712345679",
            amount_cents=20000,
            description="Pending invoice 2",
            status="PENDING",
        ),
        Invoice(
            msisdn="254712345678",
            merchant_msisdn="254712345679",
            amount_cents=30000,
            description="Paid invoice",
            status="PAID",
        ),
    ]

    for invoice in invoices:
        async_session.add(invoice)
    await async_session.commit()

    conversion_rate = await get_conversion_rate(async_session)

    # Only 1 sent (PAID), 1 paid out of 1 = 100%
    assert conversion_rate == 100.0


# Test get_average_payment_time


@pytest.mark.asyncio
async def test_get_average_payment_time_no_payments(async_session: AsyncSession):
    """Test average payment time with no paid invoices."""
    # Create a pending invoice
    invoice = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254712345679",
        amount_cents=10000,
        description="Pending invoice",
        status="PENDING",
    )
    async_session.add(invoice)
    await async_session.commit()

    avg_time = await get_average_payment_time(async_session)

    assert avg_time is None


@pytest.mark.asyncio
async def test_get_average_payment_time_single_payment(async_session: AsyncSession):
    """Test average payment time with a single payment."""
    # Create invoice
    invoice = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254712345679",
        amount_cents=10000,
        description="Paid invoice",
        status="PAID",
    )
    async_session.add(invoice)
    await async_session.commit()

    # Create payment with 60 second delay
    base_time = datetime.utcnow()
    payment = Payment(
        invoice_id=invoice.id,
        method="MPESA_STK",
        status="SUCCESS",
        amount_cents=10000,
        idempotency_key=str(uuid.uuid4()),
        created_at=base_time,
        updated_at=base_time + timedelta(seconds=60),
    )
    async_session.add(payment)
    await async_session.commit()

    avg_time = await get_average_payment_time(async_session)

    assert avg_time is not None
    assert abs(avg_time - 60.0) < 1.0  # Allow 1 second tolerance


@pytest.mark.asyncio
async def test_get_average_payment_time_multiple_payments(async_session: AsyncSession):
    """Test average payment time with multiple payments."""
    base_time = datetime.utcnow()

    # Create invoices and payments with different times
    payment_times = [30, 60, 90]  # seconds
    for i, delay in enumerate(payment_times):
        invoice = Invoice(
            msisdn="254712345678",
            merchant_msisdn="254712345679",
            amount_cents=10000 * (i + 1),
            description=f"Paid invoice {i + 1}",
            status="PAID",
        )
        async_session.add(invoice)
        await async_session.commit()

        payment = Payment(
            invoice_id=invoice.id,
            method="MPESA_STK",
            status="SUCCESS",
            amount_cents=invoice.amount_cents,
            idempotency_key=str(uuid.uuid4()),
            created_at=base_time,
            updated_at=base_time + timedelta(seconds=delay),
        )
        async_session.add(payment)
        await async_session.commit()

    avg_time = await get_average_payment_time(async_session)

    expected_avg = sum(payment_times) / len(payment_times)  # 60 seconds
    assert avg_time is not None
    assert abs(avg_time - expected_avg) < 1.0  # Allow 1 second tolerance


@pytest.mark.asyncio
async def test_get_average_payment_time_excludes_failed_payments(
    async_session: AsyncSession,
):
    """Test that average payment time excludes failed payments."""
    base_time = datetime.utcnow()

    # Create successful payment
    invoice1 = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254712345679",
        amount_cents=10000,
        description="Paid invoice",
        status="PAID",
    )
    async_session.add(invoice1)
    await async_session.commit()

    payment1 = Payment(
        invoice_id=invoice1.id,
        method="MPESA_STK",
        status="SUCCESS",
        amount_cents=10000,
        idempotency_key=str(uuid.uuid4()),
        created_at=base_time,
        updated_at=base_time + timedelta(seconds=60),
    )
    async_session.add(payment1)
    await async_session.commit()

    # Create failed payment
    invoice2 = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254712345679",
        amount_cents=20000,
        description="Failed invoice",
        status="FAILED",
    )
    async_session.add(invoice2)
    await async_session.commit()

    payment2 = Payment(
        invoice_id=invoice2.id,
        method="MPESA_STK",
        status="FAILED",
        amount_cents=20000,
        idempotency_key=str(uuid.uuid4()),
        created_at=base_time,
        updated_at=base_time + timedelta(seconds=120),
    )
    async_session.add(payment2)
    await async_session.commit()

    avg_time = await get_average_payment_time(async_session)

    # Should only include the successful payment (60 seconds)
    assert avg_time is not None
    assert abs(avg_time - 60.0) < 1.0  # Allow 1 second tolerance


@pytest.mark.asyncio
async def test_get_average_payment_time_excludes_unpaid_invoices(
    async_session: AsyncSession,
):
    """Test that average payment time excludes unpaid invoices."""
    base_time = datetime.utcnow()

    # Create paid invoice with payment
    invoice1 = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254712345679",
        amount_cents=10000,
        description="Paid invoice",
        status="PAID",
    )
    async_session.add(invoice1)
    await async_session.commit()

    payment1 = Payment(
        invoice_id=invoice1.id,
        method="MPESA_STK",
        status="SUCCESS",
        amount_cents=10000,
        idempotency_key=str(uuid.uuid4()),
        created_at=base_time,
        updated_at=base_time + timedelta(seconds=60),
    )
    async_session.add(payment1)
    await async_session.commit()

    # Create sent invoice without payment
    invoice2 = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254712345679",
        amount_cents=20000,
        description="Sent invoice",
        status="SENT",
    )
    async_session.add(invoice2)
    await async_session.commit()

    avg_time = await get_average_payment_time(async_session)

    # Should only include the paid invoice (60 seconds)
    assert avg_time is not None
    assert abs(avg_time - 60.0) < 1.0  # Allow 1 second tolerance