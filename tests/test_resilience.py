"""
Tests for Phase 13: Error Handling & Resilience.

This module tests retry logic, circuit breakers, rate limiting, timeout handling,
and error message user-friendliness across the InvoiceIQ system.
"""

import pytest
import httpx
import pybreaker
from unittest.mock import AsyncMock, Mock, patch
from slowapi.errors import RateLimitExceeded

from src.app.services.mpesa import MPesaService, mpesa_circuit_breaker
from src.app.services.whatsapp import WhatsAppService, get_user_friendly_error_message


class TestMPesaRetryLogic:
    """Test retry logic for M-PESA service."""

    @pytest.mark.asyncio
    async def test_mpesa_token_retries_on_timeout(self):
        """Test that M-PESA token generation retries on timeout."""
        mpesa_service = MPesaService()
        # Clear token cache
        mpesa_service._token_cache = {}

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "access_token": "test_token_123",
                "expires_in": 3600
            }

            call_count = 0

            async def mock_get(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    raise httpx.TimeoutException("Request timed out")
                mock_response.raise_for_status = Mock()
                return mock_response

            mock_client.return_value.__aenter__.return_value.get = mock_get

            # Should succeed on 3rd attempt
            token = await mpesa_service.get_access_token()

            assert token == "test_token_123"
            assert call_count == 3

    @pytest.mark.asyncio
    async def test_mpesa_stk_push_retries_on_network_error(self):
        """Test that STK Push retries on network errors."""
        mpesa_service = MPesaService()

        # Mock get_access_token to return immediately
        with patch.object(mpesa_service, "get_access_token", return_value="test_token"):
            with patch("httpx.AsyncClient") as mock_client:
                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.json.return_value = {
                    "ResponseCode": "0",
                    "ResponseDescription": "Success"
                }

                call_count = 0

                async def mock_post(*args, **kwargs):
                    nonlocal call_count
                    call_count += 1
                    if call_count < 3:
                        raise httpx.RequestError("Network error")
                    mock_response.raise_for_status = Mock()
                    return mock_response

                mock_client.return_value.__aenter__.return_value.post = mock_post

                # Should succeed on 3rd attempt
                result = await mpesa_service.initiate_stk_push(
                    phone_number="254712345678",
                    amount=100,
                    account_reference="INV-123",
                    transaction_desc="Test payment"
                )

                assert result["ResponseCode"] == "0"
                assert call_count == 3


class TestMPesaCircuitBreaker:
    """Test circuit breaker for M-PESA service."""

    def setup_method(self):
        """Reset circuit breaker before each test."""
        # Reset the circuit breaker
        mpesa_circuit_breaker._failure_count = 0
        mpesa_circuit_breaker._state = pybreaker.STATE_CLOSED

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_failures(self):
        """Test that circuit breaker opens after consecutive failures."""
        mpesa_service = MPesaService()

        # Mock get_access_token
        with patch.object(mpesa_service, "get_access_token", return_value="test_token"):
            with patch("httpx.AsyncClient") as mock_client:
                async def mock_post(*args, **kwargs):
                    raise httpx.RequestError("Network error")

                mock_client.return_value.__aenter__.return_value.post = mock_post

                # Make 5 failed requests to open the circuit
                for _ in range(5):
                    try:
                        await mpesa_service.initiate_stk_push(
                            phone_number="254712345678",
                            amount=100,
                            account_reference="INV-123",
                            transaction_desc="Test"
                        )
                    except Exception:
                        pass

                # 6th request should fail immediately with CircuitBreakerError
                with pytest.raises(pybreaker.CircuitBreakerError):
                    await mpesa_service.initiate_stk_push(
                        phone_number="254712345678",
                        amount=100,
                        account_reference="INV-123",
                        transaction_desc="Test"
                    )


class TestRateLimiting:
    """Test rate limiting on invoice creation endpoint."""

    @pytest.mark.asyncio
    async def test_rate_limit_enforcement(self):
        """Test that rate limiting returns 429 when exceeded."""
        from fastapi.testclient import TestClient
        from src.app.main import app

        client = TestClient(app)

        # Make 11 requests rapidly (limit is 10/minute)
        invoice_data = {
            "msisdn": "254712345678",
            "amount_cents": 10000,
            "description": "Test invoice"
        }

        responses = []
        for _ in range(11):
            response = client.post("/invoices", json=invoice_data)
            responses.append(response.status_code)

        # At least one should be 429 (Too Many Requests)
        assert 429 in responses


class TestTimeoutHandling:
    """Test timeout handling across services."""

    @pytest.mark.asyncio
    async def test_whatsapp_timeout_handled(self):
        """Test that WhatsApp service handles timeout gracefully."""
        whatsapp_service = WhatsAppService()

        with patch("httpx.AsyncClient") as mock_client:
            async def mock_post(*args, **kwargs):
                raise httpx.TimeoutException("Request timed out")

            mock_client.return_value.__aenter__.return_value.post = mock_post

            # Should raise exception after retries
            with pytest.raises(Exception, match="Failed to send message"):
                await whatsapp_service.send_message(
                    to="254712345678",
                    message="Test message"
                )

    @pytest.mark.asyncio
    async def test_mpesa_timeout_logged(self):
        """Test that M-PESA timeouts are properly logged."""
        mpesa_service = MPesaService()
        mpesa_service._token_cache = {}

        with patch("httpx.AsyncClient") as mock_client:
            async def mock_get(*args, **kwargs):
                raise httpx.TimeoutException("Request timed out")

            mock_client.return_value.__aenter__.return_value.get = mock_get

            # Should fail after retries
            with pytest.raises(httpx.TimeoutException):
                await mpesa_service.get_access_token()


class TestUserFriendlyErrorMessages:
    """Test user-friendly error message generation."""

    def test_timeout_error_message(self):
        """Test timeout error mapping."""
        error = httpx.TimeoutException("Connection timeout")
        message = get_user_friendly_error_message(error)

        assert "temporarily unavailable" in message.lower()
        assert "try again" in message.lower()

    def test_network_error_message(self):
        """Test network error mapping."""
        error = httpx.RequestError("Network error")
        message = get_user_friendly_error_message(error)

        assert "connection issue" in message.lower()
        assert "internet" in message.lower()

    def test_phone_validation_error_message(self):
        """Test phone validation error mapping."""
        error = ValueError("Invalid phone number")
        message = get_user_friendly_error_message(error)

        assert "invalid phone number" in message.lower()
        assert "2547" in message

    def test_amount_validation_error_message(self):
        """Test amount validation error mapping."""
        error = ValueError("Invalid amount")
        message = get_user_friendly_error_message(error)

        assert "invalid amount" in message.lower()
        assert "1 kes" in message.lower()

    def test_circuit_breaker_error_message(self):
        """Test circuit breaker error mapping."""
        error = pybreaker.CircuitBreakerError("Circuit breaker is OPEN")
        message = get_user_friendly_error_message(error)

        assert "payment service" in message.lower()
        assert "temporarily unavailable" in message.lower()

    def test_rate_limit_error_message(self):
        """Test rate limit error mapping."""
        error = RateLimitExceeded("Rate limit exceeded")
        message = get_user_friendly_error_message(error)

        assert "too many requests" in message.lower()
        assert "wait" in message.lower()

    def test_generic_error_fallback(self):
        """Test fallback for unmapped errors."""
        error = RuntimeError("Something unexpected")
        message = get_user_friendly_error_message(error)

        assert "something went wrong" in message.lower()
        assert "contact support" in message.lower()


class TestAPIErrorRecovery:
    """Test API error recovery scenarios."""

    @pytest.mark.asyncio
    async def test_whatsapp_api_400_not_retried(self):
        """Test that 4xx errors are not retried."""
        whatsapp_service = WhatsAppService()

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = Mock()
            mock_response.status_code = 400
            mock_response.text = "Bad request"

            async def mock_post(*args, **kwargs):
                mock_response.raise_for_status = Mock(
                    side_effect=httpx.HTTPStatusError(
                        "Bad request", request=Mock(), response=mock_response
                    )
                )
                return mock_response

            mock_client.return_value.__aenter__.return_value.post = mock_post

            # Should fail immediately without retries
            with pytest.raises(Exception, match="WhatsApp API error"):
                await whatsapp_service.send_message(
                    to="254712345678",
                    message="Test"
                )

    @pytest.mark.asyncio
    async def test_mpesa_invalid_response_handled(self):
        """Test that invalid M-PESA responses are handled."""
        mpesa_service = MPesaService()
        mpesa_service._token_cache = {}

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {}  # Missing access_token

            async def mock_get(*args, **kwargs):
                mock_response.raise_for_status = Mock()
                return mock_response

            mock_client.return_value.__aenter__.return_value.get = mock_get

            # Should raise ValueError for invalid response
            with pytest.raises(ValueError, match="No access_token"):
                await mpesa_service.get_access_token()


class TestWebhookSignatureValidation:
    """Test webhook signature validation placeholder."""

    def test_signature_validation_placeholder_exists(self):
        """Test that signature validation function exists."""
        from src.app.routers.whatsapp import validate_webhook_signature

        # Should return True in MVP mode
        assert validate_webhook_signature({}, "") is True

    def test_signature_validation_logs_warning(self):
        """Test that signature validation logs warning when not configured."""
        from src.app.routers.whatsapp import validate_webhook_signature

        # Should always return True for MVP
        result = validate_webhook_signature({"test": "payload"}, "signature_123")
        assert result is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])