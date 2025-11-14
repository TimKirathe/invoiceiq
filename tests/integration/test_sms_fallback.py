"""
Integration tests for SMS fallback functionality.

Tests the SMS fallback mechanism when WhatsApp delivery fails, including
SMS sending, inbound SMS handling, delivery status callbacks, and command parsing.
"""

from typing import AsyncGenerator
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient, RequestError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from src.app.db import Base, get_db
from src.app.main import app
from src.app.models import MessageLog
from src.app.services.sms import SMSService
from src.app.services.whatsapp import WhatsAppService


# Test database setup
#Use file-based SQLite for better session sharing
TEST_DATABASE_URL = "sqlite+aiosqlite:///./test_sms.db"

# Create async engine for tests
test_engine = create_async_engine(
    TEST_DATABASE_URL,
    poolclass=NullPool,
    connect_args={"check_same_thread": False},
)

# Create async session factory
TestSessionLocal = sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@pytest.fixture
async def test_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Create a fresh database for each test.

    Yields:
        AsyncSession: Test database session
    """
    # Create tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Create session
    async with TestSessionLocal() as session:
        yield session

    # Drop tables after test
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
def override_get_db(test_db: AsyncSession):
    """Override the get_db dependency with test database."""
    async def _override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = _override_get_db
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def mock_sms_api_success():
    """Mock successful SMS API response."""
    return {
        "SMSMessageData": {
            "Message": "Sent to 1/1 Total Cost: KES 0.8000",
            "Recipients": [
                {
                    "statusCode": 101,
                    "number": "+254712345678",
                    "status": "Success",
                    "cost": "KES 0.8000",
                    "messageId": "ATXid_test123"
                }
            ]
        }
    }


@pytest.fixture
def mock_whatsapp_network_error():
    """Mock WhatsApp network error."""
    return RequestError("Connection timeout")


class TestSMSService:
    """Test SMS service functionality."""

    @pytest.mark.asyncio
    async def test_send_sms_success(self, mock_sms_api_success):
        """Test successful SMS sending."""
        sms_service = SMSService()

        with patch("httpx.AsyncClient.post") as mock_post:
            # Mock successful API response
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_sms_api_success
            mock_response.raise_for_status = MagicMock()
            mock_post.return_value = mock_response

            result = await sms_service.send_sms(
                to="254712345678",
                message="Test SMS message",
            )

            assert result["status"] == "success"
            assert result["recipient"] == "254712345678"
            assert result["message"] == "Test SMS message"

            # Verify API was called with correct parameters
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args.kwargs["data"]["username"] == sms_service.username
            assert call_args.kwargs["data"]["to"] == "+254712345678"
            assert call_args.kwargs["data"]["message"] == "Test SMS message"

    @pytest.mark.asyncio
    async def test_send_sms_invalid_phone(self):
        """Test SMS sending with invalid phone number."""
        sms_service = SMSService()

        with pytest.raises(ValueError, match="Invalid phone number"):
            await sms_service.send_sms(
                to="invalid_phone",
                message="Test message",
            )

    @pytest.mark.asyncio
    async def test_send_sms_api_error(self):
        """Test SMS sending with API error."""
        sms_service = SMSService()

        with patch("httpx.AsyncClient.post") as mock_post:
            # Mock API error response
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_response.text = "Unauthorized"
            mock_response.raise_for_status.side_effect = Exception("HTTP 401")
            mock_post.return_value = mock_response

            with pytest.raises(Exception, match="SMS API error"):
                await sms_service.send_sms(
                    to="254712345678",
                    message="Test message",
                )

    @pytest.mark.asyncio
    async def test_send_invoice_to_customer_via_sms(
        self, test_db: AsyncSession, mock_sms_api_success
    ):
        """Test sending invoice to customer via SMS."""
        sms_service = SMSService()

        with patch("httpx.AsyncClient.post") as mock_post:
            # Mock successful API response
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_sms_api_success
            mock_response.raise_for_status = MagicMock()
            mock_post.return_value = mock_response

            # Send invoice
            success = await sms_service.send_invoice_to_customer(
                invoice_id="INV-123",
                customer_msisdn="254712345678",
                customer_name="John Doe",
                amount_cents=50000,
                description="Test invoice",
                db_session=test_db,
            )

            assert success is True

            # Verify MessageLog was created
            result = await test_db.execute(
                select(MessageLog).where(MessageLog.channel == "SMS")
            )
            logs = result.scalars().all()
            assert len(logs) == 1
            assert logs[0].invoice_id == "INV-123"
            assert logs[0].direction == "OUT"
            assert logs[0].event == "invoice_sent"

    def test_format_invoice_sms(self):
        """Test invoice SMS formatting."""
        sms_service = SMSService()

        # Create mock invoice
        invoice = MagicMock()
        invoice.id = "INV-123"
        invoice.amount_cents = 50000  # KES 500.00

        message = sms_service.format_invoice_sms(invoice)

        assert "INV-123" in message
        assert "500.00" in message
        assert "Reply PAY" in message
        assert len(message) <= 160 * 2  # Should fit in 2 SMS messages

    def test_parse_sms_command_pay(self):
        """Test parsing PAY command from SMS."""
        sms_service = SMSService()

        result = sms_service.parse_sms_command("PAY")

        assert result["command"] == "pay"
        assert result["params"] == {}

    def test_parse_sms_command_invoice(self):
        """Test parsing invoice command from SMS."""
        sms_service = SMSService()

        result = sms_service.parse_sms_command(
            "invoice 254712345678 500 Test description"
        )

        assert result["command"] == "invoice"
        assert result["params"]["phone"] == "254712345678"
        assert result["params"]["amount"] == 500
        assert result["params"]["description"] == "Test description"

    def test_parse_sms_command_help(self):
        """Test parsing help command from SMS."""
        sms_service = SMSService()

        result = sms_service.parse_sms_command("help")

        assert result["command"] == "help"
        assert result["params"] == {}

    def test_parse_sms_command_unknown(self):
        """Test parsing unknown command from SMS."""
        sms_service = SMSService()

        result = sms_service.parse_sms_command("unknown command")

        assert result["command"] == "unknown"

    def test_parse_africas_talking_callback(self):
        """Test parsing Africa's Talking inbound SMS callback."""
        sms_service = SMSService()

        payload = {
            "from": "+254712345678",
            "to": "12345",
            "text": "PAY",
            "date": "2025-11-15 10:00:00",
            "id": "ATXid_test123",
            "linkId": "SampleLinkId123"
        }

        result = sms_service.parse_africas_talking_callback(payload)

        assert result is not None
        assert result["from"] == "+254712345678"
        assert result["text"] == "PAY"
        assert result["message_id"] == "ATXid_test123"

    def test_parse_delivery_receipt(self):
        """Test parsing delivery receipt callback."""
        sms_service = SMSService()

        payload = {
            "id": "ATXid_test123",
            "status": "Success",
            "phoneNumber": "+254712345678",
            "retryCount": 0
        }

        result = sms_service.parse_delivery_receipt(payload)

        assert result is not None
        assert result["message_id"] == "ATXid_test123"
        assert result["status"] == "Success"
        assert result["phone_number"] == "+254712345678"


