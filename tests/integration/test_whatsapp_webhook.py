"""
Integration tests for WhatsApp webhook endpoints.

These tests verify the webhook verification (GET) and message receiving (POST)
endpoints work correctly with the database and application lifecycle.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.app.db import Base, get_db
from src.app.main import app
from src.app.models import MessageLog


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
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac


class TestWebhookVerification:
    """Tests for GET /whatsapp/webhook verification endpoint."""

    @pytest.mark.asyncio
    async def test_valid_verification(self, client: AsyncClient, test_db):
        """Test webhook verification with valid token."""
        response = await client.get(
            "/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "test_verify_token",
                "hub.challenge": "test_challenge_12345",
            },
        )

        assert response.status_code == 200
        assert response.text == "test_challenge_12345"
        assert response.headers["content-type"] == "text/plain; charset=utf-8"

    @pytest.mark.asyncio
    async def test_invalid_verify_token(self, client: AsyncClient, test_db):
        """Test webhook verification with invalid token."""
        response = await client.get(
            "/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong_token",
                "hub.challenge": "test_challenge_12345",
            },
        )

        assert response.status_code == 403
        assert "verify token" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_invalid_hub_mode(self, client: AsyncClient, test_db):
        """Test webhook verification with invalid hub.mode."""
        response = await client.get(
            "/whatsapp/webhook",
            params={
                "hub.mode": "unsubscribe",
                "hub.verify_token": "test_verify_token",
                "hub.challenge": "test_challenge_12345",
            },
        )

        assert response.status_code == 403
        assert "hub.mode" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_missing_parameters(self, client: AsyncClient, test_db):
        """Test webhook verification with missing required parameters."""
        # Missing hub.challenge
        response = await client.get(
            "/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "test_verify_token",
            },
        )

        assert response.status_code == 422  # Validation error


class TestWebhookReceiver:
    """Tests for POST /whatsapp/webhook message receiver endpoint."""

    @pytest.mark.asyncio
    async def test_receive_valid_payload(self, client: AsyncClient, test_db):
        """Test receiving a valid WhatsApp webhook payload."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "123456789",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "254712345678",
                                    "phone_number_id": "test_phone_id",
                                },
                                "messages": [
                                    {
                                        "from": "254798765432",
                                        "id": "wamid.test123",
                                        "timestamp": "1234567890",
                                        "text": {"body": "Hello"},
                                        "type": "text",
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }

        response = await client.post("/whatsapp/webhook", json=payload)

        assert response.status_code == 200
        assert response.json() == {"status": "received"}

        # Verify MessageLog was created
        async for db in override_get_db():
            result = await db.execute(select(MessageLog))
            message_logs = result.scalars().all()

            assert len(message_logs) == 1
            log = message_logs[0]
            assert log.channel == "WHATSAPP"
            assert log.direction == "IN"
            assert log.event == "webhook_received"
            assert log.payload == payload
            assert log.invoice_id is None

    @pytest.mark.asyncio
    async def test_receive_minimal_payload(self, client: AsyncClient, test_db):
        """Test receiving a minimal webhook payload."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [],
        }

        response = await client.post("/whatsapp/webhook", json=payload)

        assert response.status_code == 200
        assert response.json() == {"status": "received"}

        # Verify MessageLog was created
        async for db in override_get_db():
            result = await db.execute(select(MessageLog))
            message_logs = result.scalars().all()

            assert len(message_logs) == 1
            assert message_logs[0].payload == payload

    @pytest.mark.asyncio
    async def test_receive_empty_payload(self, client: AsyncClient, test_db):
        """Test receiving an empty webhook payload."""
        payload: dict[str, str] = {}

        response = await client.post("/whatsapp/webhook", json=payload)

        # Should still return 200 and log the payload
        assert response.status_code == 200
        assert response.json() == {"status": "received"}

        # Verify MessageLog was created
        async for db in override_get_db():
            result = await db.execute(select(MessageLog))
            message_logs = result.scalars().all()

            assert len(message_logs) == 1
            assert message_logs[0].payload == payload

    @pytest.mark.asyncio
    async def test_receive_invalid_json(self, client: AsyncClient, test_db):
        """Test receiving invalid JSON."""
        response = await client.post(
            "/whatsapp/webhook",
            content="not json",
            headers={"Content-Type": "application/json"},
        )

        # FastAPI should return 422 for invalid JSON
        assert response.status_code == 422


class TestHealthChecks:
    """Tests for health check endpoints."""

    @pytest.mark.asyncio
    async def test_healthz(self, client: AsyncClient, test_db):
        """Test /healthz endpoint."""
        response = await client.get("/healthz")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_readyz(self, client: AsyncClient, test_db):
        """Test /readyz endpoint with database connection."""
        response = await client.get("/readyz")

        assert response.status_code == 200
        assert response.json() == {"status": "ready", "database": "connected"}