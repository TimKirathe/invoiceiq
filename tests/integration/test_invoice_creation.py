"""
Integration tests for invoice creation (Phase 6).

Tests the complete flow of invoice creation, from POST /invoices endpoint
to WhatsApp message delivery with interactive buttons, status updates,
and merchant confirmations.
"""

import re
from unittest.mock import AsyncMock, Mock, patch

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.main import app
from src.app.models import Invoice, MessageLog
from src.app.schemas import InvoiceCreate


@pytest.mark.asyncio
async def test_create_invoice_success(db_session: AsyncSession):
    """Test that POST /invoices creates an invoice with PENDING status."""
    # Mock WhatsApp API to fail (so invoice stays PENDING)
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = Exception("WhatsApp API unavailable")

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/invoices",
                json={
                    "msisdn": "254712345678",
                    "customer_name": "John Doe",
                    "amount_cents": 10000,
                    "description": "Test invoice for Phase 6",
                },
            )

        # Verify response
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "PENDING"  # Failed to send, so stays PENDING
        assert data["msisdn"] == "254712345678"
        assert data["customer_name"] == "John Doe"
        assert data["amount_cents"] == 10000
        assert data["description"] == "Test invoice for Phase 6"
        assert data["currency"] == "KES"
        assert data["id"].startswith("INV-")

        # Verify database
        result = await db_session.execute(select(Invoice).where(Invoice.id == data["id"]))
        invoice = result.scalar_one()
        assert invoice.status == "PENDING"
        assert invoice.msisdn == "254712345678"


@pytest.mark.asyncio
async def test_create_invoice_sends_to_customer(db_session: AsyncSession):
    """Test that invoice is sent to customer via WhatsApp with interactive button."""
    # Mock WhatsApp API response
    mock_response = Mock(spec=Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "messages": [{"id": "wamid.test123"}]
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/invoices",
                json={
                    "msisdn": "254798765432",
                    "customer_name": "Jane Smith",
                    "amount_cents": 50000,
                    "description": "Website design services",
                },
            )

        assert response.status_code == 201
        data = response.json()

        # Verify WhatsApp API was called
        assert mock_post.called
        call_args = mock_post.call_args
        payload = call_args.kwargs["json"]

        # Verify interactive button structure
        assert payload["messaging_product"] == "whatsapp"
        assert payload["to"] == "254798765432"
        assert payload["type"] == "interactive"
        assert payload["interactive"]["type"] == "button"
        assert "body" in payload["interactive"]
        assert "text" in payload["interactive"]["body"]

        # Verify button structure
        buttons = payload["interactive"]["action"]["buttons"]
        assert len(buttons) == 1
        assert buttons[0]["type"] == "reply"
        assert buttons[0]["reply"]["title"] == "Pay with M-PESA"
        assert buttons[0]["reply"]["id"] == f"pay_{data['id']}"

        # Verify message text format
        message_text = payload["interactive"]["body"]["text"]
        assert data["id"] in message_text
        assert "KES 500.00" in message_text
        assert "Website design services" in message_text


@pytest.mark.asyncio
async def test_create_invoice_updates_status_to_sent(db_session: AsyncSession):
    """Test that invoice status changes to SENT after successful delivery."""
    # Mock successful WhatsApp API response
    mock_response = Mock(spec=Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "messages": [{"id": "wamid.test456"}]
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/invoices",
                json={
                    "msisdn": "254701234567",
                    "amount_cents": 25000,
                    "description": "Consulting fees",
                },
            )

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "SENT"

        # Verify in database
        result = await db_session.execute(select(Invoice).where(Invoice.id == data["id"]))
        invoice = result.scalar_one()
        assert invoice.status == "SENT"


@pytest.mark.asyncio
async def test_create_invoice_stays_pending_on_failure(db_session: AsyncSession):
    """Test that invoice status stays PENDING if WhatsApp API fails."""
    # Mock WhatsApp API failure
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = Exception("Network error")

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/invoices",
                json={
                    "msisdn": "254711111111",
                    "amount_cents": 15000,
                    "description": "Failed delivery test",
                },
            )

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "PENDING"

        # Verify in database
        result = await db_session.execute(select(Invoice).where(Invoice.id == data["id"]))
        invoice = result.scalar_one()
        assert invoice.status == "PENDING"


@pytest.mark.asyncio
async def test_message_log_created(db_session: AsyncSession):
    """Test that MessageLog entries are created for sent invoices."""
    # Mock successful WhatsApp API response
    mock_response = Mock(spec=Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "messages": [{"id": "wamid.test789"}]
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/invoices",
                json={
                    "msisdn": "254722222222",
                    "amount_cents": 30000,
                    "description": "Message log test",
                },
            )

        assert response.status_code == 201
        data = response.json()

        # Verify MessageLog entry exists
        result = await db_session.execute(
            select(MessageLog).where(MessageLog.invoice_id == data["id"])
        )
        message_logs = result.scalars().all()
        assert len(message_logs) >= 1

        # Find the invoice_sent log
        sent_log = next(
            (log for log in message_logs if log.event == "invoice_sent"), None
        )
        assert sent_log is not None
        assert sent_log.channel == "WHATSAPP"
        assert sent_log.direction == "OUT"
        assert sent_log.invoice_id == data["id"]


@pytest.mark.asyncio
async def test_invoice_id_format(db_session: AsyncSession):
    """Test that invoice ID follows INV-{timestamp}-{random} format."""
    # Mock WhatsApp API
    mock_response = Mock(spec=Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "messages": [{"id": "wamid.test000"}]
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/invoices",
                json={
                    "msisdn": "254733333333",
                    "amount_cents": 10000,
                    "description": "ID format test",
                },
            )

        assert response.status_code == 201
        data = response.json()

        # Verify ID format: INV-{timestamp}-{random}
        assert re.match(r"^INV-\d{10}-\d{4}$", data["id"])


@pytest.mark.asyncio
async def test_invoice_validation_errors():
    """Test that invalid invoice data returns validation errors."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Invalid MSISDN
        response = await client.post(
            "/invoices",
            json={
                "msisdn": "123456789",  # Invalid format
                "amount_cents": 10000,
                "description": "Test",
            },
        )
        assert response.status_code == 422

        # Amount too small
        response = await client.post(
            "/invoices",
            json={
                "msisdn": "254712345678",
                "amount_cents": 50,  # Less than 100 cents
                "description": "Test",
            },
        )
        assert response.status_code == 422

        # Description too short
        response = await client.post(
            "/invoices",
            json={
                "msisdn": "254712345678",
                "amount_cents": 10000,
                "description": "ab",  # Less than 3 characters
            },
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_message_text_format():
    """Test that invoice message text is ≤ 2 lines as per requirements."""
    # Mock WhatsApp API
    mock_response = Mock(spec=Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "messages": [{"id": "wamid.test111"}]
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/invoices",
                json={
                    "msisdn": "254744444444",
                    "amount_cents": 10000,
                    "description": "Text format test",
                },
            )

        assert response.status_code == 201

        # Verify message format
        call_args = mock_post.call_args
        payload = call_args.kwargs["json"]
        message_text = payload["interactive"]["body"]["text"]

        # Message should have exactly 2 lines
        lines = message_text.strip().split("\n")
        assert len(lines) == 2

        # Line 1: Invoice {id}
        assert lines[0].startswith("Invoice INV-")

        # Line 2: Amount: KES {amount} | {description}
        assert "Amount: KES 100.00" in lines[1]
        assert "Text format test" in lines[1]