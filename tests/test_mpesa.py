"""
Unit tests for M-PESA service.

Tests password generation, timestamp format, token caching, and STK request
payload formatting without making real API calls.
"""

import base64
import time
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import Response

from src.app.services.mpesa import MPesaService


class TestMPesaService:
    """Unit tests for MPesaService class."""

    @pytest.fixture
    def mpesa_service(self) -> MPesaService:
        """
        Create MPesaService instance for testing.

        Returns:
            MPesaService instance configured for sandbox
        """
        return MPesaService(environment="sandbox")

    def test_initialization_sandbox(self) -> None:
        """Test MPesaService initialization with sandbox environment."""
        service = MPesaService(environment="sandbox")

        assert service.environment == "sandbox"
        assert service.base_url == MPesaService.SANDBOX_BASE_URL

    def test_initialization_production(self) -> None:
        """Test MPesaService initialization with production environment."""
        service = MPesaService(environment="production")

        assert service.environment == "production"
        assert service.base_url == MPesaService.PRODUCTION_BASE_URL

    def test_generate_password(self, mpesa_service: MPesaService) -> None:
        """
        Test password generation matches Daraja specification.

        Uses known example from Daraja documentation to verify correctness.
        """
        # Known example from Daraja documentation
        shortcode = "174379"
        passkey = "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919"
        timestamp = "20160216165627"

        # Expected password: base64(174379 + passkey + 20160216165627)
        expected_raw = f"{shortcode}{passkey}{timestamp}"
        expected_password = base64.b64encode(expected_raw.encode()).decode()

        # Generate password
        generated_password = mpesa_service.generate_password(
            shortcode, passkey, timestamp
        )

        assert generated_password == expected_password

    def test_generate_password_with_different_inputs(
        self, mpesa_service: MPesaService
    ) -> None:
        """Test password generation with different inputs."""
        shortcode = "600000"
        passkey = "test_passkey_123456789"
        timestamp = "20250112153045"

        password = mpesa_service.generate_password(shortcode, passkey, timestamp)

        # Verify it's base64 encoded
        decoded = base64.b64decode(password).decode()
        assert decoded == f"{shortcode}{passkey}{timestamp}"

    def test_generate_timestamp_format(self, mpesa_service: MPesaService) -> None:
        """Test timestamp generation returns correct format."""
        timestamp = mpesa_service.generate_timestamp()

        # Verify format: YYYYMMDDHHmmss (14 characters)
        assert len(timestamp) == 14
        assert timestamp.isdigit()

        # Verify it's a valid datetime
        datetime.strptime(timestamp, "%Y%m%d%H%M%S")

    def test_generate_timestamp_is_current(self, mpesa_service: MPesaService) -> None:
        """Test timestamp generation returns current time."""
        before = datetime.now().replace(microsecond=0)
        timestamp = mpesa_service.generate_timestamp()
        after = datetime.now().replace(microsecond=0)

        # Parse generated timestamp
        generated_time = datetime.strptime(timestamp, "%Y%m%d%H%M%S")

        # Verify it's between before and after (within 1 second)
        assert before <= generated_time <= after or generated_time == before

    @pytest.mark.asyncio
    async def test_get_access_token_success(self, mpesa_service: MPesaService) -> None:
        """Test successful OAuth token generation."""
        mock_response_data = {
            "access_token": "test_token_abc123",
            "expires_in": "3599",
        }

        # Mock httpx.AsyncClient
        with patch("src.app.services.mpesa.httpx.AsyncClient") as mock_client:
            # Setup mock response
            mock_response = AsyncMock(spec=Response)
            mock_response.json.return_value = mock_response_data
            mock_response.raise_for_status = AsyncMock()

            # Setup mock client context manager
            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            # Clear token cache
            mpesa_service._token_cache.clear()

            # Get access token
            token = await mpesa_service.get_access_token()

            assert token == "test_token_abc123"
            assert "access_token" in mpesa_service._token_cache
            assert "expires_at" in mpesa_service._token_cache

    @pytest.mark.asyncio
    async def test_get_access_token_caching(self, mpesa_service: MPesaService) -> None:
        """Test access token caching logic."""
        mock_response_data = {
            "access_token": "cached_token_xyz",
            "expires_in": "3600",
        }

        with patch("src.app.services.mpesa.httpx.AsyncClient") as mock_client:
            # Setup mock
            mock_response = AsyncMock(spec=Response)
            mock_response.json.return_value = mock_response_data
            mock_response.raise_for_status = AsyncMock()

            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            # Clear cache
            mpesa_service._token_cache.clear()

            # First call - should make API request
            token1 = await mpesa_service.get_access_token()
            assert token1 == "cached_token_xyz"

            # Second call - should use cached token
            token2 = await mpesa_service.get_access_token()
            assert token2 == "cached_token_xyz"

            # Verify API was called only once
            assert mock_client_instance.get.call_count == 1

    @pytest.mark.asyncio
    async def test_get_access_token_expired_cache(
        self, mpesa_service: MPesaService
    ) -> None:
        """Test token refresh when cache is expired."""
        # Set expired token in cache
        mpesa_service._token_cache = {
            "access_token": "expired_token",
            "expires_at": time.time() - 100,  # Expired 100 seconds ago
        }

        mock_response_data = {
            "access_token": "new_token_123",
            "expires_in": "3600",
        }

        with patch("src.app.services.mpesa.httpx.AsyncClient") as mock_client:
            mock_response = AsyncMock(spec=Response)
            mock_response.json.return_value = mock_response_data
            mock_response.raise_for_status = AsyncMock()

            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            # Get token - should refresh
            token = await mpesa_service.get_access_token()

            assert token == "new_token_123"
            assert mock_client_instance.get.call_count == 1

    @pytest.mark.asyncio
    async def test_initiate_stk_push_payload_format(
        self, mpesa_service: MPesaService
    ) -> None:
        """Test STK Push request payload formatting."""
        phone_number = "254712345678"
        amount = 100
        account_reference = "INV-12345"
        transaction_desc = "Test payment"

        mock_stk_response = {
            "MerchantRequestID": "12345-67890-12345",
            "CheckoutRequestID": "ws_CO_12345",
            "ResponseCode": "0",
            "ResponseDescription": "Success",
            "CustomerMessage": "Success",
        }

        with patch("src.app.services.mpesa.httpx.AsyncClient") as mock_client:
            # Mock OAuth response
            mock_oauth_response = AsyncMock(spec=Response)
            mock_oauth_response.json.return_value = {
                "access_token": "test_token",
                "expires_in": "3600",
            }
            mock_oauth_response.raise_for_status = AsyncMock()

            # Mock STK Push response
            mock_stk_response_obj = AsyncMock(spec=Response)
            mock_stk_response_obj.json.return_value = mock_stk_response
            mock_stk_response_obj.raise_for_status = AsyncMock()

            # Setup mock client
            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_oauth_response
            mock_client_instance.post.return_value = mock_stk_response_obj
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            # Clear cache
            mpesa_service._token_cache.clear()

            # Initiate STK Push
            response = await mpesa_service.initiate_stk_push(
                phone_number=phone_number,
                amount=amount,
                account_reference=account_reference,
                transaction_desc=transaction_desc,
            )

            assert response == mock_stk_response

            # Verify STK Push was called with correct payload
            assert mock_client_instance.post.call_count == 1

            # Get the call arguments
            call_args = mock_client_instance.post.call_args
            payload = call_args.kwargs["json"]

            # Verify payload structure
            assert payload["BusinessShortCode"] == mpesa_service.shortcode
            assert payload["TransactionType"] == "CustomerPayBillOnline"
            assert payload["Amount"] == amount
            assert payload["PartyA"] == phone_number
            assert payload["PartyB"] == mpesa_service.shortcode
            assert payload["PhoneNumber"] == phone_number
            assert payload["CallBackURL"] == mpesa_service.callback_url
            assert payload["AccountReference"] == account_reference
            assert payload["TransactionDesc"] == transaction_desc
            assert "Password" in payload
            assert "Timestamp" in payload

    @pytest.mark.asyncio
    async def test_initiate_stk_push_error_handling(
        self, mpesa_service: MPesaService
    ) -> None:
        """Test STK Push error handling."""
        with patch("src.app.services.mpesa.httpx.AsyncClient") as mock_client:
            # Mock OAuth response
            mock_oauth_response = AsyncMock(spec=Response)
            mock_oauth_response.json.return_value = {
                "access_token": "test_token",
                "expires_in": "3600",
            }
            mock_oauth_response.raise_for_status = AsyncMock()

            # Mock STK Push failure
            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_oauth_response
            mock_client_instance.post.side_effect = Exception("Network error")
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            # Clear cache
            mpesa_service._token_cache.clear()

            # Expect exception
            with pytest.raises(Exception, match="Network error"):
                await mpesa_service.initiate_stk_push(
                    phone_number="254712345678",
                    amount=100,
                    account_reference="INV-123",
                    transaction_desc="Test",
                )