class TestSMSFallback:
    """Test WhatsApp to SMS fallback functionality."""

    @pytest.mark.asyncio
    async def test_whatsapp_success_no_sms_fallback(
        self, test_db: AsyncSession
    ):
        """Test that SMS fallback is NOT triggered when WhatsApp succeeds."""
        whatsapp_service = WhatsAppService()

        with patch("httpx.AsyncClient.post") as mock_post:
            # Mock successful WhatsApp response
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "messages": [{"id": "wamid.test123"}]
            }
            mock_response.raise_for_status = MagicMock()
            mock_post.return_value = mock_response

            success = await whatsapp_service.send_invoice_to_customer(
                invoice_id="INV-123",
                customer_msisdn="254712345678",
                customer_name="John Doe",
                amount_cents=50000,
                description="Test invoice",
                db_session=test_db,
            )

            assert success is True

            # Verify only WhatsApp message was logged (no SMS)
            result = await test_db.execute(select(MessageLog))
            logs = result.scalars().all()
            assert len(logs) == 1
            assert logs[0].channel == "WHATSAPP"

    @pytest.mark.asyncio
    async def test_whatsapp_network_error_triggers_sms_fallback(
        self, test_db: AsyncSession, mock_sms_api_success
    ):
        """Test that SMS fallback is triggered on WhatsApp network error."""
        whatsapp_service = WhatsAppService()

        with patch("httpx.AsyncClient.post") as mock_post:
            # First call (WhatsApp) fails with network error
            # Second call (SMS) succeeds
            def side_effect(*args, **kwargs):
                if "graph.facebook.com" in args[0]:
                    # WhatsApp call - fail
                    raise RequestError("Connection timeout")
                else:
                    # SMS call - succeed
                    mock_response = MagicMock()
                    mock_response.status_code = 200
                    mock_response.json.return_value = mock_sms_api_success
                    mock_response.raise_for_status = MagicMock()
                    return mock_response

            mock_post.side_effect = side_effect

            success = await whatsapp_service.send_invoice_to_customer(
                invoice_id="INV-123",
                customer_msisdn="254712345678",
                customer_name="John Doe",
                amount_cents=50000,
                description="Test invoice",
                db_session=test_db,
            )

            # Should succeed via SMS fallback
            assert success is True

            # Verify both WhatsApp failure and SMS success were logged
            result = await test_db.execute(
                select(MessageLog).order_by(MessageLog.created_at)
            )
            logs = result.scalars().all()
            assert len(logs) == 2
            assert logs[0].channel == "WHATSAPP"
            assert logs[0].event == "invoice_send_failed"
            assert logs[1].channel == "SMS"
            assert logs[1].event == "invoice_sent"

    @pytest.mark.asyncio
    async def test_whatsapp_unexpected_error_triggers_sms_fallback(
        self, test_db: AsyncSession, mock_sms_api_success
    ):
        """Test that SMS fallback is triggered on WhatsApp unexpected error."""
        whatsapp_service = WhatsAppService()

        with patch("httpx.AsyncClient.post") as mock_post:
            # First call (WhatsApp) fails with unexpected error
            # Second call (SMS) succeeds
            def side_effect(*args, **kwargs):
                if "graph.facebook.com" in args[0]:
                    # WhatsApp call - fail
                    raise Exception("Unexpected error")
                else:
                    # SMS call - succeed
                    mock_response = MagicMock()
                    mock_response.status_code = 200
                    mock_response.json.return_value = mock_sms_api_success
                    mock_response.raise_for_status = MagicMock()
                    return mock_response

            mock_post.side_effect = side_effect

            success = await whatsapp_service.send_invoice_to_customer(
                invoice_id="INV-123",
                customer_msisdn="254712345678",
                customer_name="John Doe",
                amount_cents=50000,
                description="Test invoice",
                db_session=test_db,
            )

            # Should succeed via SMS fallback
            assert success is True

    @pytest.mark.asyncio
    async def test_both_whatsapp_and_sms_fail(
        self, test_db: AsyncSession
    ):
        """Test when both WhatsApp and SMS fail."""
        whatsapp_service = WhatsAppService()

        with patch("httpx.AsyncClient.post") as mock_post:
            # Both calls fail
            mock_post.side_effect = RequestError("Connection timeout")

            success = await whatsapp_service.send_invoice_to_customer(
                invoice_id="INV-123",
                customer_msisdn="254712345678",
                customer_name="John Doe",
                amount_cents=50000,
                description="Test invoice",
                db_session=test_db,
            )

            # Should fail
            assert success is False

            # Verify both failures were logged
            result = await test_db.execute(
                select(MessageLog).order_by(MessageLog.created_at)
            )
            logs = result.scalars().all()
            # WhatsApp failure, SMS failure (or SMS fallback failure)
            assert len(logs) >= 2


