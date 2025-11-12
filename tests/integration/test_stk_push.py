"""
Integration tests for STK Push flow.

Tests full flow from invoice creation through STK Push initiation with
mocked M-PESA API responses.
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models import Invoice, Payment


@pytest.mark.asyncio
async def test_stk_push_initiate_success(client, db_session: AsyncSession) -> None:
    """
    Test successful STK Push initiation.

    Creates invoice, initiates STK Push, and verifies payment record
    is created with correct data.
    """
    # Create invoice with SENT status
    invoice = Invoice(
        id=str(uuid4()),
        customer_name="John Doe",
        msisdn="254712345678",
        amount_cents=10000,  # 100 KES
        currency="KES",
        description="Test invoice for payment",
        status="SENT",
    )

    db_session.add(invoice)
    await db_session.commit()
    await db_session.refresh(invoice)

    # Mock M-PESA API responses
    mock_oauth_response = {
        "access_token": "test_access_token_123",
        "expires_in": "3600",
    }

    mock_stk_response = {
        "MerchantRequestID": "12345-67890-12345",
        "CheckoutRequestID": "ws_CO_12345678901234567890",
        "ResponseCode": "0",
        "ResponseDescription": "Success. Request accepted for processing",
        "CustomerMessage": "Success. Request accepted for processing",
    }

    with patch("src.app.services.mpesa.httpx.AsyncClient") as mock_client:
        # Setup OAuth mock
        mock_oauth_resp = AsyncMock(spec=Response)
        mock_oauth_resp.json.return_value = mock_oauth_response
        mock_oauth_resp.raise_for_status = AsyncMock()

        # Setup STK Push mock
        mock_stk_resp = AsyncMock(spec=Response)
        mock_stk_resp.json.return_value = mock_stk_response
        mock_stk_resp.raise_for_status = AsyncMock()

        # Configure mock client
        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_oauth_resp
        mock_client_instance.post.return_value = mock_stk_resp
        mock_client.return_value.__aenter__.return_value = mock_client_instance

        # Make request to initiate STK Push
        response = client.post(
            "/payments/stk/initiate",
            json={
                "invoice_id": invoice.id,
                "idempotency_key": f"test-payment-{invoice.id}",
            },
        )

        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert "id" in data
        assert data["invoice_id"] == invoice.id
        assert data["method"] == "MPESA_STK"
        assert data["status"] == "INITIATED"
        assert data["amount_cents"] == 10000
        assert data["idempotency_key"] == f"test-payment-{invoice.id}"

        # Verify payment record in database
        payment_stmt = select(Payment).where(Payment.invoice_id == invoice.id)
        payment_result = await db_session.execute(payment_stmt)
        payment = payment_result.scalar_one_or_none()

        assert payment is not None
        assert payment.status == "INITIATED"
        assert payment.amount_cents == 10000
        assert payment.raw_request is not None
        assert payment.raw_request["amount"] == 100  # Whole KES
        assert payment.raw_request["phone_number"] == "254712345678"


@pytest.mark.asyncio
async def test_stk_push_idempotency(client, db_session: AsyncSession) -> None:
    """
    Test idempotency prevents duplicate payments.

    Makes two identical requests and verifies that the second request
    returns cached response without creating duplicate payment.
    """
    # Create invoice
    invoice = Invoice(
        id=str(uuid4()),
        customer_name="Jane Doe",
        msisdn="254723456789",
        amount_cents=5000,
        currency="KES",
        description="Idempotency test invoice",
        status="SENT",
    )

    db_session.add(invoice)
    await db_session.commit()
    await db_session.refresh(invoice)

    idempotency_key = f"idempotent-payment-{invoice.id}"

    # Mock M-PESA API
    mock_oauth_response = {"access_token": "token_123", "expires_in": "3600"}
    mock_stk_response = {
        "MerchantRequestID": "12345",
        "CheckoutRequestID": "ws_CO_12345",
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

        # First request
        response1 = client.post(
            "/payments/stk/initiate",
            json={
                "invoice_id": invoice.id,
                "idempotency_key": idempotency_key,
            },
        )

        assert response1.status_code == 200
        data1 = response1.json()
        payment_id_1 = data1["id"]

        # Second request with same idempotency key
        response2 = client.post(
            "/payments/stk/initiate",
            json={
                "invoice_id": invoice.id,
                "idempotency_key": idempotency_key,
            },
        )

        assert response2.status_code == 200
        data2 = response2.json()
        payment_id_2 = data2["id"]

        # Verify both requests returned same payment
        assert payment_id_1 == payment_id_2

        # Verify only one payment in database
        payments_stmt = select(Payment).where(
            Payment.idempotency_key == idempotency_key
        )
        payments_result = await db_session.execute(payments_stmt)
        payments = payments_result.scalars().all()

        assert len(payments) == 1


@pytest.mark.asyncio
async def test_stk_push_invoice_not_found(client, db_session: AsyncSession) -> None:
    """Test STK Push initiation with non-existent invoice."""
    response = client.post(
        "/payments/stk/initiate",
        json={
            "invoice_id": str(uuid4()),  # Non-existent invoice
            "idempotency_key": "test-nonexistent",
        },
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_stk_push_invalid_invoice_status(
    client, db_session: AsyncSession
) -> None:
    """Test STK Push initiation with invoice in wrong status."""
    # Create invoice with PENDING status (not SENT)
    invoice = Invoice(
        id=str(uuid4()),
        customer_name="Test User",
        msisdn="254734567890",
        amount_cents=3000,
        currency="KES",
        description="Pending invoice",
        status="PENDING",  # Wrong status
    )

    db_session.add(invoice)
    await db_session.commit()
    await db_session.refresh(invoice)

    response = client.post(
        "/payments/stk/initiate",
        json={
            "invoice_id": invoice.id,
            "idempotency_key": f"test-invalid-status-{invoice.id}",
        },
    )

    assert response.status_code == 400
    assert "must be SENT" in response.json()["detail"]


@pytest.mark.asyncio
async def test_stk_push_api_failure(client, db_session: AsyncSession) -> None:
    """Test STK Push initiation when M-PESA API fails."""
    # Create invoice
    invoice = Invoice(
        id=str(uuid4()),
        customer_name="API Failure Test",
        msisdn="254745678901",
        amount_cents=2000,
        currency="KES",
        description="API failure test",
        status="SENT",
    )

    db_session.add(invoice)
    await db_session.commit()
    await db_session.refresh(invoice)

    # Mock M-PESA API failure
    with patch("src.app.services.mpesa.httpx.AsyncClient") as mock_client:
        # OAuth succeeds
        mock_oauth_resp = AsyncMock(spec=Response)
        mock_oauth_resp.json.return_value = {
            "access_token": "token",
            "expires_in": "3600",
        }
        mock_oauth_resp.raise_for_status = AsyncMock()

        # STK Push fails
        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_oauth_resp
        mock_client_instance.post.side_effect = Exception("M-PESA API error")
        mock_client.return_value.__aenter__.return_value = mock_client_instance

        response = client.post(
            "/payments/stk/initiate",
            json={
                "invoice_id": invoice.id,
                "idempotency_key": f"test-api-failure-{invoice.id}",
            },
        )

        assert response.status_code == 500
        assert "Failed to initiate STK Push" in response.json()["detail"]

        # Verify payment record was created with FAILED status
        payment_stmt = select(Payment).where(Payment.invoice_id == invoice.id)
        payment_result = await db_session.execute(payment_stmt)
        payment = payment_result.scalar_one_or_none()

        assert payment is not None
        assert payment.status == "FAILED"
        assert "error" in payment.raw_request


@pytest.mark.asyncio
async def test_stk_push_amount_conversion(client, db_session: AsyncSession) -> None:
    """Test that amount is correctly converted from cents to whole KES."""
    # Create invoice with specific amount
    invoice = Invoice(
        id=str(uuid4()),
        customer_name="Amount Test",
        msisdn="254756789012",
        amount_cents=12345,  # 123.45 KES in cents
        currency="KES",
        description="Amount conversion test",
        status="SENT",
    )

    db_session.add(invoice)
    await db_session.commit()
    await db_session.refresh(invoice)

    # Mock M-PESA API
    mock_oauth_response = {"access_token": "token", "expires_in": "3600"}
    mock_stk_response = {
        "MerchantRequestID": "12345",
        "CheckoutRequestID": "ws_CO_12345",
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

        response = client.post(
            "/payments/stk/initiate",
            json={
                "invoice_id": invoice.id,
                "idempotency_key": f"test-amount-{invoice.id}",
            },
        )

        assert response.status_code == 200

        # Verify payment record
        payment_stmt = select(Payment).where(Payment.invoice_id == invoice.id)
        payment_result = await db_session.execute(payment_stmt)
        payment = payment_result.scalar_one_or_none()

        assert payment is not None

        # Verify amount was converted to whole KES (12345 cents = 123.45 KES = 123 KES rounded)
        assert payment.raw_request["amount"] == 123