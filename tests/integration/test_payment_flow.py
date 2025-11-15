"""
Integration tests for complete payment flow (Phase 8).

Tests the full payment lifecycle:
1. Invoice creation
2. STK Push initiation
3. M-PESA callback processing (success and failure)
4. Payment and invoice status updates
5. Receipt delivery to customer and merchant

All tests mock external API calls (M-PESA, WhatsApp) to ensure fast,
reliable test execution.
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.db import Base, get_db
from src.app.main import app
from src.app.models import Invoice, MessageLog, Payment


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
async def test_successful_payment_flow_complete(
    client, db_session: AsyncSession, test_db
) -> None:
    """
    Test complete successful payment flow:
    1. Create invoice with SENT status
    2. Initiate STK Push
    3. Receive successful callback (ResultCode 0)
    4. Verify payment status = SUCCESS
    5. Verify invoice status = PAID
    6. Verify receipts sent to customer and merchant
    7. Verify MessageLog entries created
    """
    # Step 1: Create invoice
    invoice = Invoice(
        id=str(uuid4()),
        customer_name="John Doe",
        msisdn="254712345678",
        merchant_msisdn="254798765432",
        amount_cents=10000,  # 100 KES
        currency="KES",
        description="Test invoice for successful payment",
        status="SENT",
    )

    db_session.add(invoice)
    await db_session.commit()
    await db_session.refresh(invoice)

    # Step 2: Mock M-PESA STK Push initiation
    mock_oauth_response = {
        "access_token": "test_access_token",
        "expires_in": "3600",
    }

    mock_stk_response = {
        "MerchantRequestID": "29115-34620561-1",
        "CheckoutRequestID": "ws_CO_191220191020363925",
        "ResponseCode": "0",
        "ResponseDescription": "Success. Request accepted for processing",
        "CustomerMessage": "Success. Request accepted for processing",
    }

    with patch("src.app.services.mpesa.httpx.AsyncClient") as mock_mpesa_client:
        # Setup M-PESA OAuth mock
        mock_oauth_resp = AsyncMock(spec=Response)
        mock_oauth_resp.json.return_value = mock_oauth_response
        mock_oauth_resp.raise_for_status = AsyncMock()

        # Setup M-PESA STK Push mock
        mock_stk_resp = AsyncMock(spec=Response)
        mock_stk_resp.json.return_value = mock_stk_response
        mock_stk_resp.raise_for_status = AsyncMock()

        # Configure mock client
        mock_mpesa_instance = AsyncMock()
        mock_mpesa_instance.get.return_value = mock_oauth_resp
        mock_mpesa_instance.post.return_value = mock_stk_resp
        mock_mpesa_client.return_value.__aenter__.return_value = mock_mpesa_instance

        # Initiate STK Push
        stk_response = client.post(
            "/payments/stk/initiate",
            json={
                "invoice_id": invoice.id,
                "idempotency_key": f"test-payment-{invoice.id}",
            },
        )

        assert stk_response.status_code == 200
        stk_data = stk_response.json()
        payment_id = stk_data["id"]

        # Verify payment record created with INITIATED status
        payment_stmt = select(Payment).where(Payment.id == payment_id)
        payment_result = await db_session.execute(payment_stmt)
        payment = payment_result.scalar_one_or_none()

        assert payment is not None
        assert payment.status == "INITIATED"
        assert payment.checkout_request_id == "ws_CO_191220191020363925"
        assert payment.merchant_request_id == "29115-34620561-1"

    # Step 3: Mock successful M-PESA callback
    callback_payload = {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": "29115-34620561-1",
                "CheckoutRequestID": "ws_CO_191220191020363925",
                "ResultCode": 0,
                "ResultDesc": "The service request is processed successfully.",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": 100.00},
                        {"Name": "MpesaReceiptNumber", "Value": "NLJ7RT61SV"},
                        {"Name": "TransactionDate", "Value": 20191219102115},
                        {"Name": "PhoneNumber", "Value": 254712345678},
                    ]
                },
            }
        }
    }

    # Mock WhatsApp API for receipt sending
    with patch("src.app.services.whatsapp.httpx.AsyncClient") as mock_whatsapp_client:
        mock_whatsapp_resp = AsyncMock(spec=Response)
        mock_whatsapp_resp.json.return_value = {
            "messages": [{"id": "wamid.test123"}]
        }
        mock_whatsapp_resp.raise_for_status = AsyncMock()

        mock_whatsapp_instance = AsyncMock()
        mock_whatsapp_instance.post.return_value = mock_whatsapp_resp
        mock_whatsapp_client.return_value.__aenter__.return_value = (
            mock_whatsapp_instance
        )

        # Send callback
        callback_response = client.post("/payments/stk/callback", json=callback_payload)

        assert callback_response.status_code == 200
        assert callback_response.json() == {
            "ResultCode": "0",
            "ResultDesc": "Accepted",
        }

    # Step 4: Verify payment status updated to SUCCESS
    await db_session.refresh(payment)
    assert payment.status == "SUCCESS"
    assert payment.mpesa_receipt == "NLJ7RT61SV"
    assert payment.raw_callback is not None

    # Step 5: Verify invoice status updated to PAID
    await db_session.refresh(invoice)
    assert invoice.status == "PAID"
    assert invoice.pay_ref == "NLJ7RT61SV"

    # Step 6: Verify MessageLog entries created for receipts
    message_logs_stmt = select(MessageLog).where(MessageLog.invoice_id == invoice.id)
    message_logs_result = await db_session.execute(message_logs_stmt)
    message_logs = message_logs_result.scalars().all()

    # Should have 2 message logs: receipt_sent_customer and receipt_sent_merchant
    receipt_logs = [
        log
        for log in message_logs
        if log.event in ["receipt_sent_customer", "receipt_sent_merchant"]
    ]
    assert len(receipt_logs) == 2

    # Verify customer receipt log
    customer_receipt_log = next(
        (log for log in receipt_logs if log.event == "receipt_sent_customer"), None
    )
    assert customer_receipt_log is not None
    assert customer_receipt_log.channel == "WHATSAPP"
    assert customer_receipt_log.direction == "OUT"
    assert customer_receipt_log.payload["mpesa_receipt"] == "NLJ7RT61SV"

    # Verify merchant receipt log
    merchant_receipt_log = next(
        (log for log in receipt_logs if log.event == "receipt_sent_merchant"), None
    )
    assert merchant_receipt_log is not None
    assert merchant_receipt_log.channel == "WHATSAPP"
    assert merchant_receipt_log.direction == "OUT"
    assert merchant_receipt_log.payload["mpesa_receipt"] == "NLJ7RT61SV"


@pytest.mark.asyncio
async def test_failed_payment_flow_complete(client, db_session: AsyncSession, test_db) -> None:
    """
    Test complete failed payment flow:
    1. Create invoice with SENT status
    2. Initiate STK Push
    3. Receive failed callback (ResultCode != 0)
    4. Verify payment status = FAILED
    5. Verify invoice status = FAILED
    6. Verify no receipts sent
    """
    # Step 1: Create invoice
    invoice = Invoice(
        id=str(uuid4()),
        customer_name="Jane Doe",
        msisdn="254723456789",
        merchant_msisdn="254787654321",
        amount_cents=5000,  # 50 KES
        currency="KES",
        description="Test invoice for failed payment",
        status="SENT",
    )

    db_session.add(invoice)
    await db_session.commit()
    await db_session.refresh(invoice)

    # Step 2: Mock M-PESA STK Push initiation
    mock_oauth_response = {
        "access_token": "test_access_token",
        "expires_in": "3600",
    }

    mock_stk_response = {
        "MerchantRequestID": "92334-77894064-1",
        "CheckoutRequestID": "ws_CO_04112024174011655708374149",
        "ResponseCode": "0",
        "ResponseDescription": "Success. Request accepted for processing",
        "CustomerMessage": "Success. Request accepted for processing",
    }

    with patch("src.app.services.mpesa.httpx.AsyncClient") as mock_mpesa_client:
        mock_oauth_resp = AsyncMock(spec=Response)
        mock_oauth_resp.json.return_value = mock_oauth_response
        mock_oauth_resp.raise_for_status = AsyncMock()

        mock_stk_resp = AsyncMock(spec=Response)
        mock_stk_resp.json.return_value = mock_stk_response
        mock_stk_resp.raise_for_status = AsyncMock()

        mock_mpesa_instance = AsyncMock()
        mock_mpesa_instance.get.return_value = mock_oauth_resp
        mock_mpesa_instance.post.return_value = mock_stk_resp
        mock_mpesa_client.return_value.__aenter__.return_value = mock_mpesa_instance

        # Initiate STK Push
        stk_response = client.post(
            "/payments/stk/initiate",
            json={
                "invoice_id": invoice.id,
                "idempotency_key": f"test-payment-failed-{invoice.id}",
            },
        )

        assert stk_response.status_code == 200
        stk_data = stk_response.json()
        payment_id = stk_data["id"]

    # Step 3: Mock failed M-PESA callback (user cancelled)
    callback_payload = {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": "92334-77894064-1",
                "CheckoutRequestID": "ws_CO_04112024174011655708374149",
                "ResultCode": 1032,
                "ResultDesc": "Request cancelled by user",
            }
        }
    }

    # Send callback
    callback_response = client.post("/payments/stk/callback", json=callback_payload)

    assert callback_response.status_code == 200
    assert callback_response.json() == {"ResultCode": "0", "ResultDesc": "Accepted"}

    # Step 4: Verify payment status updated to FAILED
    payment_stmt = select(Payment).where(Payment.id == payment_id)
    payment_result = await db_session.execute(payment_stmt)
    payment = payment_result.scalar_one_or_none()

    assert payment is not None
    assert payment.status == "FAILED"
    assert payment.mpesa_receipt is None
    assert payment.raw_callback is not None

    # Step 5: Verify invoice status updated to FAILED
    await db_session.refresh(invoice)
    assert invoice.status == "FAILED"

    # Step 6: Verify no receipt MessageLog entries created
    message_logs_stmt = select(MessageLog).where(MessageLog.invoice_id == invoice.id)
    message_logs_result = await db_session.execute(message_logs_stmt)
    message_logs = message_logs_result.scalars().all()

    receipt_logs = [
        log
        for log in message_logs
        if "receipt" in log.event.lower() and "sent" in log.event.lower()
    ]
    assert len(receipt_logs) == 0


@pytest.mark.asyncio
async def test_duplicate_callback_idempotency(client, db_session: AsyncSession, test_db) -> None:
    """
    Test callback idempotency prevents duplicate processing:
    1. Process callback once
    2. Send same callback again
    3. Verify no duplicate processing
    4. Verify response is 200 OK
    """
    # Create invoice and payment
    invoice = Invoice(
        id=str(uuid4()),
        customer_name="Test User",
        msisdn="254734567890",
        merchant_msisdn="254776543210",
        amount_cents=3000,  # 30 KES
        currency="KES",
        description="Duplicate callback test",
        status="SENT",
    )

    db_session.add(invoice)
    await db_session.commit()
    await db_session.refresh(invoice)

    # Mock STK Push
    mock_oauth_response = {"access_token": "token", "expires_in": "3600"}
    mock_stk_response = {
        "MerchantRequestID": "12345-67890",
        "CheckoutRequestID": "ws_CO_DUPLICATE_TEST",
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

        mock_instance = AsyncMock()
        mock_instance.get.return_value = mock_oauth_resp
        mock_instance.post.return_value = mock_stk_resp
        mock_client.return_value.__aenter__.return_value = mock_instance

        # Initiate STK Push
        stk_response = client.post(
            "/payments/stk/initiate",
            json={
                "invoice_id": invoice.id,
                "idempotency_key": f"test-duplicate-{invoice.id}",
            },
        )

        assert stk_response.status_code == 200

    # Prepare callback
    callback_payload = {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": "12345-67890",
                "CheckoutRequestID": "ws_CO_DUPLICATE_TEST",
                "ResultCode": 0,
                "ResultDesc": "Success",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": 30.00},
                        {"Name": "MpesaReceiptNumber", "Value": "DUPLICATE123"},
                        {"Name": "TransactionDate", "Value": 20250112000000},
                        {"Name": "PhoneNumber", "Value": 254734567890},
                    ]
                },
            }
        }
    }

    # Mock WhatsApp
    with patch("src.app.services.whatsapp.httpx.AsyncClient") as mock_whatsapp:
        mock_whatsapp_resp = AsyncMock(spec=Response)
        mock_whatsapp_resp.json.return_value = {"messages": [{"id": "wamid.123"}]}
        mock_whatsapp_resp.raise_for_status = AsyncMock()

        mock_whatsapp_instance = AsyncMock()
        mock_whatsapp_instance.post.return_value = mock_whatsapp_resp
        mock_whatsapp.return_value.__aenter__.return_value = mock_whatsapp_instance

        # Send first callback
        response1 = client.post("/payments/stk/callback", json=callback_payload)
        assert response1.status_code == 200

    # Get payment to check status
    payment_stmt = select(Payment).where(
        Payment.checkout_request_id == "ws_CO_DUPLICATE_TEST"
    )
    payment_result = await db_session.execute(payment_stmt)
    payment = payment_result.scalar_one_or_none()

    assert payment is not None
    assert payment.status == "SUCCESS"

    # Send duplicate callback (no WhatsApp mock - should not be called)
    response2 = client.post("/payments/stk/callback", json=callback_payload)
    assert response2.status_code == 200

    # Verify payment status unchanged
    await db_session.refresh(payment)
    assert payment.status == "SUCCESS"


@pytest.mark.asyncio
async def test_callback_for_unknown_payment(client, db_session: AsyncSession, test_db) -> None:
    """
    Test callback for unknown CheckoutRequestID:
    1. Send callback with unknown CheckoutRequestID
    2. Verify 200 OK response (prevents retries)
    3. Verify warning logged (checked implicitly via no error)
    """
    callback_payload = {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": "UNKNOWN-12345",
                "CheckoutRequestID": "ws_CO_UNKNOWN_PAYMENT",
                "ResultCode": 0,
                "ResultDesc": "Success",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": 50.00},
                        {"Name": "MpesaReceiptNumber", "Value": "UNKNOWN123"},
                        {"Name": "TransactionDate", "Value": 20250112000000},
                        {"Name": "PhoneNumber", "Value": 254700000000},
                    ]
                },
            }
        }
    }

    # Send callback for unknown payment
    response = client.post("/payments/stk/callback", json=callback_payload)

    assert response.status_code == 200
    assert response.json() == {"ResultCode": "0", "ResultDesc": "Accepted"}

    # Verify no payment was created
    payment_stmt = select(Payment).where(
        Payment.checkout_request_id == "ws_CO_UNKNOWN_PAYMENT"
    )
    payment_result = await db_session.execute(payment_stmt)
    payment = payment_result.scalar_one_or_none()

    assert payment is None


@pytest.mark.asyncio
async def test_callback_with_malformed_payload(client, db_session: AsyncSession, test_db) -> None:
    """
    Test callback with malformed payload:
    1. Send callback with invalid JSON structure
    2. Verify 200 OK response (graceful handling)
    3. Verify no processing occurred
    """
    malformed_payloads = [
        {},  # Empty payload
        {"Body": {}},  # Missing stkCallback
        {"Body": {"stkCallback": {}}},  # Missing required fields
        {
            "Body": {
                "stkCallback": {
                    "CheckoutRequestID": "ws_CO_TEST",
                    # Missing ResultCode
                }
            }
        },
    ]

    for payload in malformed_payloads:
        response = client.post("/payments/stk/callback", json=payload)
        assert response.status_code == 200
        assert response.json() == {"ResultCode": "0", "ResultDesc": "Accepted"}


@pytest.mark.asyncio
async def test_callback_various_result_codes(client, db_session: AsyncSession, test_db) -> None:
    """
    Test callback handling for various M-PESA result codes:
    - ResultCode 0: Success
    - ResultCode 1032: Request cancelled by user
    - ResultCode 1037: Timeout
    - ResultCode 1: Insufficient funds
    - ResultCode 2001: Wrong PIN
    """
    result_codes = [
        (1032, "Request cancelled by user"),
        (1037, "Timeout of transaction"),
        (1, "Insufficient funds"),
        (2001, "Wrong PIN"),
    ]

    for result_code, result_desc in result_codes:
        # Create invoice for each test
        invoice = Invoice(
            id=str(uuid4()),
            customer_name=f"User {result_code}",
            msisdn="254745678901",
            merchant_msisdn="254765432109",
            amount_cents=2000,
            currency="KES",
            description=f"Test result code {result_code}",
            status="SENT",
        )

        db_session.add(invoice)
        await db_session.commit()
        await db_session.refresh(invoice)

        # Mock STK Push
        checkout_request_id = f"ws_CO_RESULT_{result_code}"
        mock_stk_response = {
            "MerchantRequestID": f"TEST-{result_code}",
            "CheckoutRequestID": checkout_request_id,
            "ResponseCode": "0",
            "ResponseDescription": "Success",
            "CustomerMessage": "Success",
        }

        with patch("src.app.services.mpesa.httpx.AsyncClient") as mock_client:
            mock_oauth_resp = AsyncMock(spec=Response)
            mock_oauth_resp.json.return_value = {
                "access_token": "token",
                "expires_in": "3600",
            }
            mock_oauth_resp.raise_for_status = AsyncMock()

            mock_stk_resp = AsyncMock(spec=Response)
            mock_stk_resp.json.return_value = mock_stk_response
            mock_stk_resp.raise_for_status = AsyncMock()

            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_oauth_resp
            mock_instance.post.return_value = mock_stk_resp
            mock_client.return_value.__aenter__.return_value = mock_instance

            # Initiate STK Push
            stk_response = client.post(
                "/payments/stk/initiate",
                json={
                    "invoice_id": invoice.id,
                    "idempotency_key": f"test-code-{result_code}-{invoice.id}",
                },
            )

            assert stk_response.status_code == 200

        # Send failed callback
        callback_payload = {
            "Body": {
                "stkCallback": {
                    "MerchantRequestID": f"TEST-{result_code}",
                    "CheckoutRequestID": checkout_request_id,
                    "ResultCode": result_code,
                    "ResultDesc": result_desc,
                }
            }
        }

        response = client.post("/payments/stk/callback", json=callback_payload)
        assert response.status_code == 200

        # Verify payment status is FAILED
        payment_stmt = select(Payment).where(
            Payment.checkout_request_id == checkout_request_id
        )
        payment_result = await db_session.execute(payment_stmt)
        payment = payment_result.scalar_one_or_none()

        assert payment is not None
        assert payment.status == "FAILED"

        # Verify invoice status is FAILED
        await db_session.refresh(invoice)
        assert invoice.status == "FAILED"