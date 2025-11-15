"""
Unit tests for SQLAlchemy models.

Tests model instantiation, field validation, constraints, relationships,
and database operations using an in-memory SQLite database.
"""

import uuid
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from src.app.db import Base
from src.app.models import Invoice, Payment, MessageLog


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


# Invoice Model Tests


@pytest.mark.asyncio
async def test_invoice_creation(async_session: AsyncSession):
    """Test creating a basic invoice."""
    invoice = Invoice(
        id=str(uuid.uuid4()),
        customer_name="John Doe",
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=10000,  # 100 KES
        currency="KES",
        description="Test invoice",
        status="PENDING",
    )

    async_session.add(invoice)
    await async_session.commit()

    # Query the invoice back
    result = await async_session.execute(
        select(Invoice).where(Invoice.id == invoice.id)
    )
    retrieved = result.scalars().first()

    assert retrieved is not None
    assert retrieved.customer_name == "John Doe"
    assert retrieved.msisdn == "254712345678"
    assert retrieved.merchant_msisdn == "254798765432"
    assert retrieved.amount_cents == 10000
    assert retrieved.status == "PENDING"


@pytest.mark.asyncio
async def test_invoice_auto_uuid(async_session: AsyncSession):
    """Test that invoice ID is auto-generated if not provided."""
    invoice = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=10000,
        description="Test invoice",
        status="PENDING",
    )

    async_session.add(invoice)
    await async_session.commit()

    assert invoice.id is not None
    assert len(invoice.id) == 36  # UUID length


@pytest.mark.asyncio
async def test_invoice_timestamps(async_session: AsyncSession):
    """Test that timestamps are auto-populated."""
    invoice = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=10000,
        description="Test invoice",
        status="PENDING",
    )

    async_session.add(invoice)
    await async_session.commit()

    assert invoice.created_at is not None
    assert invoice.updated_at is not None
    assert isinstance(invoice.created_at, datetime)
    assert isinstance(invoice.updated_at, datetime)


@pytest.mark.asyncio
async def test_invoice_nullable_fields(async_session: AsyncSession):
    """Test that optional fields can be null."""
    invoice = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=10000,
        description="Test invoice",
        status="PENDING",
        # customer_name, pay_ref, pay_link are optional
    )

    async_session.add(invoice)
    await async_session.commit()

    assert invoice.customer_name is None
    assert invoice.pay_ref is None
    assert invoice.pay_link is None


@pytest.mark.asyncio
async def test_invoice_required_fields(async_session: AsyncSession):
    """Test that required fields must be provided."""
    # Missing msisdn should fail at database commit time
    invoice = Invoice(
        amount_cents=10000,
        description="Test invoice",
        status="PENDING",
    )
    async_session.add(invoice)

    # This should fail due to NOT NULL constraint
    with pytest.raises(IntegrityError):
        await async_session.commit()


@pytest.mark.asyncio
async def test_invoice_status_constraint(async_session: AsyncSession):
    """Test that status must be one of the allowed values."""
    invoice = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=10000,
        description="Test invoice",
        status="INVALID_STATUS",
    )

    async_session.add(invoice)

    # This should fail due to CHECK constraint
    with pytest.raises(IntegrityError):
        await async_session.commit()


@pytest.mark.asyncio
async def test_invoice_amount_constraint(async_session: AsyncSession):
    """Test that amount must be at least 100 cents (1 KES)."""
    invoice = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=50,  # Less than minimum
        description="Test invoice",
        status="PENDING",
    )

    async_session.add(invoice)

    # This should fail due to CHECK constraint
    with pytest.raises(IntegrityError):
        await async_session.commit()


@pytest.mark.asyncio
async def test_invoice_msisdn_length_constraint(async_session: AsyncSession):
    """Test that MSISDN must be exactly 12 characters."""
    invoice = Invoice(
        msisdn="12345",  # Too short
        merchant_msisdn="254798765432",
        amount_cents=10000,
        description="Test invoice",
        status="PENDING",
    )

    async_session.add(invoice)

    # This should fail due to CHECK constraint
    with pytest.raises(IntegrityError):
        await async_session.commit()