class TestSMSRoutes:
    """Test SMS router endpoints."""

    @pytest.mark.asyncio
    async def test_inbound_sms_endpoint(
        self, test_db: AsyncSession, override_get_db
    ):
        """Test receiving inbound SMS."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            payload = {
                "from": "+254712345678",
                "to": "12345",
                "text": "PAY",
                "date": "2025-11-15 10:00:00",
                "id": "ATXid_test123",
            }

            response = await client.post("/sms/inbound", json=payload)

            assert response.status_code == 200
            assert response.json() == {"status": "received"}

            # Verify message was logged
            result = await test_db.execute(
                select(MessageLog).where(
                    MessageLog.channel == "SMS", MessageLog.direction == "IN"
                )
            )
            logs = result.scalars().all()
            assert len(logs) == 1

    @pytest.mark.asyncio
    async def test_delivery_status_endpoint(
        self, test_db: AsyncSession, override_get_db
    ):
        """Test receiving delivery status callback."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            payload = {
                "id": "ATXid_test123",
                "status": "Success",
                "phoneNumber": "+254712345678",
                "retryCount": 0,
            }

            response = await client.post("/sms/status", json=payload)

            assert response.status_code == 200
            assert response.json() == {"status": "received"}

            # Verify delivery status was logged
            result = await test_db.execute(
                select(MessageLog).where(
                    MessageLog.channel == "SMS", MessageLog.direction == "OUT"
                )
            )
            logs = result.scalars().all()
            assert len(logs) == 1
            assert "Success" in logs[0].event or "success" in logs[0].event.lower()