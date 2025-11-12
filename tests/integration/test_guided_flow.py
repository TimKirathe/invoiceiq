"""
Integration tests for WhatsApp guided invoice creation flow.

This module tests the complete end-to-end guided flow with mocked WhatsApp API calls.
"""

import pytest
from unittest.mock import AsyncMock, patch

from src.app.services.whatsapp import ConversationStateManager, WhatsAppService


@pytest.fixture(autouse=True)
def clear_state():
    """Clear state manager before each test."""
    ConversationStateManager.states.clear()
    yield
    ConversationStateManager.states.clear()


@pytest.fixture
def mock_whatsapp_api():
    """Mock WhatsApp API responses."""
    with patch("httpx.AsyncClient.post") as mock_post:
        # Mock successful API response
        mock_response = AsyncMock()
        mock_response.status_code = 200
        # Make json() return a coroutine that returns the data
        async def json_return():
            return {
                "messaging_product": "whatsapp",
                "contacts": [{"input": "254712345678", "wa_id": "254712345678"}],
                "messages": [{"id": "wamid.test123"}],
            }
        mock_response.json = json_return
        mock_response.raise_for_status = AsyncMock()
        mock_post.return_value = mock_response
        yield mock_post


class TestGuidedFlowIntegration:
    """Integration tests for complete guided flow."""

    @pytest.mark.asyncio
    async def test_complete_guided_flow_with_name(self, mock_whatsapp_api):
        """Test complete guided flow with customer name."""
        service = WhatsAppService()
        user_id = "254712345678"

        # Step 1: Start flow
        result = service.handle_guided_flow(user_id, "invoice")
        assert result["action"] == "started"
        await service.send_message(user_id, result["response"])
        assert mock_whatsapp_api.called

        # Step 2: Provide phone
        result = service.handle_guided_flow(user_id, "254787654321")
        assert result["action"] == "phone_collected"
        await service.send_message(user_id, result["response"])

        # Step 3: Provide name
        result = service.handle_guided_flow(user_id, "John Doe")
        assert result["action"] == "name_collected"
        await service.send_message(user_id, result["response"])

        # Step 4: Provide amount
        result = service.handle_guided_flow(user_id, "1500")
        assert result["action"] == "amount_collected"
        await service.send_message(user_id, result["response"])

        # Step 5: Provide description
        result = service.handle_guided_flow(user_id, "Website development services")
        assert result["action"] == "ready"
        await service.send_message(user_id, result["response"])

        # Step 6: Confirm
        result = service.handle_guided_flow(user_id, "confirm")
        assert result["action"] == "confirmed"
        assert result["invoice_data"]["phone"] == "254787654321"
        assert result["invoice_data"]["name"] == "John Doe"
        assert result["invoice_data"]["amount_cents"] == 150000
        assert result["invoice_data"]["description"] == "Website development services"

        # Verify state is cleared
        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_IDLE

        # Verify WhatsApp API was called multiple times
        assert mock_whatsapp_api.call_count >= 5

    @pytest.mark.asyncio
    async def test_complete_guided_flow_without_name(self, mock_whatsapp_api):
        """Test complete guided flow with skipped customer name."""
        service = WhatsAppService()
        user_id = "254712345678"

        # Start flow
        result = service.handle_guided_flow(user_id, "invoice")
        await service.send_message(user_id, result["response"])

        # Provide phone
        result = service.handle_guided_flow(user_id, "254787654321")
        await service.send_message(user_id, result["response"])

        # Skip name
        result = service.handle_guided_flow(user_id, "-")
        assert result["action"] == "name_collected"
        await service.send_message(user_id, result["response"])

        # Provide amount
        result = service.handle_guided_flow(user_id, "2000")
        await service.send_message(user_id, result["response"])

        # Provide description
        result = service.handle_guided_flow(user_id, "Graphic design work")
        await service.send_message(user_id, result["response"])

        # Confirm
        result = service.handle_guided_flow(user_id, "confirm")
        assert result["action"] == "confirmed"
        assert result["invoice_data"]["name"] is None
        assert result["invoice_data"]["phone"] == "254787654321"

    @pytest.mark.asyncio
    async def test_guided_flow_with_cancellation(self, mock_whatsapp_api):
        """Test cancelling guided flow mid-way."""
        service = WhatsAppService()
        user_id = "254712345678"

        # Start flow
        result = service.handle_guided_flow(user_id, "invoice")
        await service.send_message(user_id, result["response"])

        # Provide phone
        result = service.handle_guided_flow(user_id, "254787654321")
        await service.send_message(user_id, result["response"])

        # Cancel at name collection stage
        result = service.handle_guided_flow(user_id, "cancel")
        assert result["action"] == "cancelled"
        await service.send_message(user_id, result["response"])

        # Verify state is cleared
        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_IDLE

    @pytest.mark.asyncio
    async def test_guided_flow_with_validation_errors(self, mock_whatsapp_api):
        """Test guided flow with validation errors and retries."""
        service = WhatsAppService()
        user_id = "254712345678"

        # Start flow
        result = service.handle_guided_flow(user_id, "invoice")
        await service.send_message(user_id, result["response"])

        # Provide invalid phone (retry)
        result = service.handle_guided_flow(user_id, "123456")
        assert result["action"] == "validation_error"
        await service.send_message(user_id, result["response"])

        # Provide valid phone
        result = service.handle_guided_flow(user_id, "254787654321")
        assert result["action"] == "phone_collected"
        await service.send_message(user_id, result["response"])

        # Provide valid name
        result = service.handle_guided_flow(user_id, "Jane Doe")
        await service.send_message(user_id, result["response"])

        # Provide invalid amount (retry)
        result = service.handle_guided_flow(user_id, "abc")
        assert result["action"] == "validation_error"
        await service.send_message(user_id, result["response"])

        # Provide valid amount
        result = service.handle_guided_flow(user_id, "500")
        assert result["action"] == "amount_collected"
        await service.send_message(user_id, result["response"])

        # Provide invalid description (too short, retry)
        result = service.handle_guided_flow(user_id, "AB")
        assert result["action"] == "validation_error"
        await service.send_message(user_id, result["response"])

        # Provide valid description
        result = service.handle_guided_flow(user_id, "Valid description")
        assert result["action"] == "ready"
        await service.send_message(user_id, result["response"])

        # Confirm
        result = service.handle_guided_flow(user_id, "confirm")
        assert result["action"] == "confirmed"

    @pytest.mark.asyncio
    async def test_multiple_users_concurrent_flows(self, mock_whatsapp_api):
        """Test multiple users can have independent concurrent flows."""
        service = WhatsAppService()
        user1 = "254712345678"
        user2 = "254787654321"

        # User 1: Start flow
        result1 = service.handle_guided_flow(user1, "invoice")
        await service.send_message(user1, result1["response"])

        # User 2: Start flow
        result2 = service.handle_guided_flow(user2, "invoice")
        await service.send_message(user2, result2["response"])

        # User 1: Provide phone
        result1 = service.handle_guided_flow(user1, "254700000001")
        assert result1["action"] == "phone_collected"

        # User 2: Provide phone
        result2 = service.handle_guided_flow(user2, "254700000002")
        assert result2["action"] == "phone_collected"

        # Verify states are independent
        state1 = ConversationStateManager.get_state(user1)
        state2 = ConversationStateManager.get_state(user2)

        assert state1["data"]["phone"] == "254700000001"
        assert state2["data"]["phone"] == "254700000002"
        assert state1["state"] == ConversationStateManager.STATE_COLLECT_NAME
        assert state2["state"] == ConversationStateManager.STATE_COLLECT_NAME

    @pytest.mark.asyncio
    async def test_ready_state_cancel_and_restart(self, mock_whatsapp_api):
        """Test cancelling at ready state and restarting flow."""
        service = WhatsAppService()
        user_id = "254712345678"

        # Complete flow to ready state
        service.handle_guided_flow(user_id, "invoice")
        service.handle_guided_flow(user_id, "254787654321")
        service.handle_guided_flow(user_id, "John Doe")
        service.handle_guided_flow(user_id, "1000")
        result = service.handle_guided_flow(user_id, "Test description")
        assert result["action"] == "ready"

        # Cancel at ready state
        result = service.handle_guided_flow(user_id, "cancel")
        assert result["action"] == "cancelled"

        # Verify state is cleared
        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_IDLE

        # Restart flow
        result = service.handle_guided_flow(user_id, "invoice")
        assert result["action"] == "started"
        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_COLLECT_PHONE

    @pytest.mark.asyncio
    async def test_send_message_api_call_format(self, mock_whatsapp_api):
        """Test that send_message makes correct API call."""
        service = WhatsAppService()

        await service.send_message("254712345678", "Test message")

        # Verify the API call was made
        assert mock_whatsapp_api.called
        call_args = mock_whatsapp_api.call_args

        # Check URL
        url = call_args[0][0]
        assert "/messages" in url
        assert service.waba_phone_id in url

        # Check payload
        payload = call_args[1]["json"]
        assert payload["messaging_product"] == "whatsapp"
        assert payload["recipient_type"] == "individual"
        assert payload["to"] == "254712345678"
        assert payload["type"] == "text"
        assert payload["text"]["body"] == "Test message"

        # Check headers
        headers = call_args[1]["headers"]
        assert "Authorization" in headers
        assert "Bearer" in headers["Authorization"]
        assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_send_message_api_error_handling(self):
        """Test error handling when WhatsApp API fails."""
        service = WhatsAppService()

        with patch("httpx.AsyncClient.post") as mock_post:
            # Mock API error - directly raise exception from post call
            from httpx import HTTPStatusError, Request, Response

            mock_response = Response(400, text="Bad Request")
            mock_request = Request("POST", "https://example.com")

            async def raise_http_error(*args, **kwargs):
                raise HTTPStatusError("Bad Request", request=mock_request, response=mock_response)

            mock_post.side_effect = raise_http_error

            # Should raise exception
            with pytest.raises(Exception) as exc_info:
                await service.send_message("254712345678", "Test message")

            # The service wraps the HTTPStatusError in a generic Exception
            assert "Failed to send message" in str(exc_info.value) or "Bad Request" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_message_parsing_and_flow_integration(self, mock_whatsapp_api):
        """Test message parsing integrates with guided flow."""
        service = WhatsAppService()
        user_id = "254712345678"

        # Simulate webhook payload for "invoice" command
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "123",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "messages": [
                                    {
                                        "from": user_id,
                                        "id": "wamid.123",
                                        "timestamp": "1749416383",
                                        "type": "text",
                                        "text": {"body": "invoice"},
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }

        # Parse the message
        parsed = service.parse_incoming_message(payload)
        assert parsed is not None
        assert parsed["text"] == "invoice"
        assert parsed["from"] == user_id

        # Handle the command
        command_info = service.parse_command(parsed["text"])
        assert command_info["command"] == "start_guided"

        # Process guided flow
        result = service.handle_guided_flow(user_id, parsed["text"])
        assert result["action"] == "started"

        # Send response
        await service.send_message(user_id, result["response"])
        assert mock_whatsapp_api.called