@pytest.mark.asyncio
async def test_invoice_description_length_constraint(async_session: AsyncSession):
    """Test that description must be between 3 and 120 characters."""
    invoice = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=10000,
        description="ab",  # Too short
        status="PENDING",
    )

    async_session.add(invoice)

    # This should fail due to CHECK constraint
    with pytest.raises(IntegrityError):
        await async_session.commit()


# Payment Model Tests


@pytest.mark.asyncio
async def test_payment_creation(async_session: AsyncSession):
    """Test creating a payment."""
    # First create an invoice
    invoice = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=10000,
        description="Test invoice",
        status="PENDING",
    )
    async_session.add(invoice)
    await async_session.commit()

    # Create a payment
    payment = Payment(
        invoice_id=invoice.id,
        method="MPESA_STK",
        status="INITIATED",
        amount_cents=10000,
        idempotency_key=str(uuid.uuid4()),
    )

    async_session.add(payment)
    await async_session.commit()

    # Query the payment back
    result = await async_session.execute(
        select(Payment).where(Payment.id == payment.id)
    )
    retrieved = result.scalars().first()

    assert retrieved is not None
    assert retrieved.invoice_id == invoice.id
    assert retrieved.method == "MPESA_STK"
    assert retrieved.status == "INITIATED"
    assert retrieved.amount_cents == 10000


@pytest.mark.asyncio
async def test_payment_idempotency_key_unique(async_session: AsyncSession):
    """Test that idempotency_key must be unique."""
    # Create an invoice
    invoice = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=10000,
        description="Test invoice",
        status="PENDING",
    )
    async_session.add(invoice)
    await async_session.commit()

    idempotency_key = str(uuid.uuid4())

    # Create first payment
    payment1 = Payment(
        invoice_id=invoice.id,
        method="MPESA_STK",
        status="INITIATED",
        amount_cents=10000,
        idempotency_key=idempotency_key,
    )
    async_session.add(payment1)
    await async_session.commit()

    # Try to create second payment with same idempotency_key
    payment2 = Payment(
        invoice_id=invoice.id,
        method="MPESA_STK",
        status="INITIATED",
        amount_cents=10000,
        idempotency_key=idempotency_key,
    )
    async_session.add(payment2)

    # This should fail due to UNIQUE constraint
    with pytest.raises(IntegrityError):
        await async_session.commit()


@pytest.mark.asyncio
async def test_payment_status_constraint(async_session: AsyncSession):
    """Test that payment status must be one of the allowed values."""
    # Create an invoice
    invoice = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=10000,
        description="Test invoice",
        status="PENDING",
    )
    async_session.add(invoice)
    await async_session.commit()

    # Create payment with invalid status
    payment = Payment(
        invoice_id=invoice.id,
        method="MPESA_STK",
        status="INVALID_STATUS",
        amount_cents=10000,
        idempotency_key=str(uuid.uuid4()),
    )
    async_session.add(payment)

    # This should fail due to CHECK constraint
    with pytest.raises(IntegrityError):
        await async_session.commit()


@pytest.mark.asyncio
async def test_payment_method_constraint(async_session: AsyncSession):
    """Test that payment method must be one of the allowed values."""
    # Create an invoice
    invoice = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=10000,
        description="Test invoice",
        status="PENDING",
    )
    async_session.add(invoice)
    await async_session.commit()

    # Create payment with invalid method
    payment = Payment(
        invoice_id=invoice.id,
        method="INVALID_METHOD",
        status="INITIATED",
        amount_cents=10000,
        idempotency_key=str(uuid.uuid4()),
    )
    async_session.add(payment)

    # This should fail due to CHECK constraint
    with pytest.raises(IntegrityError):
        await async_session.commit()


# MessageLog Model Tests


