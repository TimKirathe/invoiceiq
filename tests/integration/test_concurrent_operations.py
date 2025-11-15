"""
Integration tests for concurrent operations (Phase 12).

Tests concurrent invoice creation and STK Push operations to verify:
1. Race conditions are properly handled
2. Database integrity is maintained under concurrent load
3. Idempotency works correctly with concurrent requests
4. All concurrent operations complete successfully

All tests use asyncio.gather() to create true concurrent operations.
"""

import asyncio
from typing import List
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.db import Base, get_db
from src.app.main import app
from src.app.models import Invoice, Payment


# Test database URL (in-memory SQLite)
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

# Create test engine and session
test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

test_session_factory = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def override_get_db():
    """Override get_db dependency to use test database."""
    async with test_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


# Override the dependency
app.dependency_overrides[get_db] = override_get_db


@pytest.fixture
async def test_db():
    """
    Create test database tables and yield session.

    Cleans up after each test by dropping all tables.
    """
    # Create tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield

    # Drop tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def client():
    """Create async test client."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
async def db_session():
    """Provide a database session for tests."""
    async with test_session_factory() as session:
        yield session


@pytest.mark.asyncio
async def test_concurrent_invoice_creation(
    client: AsyncClient, db_session: AsyncSession, test_db
) -> None:
    """
    Test creating multiple invoices simultaneously to check for race conditions.

    Creates 10 invoices concurrently using asyncio.gather() and verifies:
    - All invoices are created successfully
    - All invoices have unique IDs
    - Database integrity is maintained
    - No data corruption or lost records
    """
    # Mock WhatsApp API to succeed
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_response = AsyncMock(spec=Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"messages": [{"id": "wamid.test"}]}
        mock_response.raise_for_status = AsyncMock()
        mock_post.return_value = mock_response

        # Create 10 concurrent invoice creation tasks
        tasks = []
        for i in range(10):
            invoice_data = {
                "msisdn": f"25471234567{i}",
                "customer_name": f"Customer {i}",
                "merchant_msisdn": "254798765432",
                "amount_cents": 10000 + (i * 1000),
                "description": f"Concurrent test invoice {i}",
            }
            task = client.post("/invoices", json=invoice_data)
            tasks.append(task)

        # Execute all requests concurrently
        responses = await asyncio.gather(*tasks)

        # Verify all requests succeeded
        assert len(responses) == 10
        for response in responses:
            assert response.status_code == 201

        # Extract invoice IDs
        invoice_ids = [response.json()["id"] for response in responses]

        # Verify all IDs are unique
        assert len(set(invoice_ids)) == 10, "Some invoice IDs are duplicated"

        # Verify all invoices exist in database with correct data
        for i, invoice_id in enumerate(invoice_ids):
            result = await db_session.execute(
                select(Invoice).where(Invoice.id == invoice_id)
            )
            invoice = result.scalar_one_or_none()

            assert invoice is not None, f"Invoice {invoice_id} not found in database"
            assert invoice.msisdn == f"25471234567{i}"
            assert invoice.customer_name == f"Customer {i}"
            assert invoice.amount_cents == 10000 + (i * 1000)
            assert invoice.status == "SENT"

        # Verify total count in database
        count_result = await db_session.execute(select(Invoice))
        all_invoices = count_result.scalars().all()
        assert len(all_invoices) == 10


@pytest.mark.asyncio
async def test_concurrent_stk_push_requests(
    client: AsyncClient, db_session: AsyncSession, test_db
) -> None:
    """
    Test concurrent STK Push initiations to verify idempotency under load.

    Creates 5 invoices, then initiates STK Push for all concurrently.
    Verifies that all payments are created with unique IDs and correct data.
    """
    # Create invoices first
    invoices: List[Invoice] = []
    for i in range(5):
        invoice = Invoice(
            id=str(uuid4()),
            customer_name=f"STK Customer {i}",
            msisdn=f"25478765432{i}",
            merchant_msisdn="254798765432",
            amount_cents=5000 + (i * 500),
            currency="KES",
            description=f"Concurrent STK test {i}",
            status="SENT",
        )
        db_session.add(invoice)
        invoices.append(invoice)

    await db_session.commit()
    for invoice in invoices:
        await db_session.refresh(invoice)

    # Mock M-PESA API
    mock_oauth_response = {"access_token": "test_token", "expires_in": "3600"}
    mock_stk_responses = [
        {
            "MerchantRequestID": f"MR-{i}",
            "CheckoutRequestID": f"ws_CO_{i}",
            "ResponseCode": "0",
            "ResponseDescription": "Success",
            "CustomerMessage": "Success",
        }
        for i in range(5)
    ]

    with patch("src.app.services.mpesa.httpx.AsyncClient") as mock_client:
        # Setup OAuth mock
        mock_oauth_resp = AsyncMock(spec=Response)
        mock_oauth_resp.json.return_value = mock_oauth_response
        mock_oauth_resp.raise_for_status = AsyncMock()

        # Setup STK Push mocks (return different responses)
        stk_call_count = [0]

        def get_stk_response(*args, **kwargs):
            mock_stk_resp = AsyncMock(spec=Response)
            mock_stk_resp.json.return_value = mock_stk_responses[stk_call_count[0]]
            mock_stk_resp.raise_for_status = AsyncMock()
            stk_call_count[0] += 1
            return mock_stk_resp

        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_oauth_resp
        mock_client_instance.post.side_effect = get_stk_response
        mock_client.return_value.__aenter__.return_value = mock_client_instance

        # Create concurrent STK Push requests
        tasks = []
        for i, invoice in enumerate(invoices):
            task = client.post(
                "/payments/stk/initiate",
                json={
                    "invoice_id": invoice.id,
                    "idempotency_key": f"concurrent-stk-{i}",
                },
            )
            tasks.append(task)

        # Execute all STK requests concurrently
        responses = await asyncio.gather(*tasks)

        # Verify all requests succeeded
        assert len(responses) == 5
        for response in responses:
            assert response.status_code == 200

        # Extract payment IDs
        payment_ids = [response.json()["id"] for response in responses]

        # Verify all payment IDs are unique
        assert len(set(payment_ids)) == 5, "Some payment IDs are duplicated"

        # Verify all payments exist in database
        for i, payment_id in enumerate(payment_ids):
            result = await db_session.execute(
                select(Payment).where(Payment.id == payment_id)
            )
            payment = result.scalar_one_or_none()

            assert payment is not None, f"Payment {payment_id} not found in database"
            assert payment.invoice_id == invoices[i].id
            assert payment.status == "INITIATED"
            assert payment.idempotency_key == f"concurrent-stk-{i}"

        # Verify total payment count
        count_result = await db_session.execute(select(Payment))
        all_payments = count_result.scalars().all()
        assert len(all_payments) == 5


@pytest.mark.asyncio
async def test_concurrent_duplicate_stk_requests_idempotency(
    client: AsyncClient, db_session: AsyncSession, test_db
) -> None:
    """
    Test idempotency with concurrent duplicate STK requests.

    Sends 10 concurrent STK requests with the same idempotency key
    and verifies only one payment is created.
    """
    # Create single invoice
    invoice = Invoice(
        id=str(uuid4()),
        customer_name="Idempotency Test",
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=10000,
        currency="KES",
        description="Concurrent idempotency test",
        status="SENT",
    )

    db_session.add(invoice)
    await db_session.commit()
    await db_session.refresh(invoice)

    # Mock M-PESA API
    mock_oauth_response = {"access_token": "test_token", "expires_in": "3600"}
    mock_stk_response = {
        "MerchantRequestID": "MR-IDEM",
        "CheckoutRequestID": "ws_CO_IDEM",
        "ResponseCode": "0",
        "ResponseDescription": "Success",
        "CustomerMessage": "Success",
    }

    with patch("src.app.services.mpesa.httpx.AsyncClient") as mock_client:
        mock_oauth_resp = AsyncMock(spec=Response)
        mock_oauth_resp.json.return_value = mock_oauth_response
        mock_oauth_resp.raise_for_status = AsyncMock()

        mock_stk_resp = AsyncMock(spec=Response)
        mock_stk_resp.json.return_value = mock_stk_response
        mock_stk_resp.raise_for_status = AsyncMock()

        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_oauth_resp
        mock_client_instance.post.return_value = mock_stk_resp
        mock_client.return_value.__aenter__.return_value = mock_client_instance

        # Create 10 concurrent requests with SAME idempotency key
        same_idempotency_key = f"duplicate-test-{invoice.id}"
        tasks = []
        for _ in range(10):
            task = client.post(
                "/payments/stk/initiate",
                json={
                    "invoice_id": invoice.id,
                    "idempotency_key": same_idempotency_key,
                },
            )
            tasks.append(task)

        # Execute all requests concurrently
        responses = await asyncio.gather(*tasks)

        # Verify all requests succeeded (returned 200)
        assert len(responses) == 10
        for response in responses:
            assert response.status_code == 200

        # Extract payment IDs - they should all be the same due to idempotency
        payment_ids = [response.json()["id"] for response in responses]

        # Verify all responses returned the SAME payment ID
        assert len(set(payment_ids)) == 1, "Idempotency failed - multiple payments created"

        # Verify only ONE payment exists in database
        result = await db_session.execute(
            select(Payment).where(Payment.idempotency_key == same_idempotency_key)
        )
        payments = result.scalars().all()
        assert len(payments) == 1, f"Expected 1 payment, found {len(payments)}"

        # Verify payment data
        payment = payments[0]
        assert payment.invoice_id == invoice.id
        assert payment.status == "INITIATED"


@pytest.mark.asyncio
async def test_concurrent_mixed_operations(
    client: AsyncClient, db_session: AsyncSession, test_db
) -> None:
    """
    Test mixed concurrent operations: invoice creation + STK Push.

    Creates 5 invoices concurrently, then immediately initiates
    STK Push for all of them concurrently.
    """
    # Mock WhatsApp API
    with patch("httpx.AsyncClient.post") as mock_whatsapp:
        mock_wa_response = AsyncMock(spec=Response)
        mock_wa_response.status_code = 200
        mock_wa_response.json.return_value = {"messages": [{"id": "wamid.test"}]}
        mock_wa_response.raise_for_status = AsyncMock()
        mock_whatsapp.return_value = mock_wa_response

        # Create invoices concurrently
        invoice_tasks = []
        for i in range(5):
            task = client.post(
                "/invoices",
                json={
                    "msisdn": f"25470000000{i}",
                    "customer_name": f"Mixed Test {i}",
                    "merchant_msisdn": "254798765432",
                    "amount_cents": 15000 + (i * 1000),
                    "description": f"Mixed concurrent test {i}",
                },
            )
            invoice_tasks.append(task)

        invoice_responses = await asyncio.gather(*invoice_tasks)

        # Verify all invoices created
        assert len(invoice_responses) == 5
        invoice_ids = [resp.json()["id"] for resp in invoice_responses]

    # Mock M-PESA API for STK Push
    mock_oauth_response = {"access_token": "test_token", "expires_in": "3600"}

    with patch("src.app.services.mpesa.httpx.AsyncClient") as mock_mpesa:
        mock_oauth_resp = AsyncMock(spec=Response)
        mock_oauth_resp.json.return_value = mock_oauth_response
        mock_oauth_resp.raise_for_status = AsyncMock()

        stk_call_count = [0]

        def get_stk_response(*args, **kwargs):
            mock_stk_resp = AsyncMock(spec=Response)
            mock_stk_resp.json.return_value = {
                "MerchantRequestID": f"MR-MIXED-{stk_call_count[0]}",
                "CheckoutRequestID": f"ws_CO_MIXED_{stk_call_count[0]}",
                "ResponseCode": "0",
                "ResponseDescription": "Success",
                "CustomerMessage": "Success",
            }
            mock_stk_resp.raise_for_status = AsyncMock()
            stk_call_count[0] += 1
            return mock_stk_resp

        mock_mpesa_instance = AsyncMock()
        mock_mpesa_instance.get.return_value = mock_oauth_resp
        mock_mpesa_instance.post.side_effect = get_stk_response
        mock_mpesa.return_value.__aenter__.return_value = mock_mpesa_instance

        # Initiate STK Push for all invoices concurrently
        stk_tasks = []
        for i, invoice_id in enumerate(invoice_ids):
            task = client.post(
                "/payments/stk/initiate",
                json={
                    "invoice_id": invoice_id,
                    "idempotency_key": f"mixed-stk-{i}",
                },
            )
            stk_tasks.append(task)

        stk_responses = await asyncio.gather(*stk_tasks)

        # Verify all STK requests succeeded
        assert len(stk_responses) == 5
        for response in stk_responses:
            assert response.status_code == 200

        # Verify database integrity
        # Check invoices
        invoice_result = await db_session.execute(select(Invoice))
        invoices = invoice_result.scalars().all()
        assert len(invoices) == 5

        # Check payments
        payment_result = await db_session.execute(select(Payment))
        payments = payment_result.scalars().all()
        assert len(payments) == 5

        # Verify each payment is linked to correct invoice
        for payment in payments:
            assert payment.invoice_id in invoice_ids
            assert payment.status == "INITIATED"


@pytest.mark.asyncio
async def test_concurrent_operations_database_integrity(
    client: AsyncClient, db_session: AsyncSession, test_db
) -> None:
    """
    Test database integrity under high concurrent load.

    Creates 20 concurrent operations (invoices + payments) and verifies
    no data corruption, constraint violations, or lost updates.
    """
    # Create 10 invoices first for STK tests
    existing_invoices: List[Invoice] = []
    for i in range(10):
        invoice = Invoice(
            id=str(uuid4()),
            customer_name=f"Integrity Test {i}",
            msisdn=f"25471111111{i}",
            merchant_msisdn="254798765432",
            amount_cents=20000 + (i * 500),
            currency="KES",
            description=f"Database integrity test {i}",
            status="SENT",
        )
        db_session.add(invoice)
        existing_invoices.append(invoice)

    await db_session.commit()
    for invoice in existing_invoices:
        await db_session.refresh(invoice)

    # Mock all APIs
    with patch("httpx.AsyncClient.post") as mock_whatsapp:
        mock_wa_response = AsyncMock(spec=Response)
        mock_wa_response.status_code = 200
        mock_wa_response.json.return_value = {"messages": [{"id": "wamid.test"}]}
        mock_wa_response.raise_for_status = AsyncMock()
        mock_whatsapp.return_value = mock_wa_response

        with patch("src.app.services.mpesa.httpx.AsyncClient") as mock_mpesa:
            mock_oauth_resp = AsyncMock(spec=Response)
            mock_oauth_resp.json.return_value = {
                "access_token": "test_token",
                "expires_in": "3600",
            }
            mock_oauth_resp.raise_for_status = AsyncMock()

            stk_call_count = [0]

            def get_stk_response(*args, **kwargs):
                mock_stk_resp = AsyncMock(spec=Response)
                mock_stk_resp.json.return_value = {
                    "MerchantRequestID": f"MR-INT-{stk_call_count[0]}",
                    "CheckoutRequestID": f"ws_CO_INT_{stk_call_count[0]}",
                    "ResponseCode": "0",
                    "ResponseDescription": "Success",
                    "CustomerMessage": "Success",
                }
                mock_stk_resp.raise_for_status = AsyncMock()
                stk_call_count[0] += 1
                return mock_stk_resp

            mock_mpesa_instance = AsyncMock()
            mock_mpesa_instance.get.return_value = mock_oauth_resp
            mock_mpesa_instance.post.side_effect = get_stk_response
            mock_mpesa.return_value.__aenter__.return_value = mock_mpesa_instance

            # Create 20 concurrent operations: 10 new invoices + 10 STK pushes
            all_tasks = []

            # 10 invoice creation tasks
            for i in range(10):
                task = client.post(
                    "/invoices",
                    json={
                        "msisdn": f"25472222222{i}",
                        "customer_name": f"New Invoice {i}",
                        "merchant_msisdn": "254798765432",
                        "amount_cents": 25000 + (i * 500),
                        "description": f"Concurrent integrity invoice {i}",
                    },
                )
                all_tasks.append(task)

            # 10 STK Push tasks
            for i, invoice in enumerate(existing_invoices):
                task = client.post(
                    "/payments/stk/initiate",
                    json={
                        "invoice_id": invoice.id,
                        "idempotency_key": f"integrity-stk-{i}",
                    },
                )
                all_tasks.append(task)

            # Execute all 20 operations concurrently
            all_responses = await asyncio.gather(*all_tasks)

            # Verify all operations succeeded
            assert len(all_responses) == 20
            for response in all_responses:
                assert response.status_code in [200, 201]

            # Verify database integrity
            # Should have 20 total invoices (10 existing + 10 new)
            invoice_result = await db_session.execute(select(Invoice))
            all_invoices = invoice_result.scalars().all()
            assert len(all_invoices) == 20

            # Should have 10 payments (for existing invoices)
            payment_result = await db_session.execute(select(Payment))
            all_payments = payment_result.scalars().all()
            assert len(all_payments) == 10

            # Verify all invoice IDs are unique
            invoice_ids = [inv.id for inv in all_invoices]
            assert len(set(invoice_ids)) == 20

            # Verify all payment IDs are unique
            payment_ids = [pay.id for pay in all_payments]
            assert len(set(payment_ids)) == 10

            # Verify all payments link to valid invoices
            for payment in all_payments:
                assert payment.invoice_id in invoice_ids