@pytest.mark.asyncio
async def test_message_log_creation(async_session: AsyncSession):
    """Test creating a message log entry."""
    # Create an invoice
    invoice = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=10000,
        description="Test invoice",
        status="PENDING",
    )
    async_session.add(invoice)
    await async_session.commit()

    # Create a message log
    message_log = MessageLog(
        invoice_id=invoice.id,
        channel="WHATSAPP",
        direction="OUT",
        event="SENT",
        payload={"message": "Invoice sent"},
    )

    async_session.add(message_log)
    await async_session.commit()

    # Query the message log back
    result = await async_session.execute(
        select(MessageLog).where(MessageLog.id == message_log.id)
    )
    retrieved = result.scalars().first()

    assert retrieved is not None
    assert retrieved.invoice_id == invoice.id
    assert retrieved.channel == "WHATSAPP"
    assert retrieved.direction == "OUT"
    assert retrieved.event == "SENT"
    assert retrieved.payload == {"message": "Invoice sent"}


@pytest.mark.asyncio
async def test_message_log_nullable_invoice(async_session: AsyncSession):
    """Test that message log can exist without an invoice."""
    message_log = MessageLog(
        channel="SMS",
        direction="IN",
        event="RECEIVED",
        payload={"message": "General message"},
    )

    async_session.add(message_log)
    await async_session.commit()

    assert message_log.invoice_id is None


@pytest.mark.asyncio
async def test_message_log_channel_constraint(async_session: AsyncSession):
    """Test that channel must be one of the allowed values."""
    message_log = MessageLog(
        channel="INVALID_CHANNEL",
        direction="OUT",
    )

    async_session.add(message_log)

    # This should fail due to CHECK constraint
    with pytest.raises(IntegrityError):
        await async_session.commit()


@pytest.mark.asyncio
async def test_message_log_direction_constraint(async_session: AsyncSession):
    """Test that direction must be one of the allowed values."""
    message_log = MessageLog(
        channel="WHATSAPP",
        direction="INVALID_DIRECTION",
    )

    async_session.add(message_log)

    # This should fail due to CHECK constraint
    with pytest.raises(IntegrityError):
        await async_session.commit()


# Relationship Tests


@pytest.mark.asyncio
async def test_invoice_payment_relationship(async_session: AsyncSession):
    """Test the relationship between Invoice and Payment."""
    # Create an invoice
    invoice = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=10000,
        description="Test invoice",
        status="PENDING",
    )
    async_session.add(invoice)
    await async_session.commit()

    invoice_id = invoice.id

    # Create payments
    payment1 = Payment(
        invoice_id=invoice_id,
        method="MPESA_STK",
        status="INITIATED",
        amount_cents=10000,
        idempotency_key=str(uuid.uuid4()),
    )
    payment2 = Payment(
        invoice_id=invoice_id,
        method="MPESA_STK",
        status="SUCCESS",
        amount_cents=10000,
        idempotency_key=str(uuid.uuid4()),
    )
    async_session.add(payment1)
    async_session.add(payment2)
    await async_session.commit()

    # Query payments for this invoice
    result = await async_session.execute(
        select(Payment).where(Payment.invoice_id == invoice_id)
    )
    payments = result.scalars().all()

    # Verify relationship via invoice_id
    assert len(payments) == 2
    assert all(p.invoice_id == invoice_id for p in payments)


@pytest.mark.asyncio
async def test_invoice_message_relationship(async_session: AsyncSession):
    """Test the relationship between Invoice and MessageLog."""
    # Create an invoice
    invoice = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=10000,
        description="Test invoice",
        status="PENDING",
    )
    async_session.add(invoice)
    await async_session.commit()

    invoice_id = invoice.id

    # Create message logs
    message1 = MessageLog(
        invoice_id=invoice_id,
        channel="WHATSAPP",
        direction="OUT",
        event="SENT",
    )
    message2 = MessageLog(
        invoice_id=invoice_id,
        channel="SMS",
        direction="OUT",
        event="DELIVERED",
    )
    async_session.add(message1)
    async_session.add(message2)
    await async_session.commit()

    # Query messages for this invoice
    result = await async_session.execute(
        select(MessageLog).where(MessageLog.invoice_id == invoice_id)
    )
    messages = result.scalars().all()

    # Verify relationship via invoice_id
    assert len(messages) == 2
    assert all(m.invoice_id == invoice_id for m in messages)


@pytest.mark.asyncio
async def test_payment_invoice_backref(async_session: AsyncSession):
    """Test the back reference from Payment to Invoice."""
    # Create an invoice
    invoice = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=10000,
        description="Test invoice",
        status="PENDING",
    )
    async_session.add(invoice)
    await async_session.commit()

    # Create a payment
    payment = Payment(
        invoice_id=invoice.id,
        method="MPESA_STK",
        status="INITIATED",
        amount_cents=10000,
        idempotency_key=str(uuid.uuid4()),
    )
    async_session.add(payment)
    await async_session.commit()

    # Query payment and access invoice through relationship
    result = await async_session.execute(
        select(Payment).where(Payment.id == payment.id)
    )
    retrieved_payment = result.scalars().first()

    # Access the back reference
    assert retrieved_payment.invoice is not None
    assert retrieved_payment.invoice.id == invoice.id
    assert retrieved_payment.invoice.msisdn == "254712345678"


@pytest.mark.asyncio
async def test_cascade_delete_invoice_payments(async_session: AsyncSession):
    """Test that deleting an invoice cascades to payments."""
    # Create an invoice
    invoice = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=10000,
        description="Test invoice",
        status="PENDING",
    )
    async_session.add(invoice)
    await async_session.commit()

    # Create a payment
    payment = Payment(
        invoice_id=invoice.id,
        method="MPESA_STK",
        status="INITIATED",
        amount_cents=10000,
        idempotency_key=str(uuid.uuid4()),
    )
    async_session.add(payment)
    await async_session.commit()

    payment_id = payment.id

    # Delete the invoice
    await async_session.delete(invoice)
    await async_session.commit()

    # Check that payment is also deleted
    result = await async_session.execute(
        select(Payment).where(Payment.id == payment_id)
    )
    deleted_payment = result.scalars().first()

    assert deleted_payment is None


@pytest.mark.asyncio
async def test_cascade_delete_invoice_messages(async_session: AsyncSession):
    """Test that deleting an invoice cascades to message logs."""
    # Create an invoice
    invoice = Invoice(
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=10000,
        description="Test invoice",
        status="PENDING",
    )
    async_session.add(invoice)
    await async_session.commit()

    # Create a message log
    message = MessageLog(
        invoice_id=invoice.id,
        channel="WHATSAPP",
        direction="OUT",
        event="SENT",
    )
    async_session.add(message)
    await async_session.commit()

    message_id = message.id

    # Delete the invoice
    await async_session.delete(invoice)
    await async_session.commit()

    # Check that message is also deleted (due to delete-orphan cascade)
    result = await async_session.execute(
        select(MessageLog).where(MessageLog.id == message_id)
    )
    deleted_message = result.scalars().first()

    assert deleted_message is None


@pytest.mark.asyncio
async def test_invoice_repr():
    """Test the string representation of Invoice."""
    invoice = Invoice(
        id="test-id",
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=10000,
        description="Test invoice",
        status="PENDING",
    )

    repr_str = repr(invoice)
    assert "Invoice" in repr_str
    assert "test-id" in repr_str
    assert "254712345678" in repr_str
    assert "10000" in repr_str
    assert "PENDING" in repr_str


@pytest.mark.asyncio
async def test_payment_repr():
    """Test the string representation of Payment."""
    payment = Payment(
        id="test-payment-id",
        invoice_id="test-invoice-id",
        method="MPESA_STK",
        status="SUCCESS",
        amount_cents=10000,
        idempotency_key="test-key",
    )

    repr_str = repr(payment)
    assert "Payment" in repr_str
    assert "test-payment-id" in repr_str
    assert "test-invoice-id" in repr_str
    assert "SUCCESS" in repr_str
    assert "10000" in repr_str


@pytest.mark.asyncio
async def test_message_log_repr():
    """Test the string representation of MessageLog."""
    message = MessageLog(
        id="test-message-id",
        channel="WHATSAPP",
        direction="OUT",
        event="SENT",
    )

    repr_str = repr(message)
    assert "MessageLog" in repr_str
    assert "test-message-id" in repr_str
    assert "WHATSAPP" in repr_str
    assert "OUT" in repr_str
    assert "SENT" in repr_str