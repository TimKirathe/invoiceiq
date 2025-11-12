"""
Unit tests for conversation state machine.

This module tests the ConversationStateManager and WhatsAppService's
state transition logic for the guided invoice creation flow.
"""

import pytest

from src.app.services.whatsapp import ConversationStateManager, WhatsAppService


@pytest.fixture(autouse=True)
def clear_state():
    """Clear state manager before each test."""
    ConversationStateManager.states.clear()
    yield
    ConversationStateManager.states.clear()


class TestConversationStateManager:
    """Tests for ConversationStateManager class."""

    def test_initial_state_is_idle(self):
        """Test that new users start in IDLE state."""
        state = ConversationStateManager.get_state("254712345678")
        assert state["state"] == ConversationStateManager.STATE_IDLE
        assert state["data"] == {}

    def test_set_state(self):
        """Test setting a new state."""
        user_id = "254712345678"
        ConversationStateManager.set_state(
            user_id, ConversationStateManager.STATE_COLLECT_PHONE, {"test": "data"}
        )

        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_COLLECT_PHONE
        assert state["data"] == {"test": "data"}

    def test_update_data(self):
        """Test updating state data."""
        user_id = "254712345678"
        ConversationStateManager.set_state(user_id, ConversationStateManager.STATE_COLLECT_PHONE)
        ConversationStateManager.update_data(user_id, "phone", "254712345678")
        ConversationStateManager.update_data(user_id, "name", "John Doe")

        state = ConversationStateManager.get_state(user_id)
        assert state["data"]["phone"] == "254712345678"
        assert state["data"]["name"] == "John Doe"

    def test_clear_state(self):
        """Test clearing state back to IDLE."""
        user_id = "254712345678"
        ConversationStateManager.set_state(
            user_id,
            ConversationStateManager.STATE_COLLECT_AMOUNT,
            {"phone": "254712345678", "name": "John Doe"},
        )
        ConversationStateManager.clear_state(user_id)

        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_IDLE
        assert state["data"] == {}

    def test_multiple_users_independent_states(self):
        """Test that different users have independent states."""
        user1 = "254712345678"
        user2 = "254787654321"

        ConversationStateManager.set_state(user1, ConversationStateManager.STATE_COLLECT_PHONE)
        ConversationStateManager.set_state(user2, ConversationStateManager.STATE_COLLECT_AMOUNT)

        state1 = ConversationStateManager.get_state(user1)
        state2 = ConversationStateManager.get_state(user2)

        assert state1["state"] == ConversationStateManager.STATE_COLLECT_PHONE
        assert state2["state"] == ConversationStateManager.STATE_COLLECT_AMOUNT


class TestGuidedFlowStateMachine:
    """Tests for handle_guided_flow state transitions."""

    def test_start_guided_flow_from_idle(self):
        """Test starting guided flow from IDLE state."""
        service = WhatsAppService()
        user_id = "254712345678"

        result = service.handle_guided_flow(user_id, "invoice")

        assert result["action"] == "started"
        assert "customer's phone number" in result["response"].lower()

        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_COLLECT_PHONE

    def test_collect_phone_valid(self):
        """Test collecting a valid phone number."""
        service = WhatsAppService()
        user_id = "254712345678"

        # Set state to COLLECT_PHONE
        ConversationStateManager.set_state(user_id, ConversationStateManager.STATE_COLLECT_PHONE)

        result = service.handle_guided_flow(user_id, "254787654321")

        assert result["action"] == "phone_collected"
        assert "customer's name" in result["response"].lower()

        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_COLLECT_NAME
        assert state["data"]["phone"] == "254787654321"

    def test_collect_phone_invalid(self):
        """Test handling invalid phone number."""
        service = WhatsAppService()
        user_id = "254712345678"

        ConversationStateManager.set_state(user_id, ConversationStateManager.STATE_COLLECT_PHONE)

        result = service.handle_guided_flow(user_id, "123456")

        assert result["action"] == "validation_error"
        assert "invalid" in result["response"].lower()

        # Should stay in same state
        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_COLLECT_PHONE

    def test_collect_name_with_value(self):
        """Test collecting customer name."""
        service = WhatsAppService()
        user_id = "254712345678"

        ConversationStateManager.set_state(
            user_id, ConversationStateManager.STATE_COLLECT_NAME, {"phone": "254787654321"}
        )

        result = service.handle_guided_flow(user_id, "John Doe")

        assert result["action"] == "name_collected"
        assert "amount" in result["response"].lower()

        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_COLLECT_AMOUNT
        assert state["data"]["name"] == "John Doe"

    def test_collect_name_skip(self):
        """Test skipping customer name with '-'."""
        service = WhatsAppService()
        user_id = "254712345678"

        ConversationStateManager.set_state(
            user_id, ConversationStateManager.STATE_COLLECT_NAME, {"phone": "254787654321"}
        )

        result = service.handle_guided_flow(user_id, "-")

        assert result["action"] == "name_collected"
        assert "amount" in result["response"].lower()

        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_COLLECT_AMOUNT
        assert state["data"]["name"] is None

    def test_collect_name_too_short(self):
        """Test validation error for too short name."""
        service = WhatsAppService()
        user_id = "254712345678"

        ConversationStateManager.set_state(
            user_id, ConversationStateManager.STATE_COLLECT_NAME, {"phone": "254787654321"}
        )

        result = service.handle_guided_flow(user_id, "A")

        assert result["action"] == "validation_error"
        assert "2 and 60 characters" in result["response"]

        # Should stay in same state
        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_COLLECT_NAME

    def test_collect_name_too_long(self):
        """Test validation error for too long name."""
        service = WhatsAppService()
        user_id = "254712345678"

        ConversationStateManager.set_state(
            user_id, ConversationStateManager.STATE_COLLECT_NAME, {"phone": "254787654321"}
        )

        long_name = "A" * 61
        result = service.handle_guided_flow(user_id, long_name)

        assert result["action"] == "validation_error"
        assert "2 and 60 characters" in result["response"]

        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_COLLECT_NAME

    def test_collect_amount_valid(self):
        """Test collecting a valid amount."""
        service = WhatsAppService()
        user_id = "254712345678"

        ConversationStateManager.set_state(
            user_id,
            ConversationStateManager.STATE_COLLECT_AMOUNT,
            {"phone": "254787654321", "name": "John Doe"},
        )

        result = service.handle_guided_flow(user_id, "500")

        assert result["action"] == "amount_collected"
        assert "invoice for" in result["response"].lower()

        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_COLLECT_DESCRIPTION
        assert state["data"]["amount_cents"] == 50000  # 500 KES = 50000 cents

    def test_collect_amount_invalid_non_numeric(self):
        """Test validation error for non-numeric amount."""
        service = WhatsAppService()
        user_id = "254712345678"

        ConversationStateManager.set_state(
            user_id,
            ConversationStateManager.STATE_COLLECT_AMOUNT,
            {"phone": "254787654321", "name": "John Doe"},
        )

        result = service.handle_guided_flow(user_id, "five hundred")

        assert result["action"] == "validation_error"
        assert "valid amount" in result["response"].lower()

        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_COLLECT_AMOUNT

    def test_collect_amount_invalid_too_small(self):
        """Test validation error for amount less than 1."""
        service = WhatsAppService()
        user_id = "254712345678"

        ConversationStateManager.set_state(
            user_id,
            ConversationStateManager.STATE_COLLECT_AMOUNT,
            {"phone": "254787654321", "name": "John Doe"},
        )

        result = service.handle_guided_flow(user_id, "0")

        assert result["action"] == "validation_error"
        assert "minimum 1" in result["response"].lower()

        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_COLLECT_AMOUNT

    def test_collect_description_valid(self):
        """Test collecting a valid description."""
        service = WhatsAppService()
        user_id = "254712345678"

        ConversationStateManager.set_state(
            user_id,
            ConversationStateManager.STATE_COLLECT_DESCRIPTION,
            {"phone": "254787654321", "name": "John Doe", "amount_cents": 50000},
        )

        result = service.handle_guided_flow(user_id, "Website design services")

        assert result["action"] == "ready"
        assert "Ready to send" in result["response"]
        assert "John Doe" in result["response"]
        assert "254787654321" in result["response"]
        assert "500" in result["response"]
        assert "Website design services" in result["response"]

        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_READY
        assert state["data"]["description"] == "Website design services"

    def test_collect_description_too_short(self):
        """Test validation error for too short description."""
        service = WhatsAppService()
        user_id = "254712345678"

        ConversationStateManager.set_state(
            user_id,
            ConversationStateManager.STATE_COLLECT_DESCRIPTION,
            {"phone": "254787654321", "name": "John Doe", "amount_cents": 50000},
        )

        result = service.handle_guided_flow(user_id, "AB")

        assert result["action"] == "validation_error"
        assert "3 and 120 characters" in result["response"]

        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_COLLECT_DESCRIPTION

    def test_collect_description_too_long(self):
        """Test validation error for too long description."""
        service = WhatsAppService()
        user_id = "254712345678"

        ConversationStateManager.set_state(
            user_id,
            ConversationStateManager.STATE_COLLECT_DESCRIPTION,
            {"phone": "254787654321", "name": "John Doe", "amount_cents": 50000},
        )

        long_desc = "A" * 121
        result = service.handle_guided_flow(user_id, long_desc)

        assert result["action"] == "validation_error"
        assert "3 and 120 characters" in result["response"]

        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_COLLECT_DESCRIPTION

    def test_ready_state_confirm(self):
        """Test confirming invoice in READY state."""
        service = WhatsAppService()
        user_id = "254712345678"

        data = {
            "phone": "254787654321",
            "name": "John Doe",
            "amount_cents": 50000,
            "description": "Website design",
        }
        ConversationStateManager.set_state(user_id, ConversationStateManager.STATE_READY, data)

        result = service.handle_guided_flow(user_id, "confirm")

        assert result["action"] == "confirmed"
        assert "invoice_data" in result
        assert result["invoice_data"]["phone"] == "254787654321"

        # State should be cleared
        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_IDLE

    def test_ready_state_cancel(self):
        """Test cancelling invoice in READY state."""
        service = WhatsAppService()
        user_id = "254712345678"

        data = {
            "phone": "254787654321",
            "name": "John Doe",
            "amount_cents": 50000,
            "description": "Website design",
        }
        ConversationStateManager.set_state(user_id, ConversationStateManager.STATE_READY, data)

        result = service.handle_guided_flow(user_id, "cancel")

        assert result["action"] == "cancelled"
        assert "cancelled" in result["response"].lower()

        # State should be cleared
        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_IDLE

    def test_ready_state_invalid_input(self):
        """Test invalid input in READY state."""
        service = WhatsAppService()
        user_id = "254712345678"

        data = {
            "phone": "254787654321",
            "name": "John Doe",
            "amount_cents": 50000,
            "description": "Website design",
        }
        ConversationStateManager.set_state(user_id, ConversationStateManager.STATE_READY, data)

        result = service.handle_guided_flow(user_id, "random text")

        assert result["action"] == "awaiting_confirmation"
        assert "confirm" in result["response"].lower()

        # State should remain READY
        state = ConversationStateManager.get_state(user_id)
        assert state["state"] == ConversationStateManager.STATE_READY

    def test_cancel_command_at_any_state(self):
        """Test that 'cancel' works at any collection state."""
        service = WhatsAppService()
        user_id = "254712345678"

        # Test cancel at COLLECT_PHONE
        ConversationStateManager.set_state(user_id, ConversationStateManager.STATE_COLLECT_PHONE)
        result = service.handle_guided_flow(user_id, "cancel")
        assert result["action"] == "cancelled"
        assert ConversationStateManager.get_state(user_id)["state"] == ConversationStateManager.STATE_IDLE

        # Test cancel at COLLECT_NAME
        ConversationStateManager.set_state(user_id, ConversationStateManager.STATE_COLLECT_NAME)
        result = service.handle_guided_flow(user_id, "cancel")
        assert result["action"] == "cancelled"

        # Test cancel at COLLECT_AMOUNT
        ConversationStateManager.set_state(user_id, ConversationStateManager.STATE_COLLECT_AMOUNT)
        result = service.handle_guided_flow(user_id, "cancel")
        assert result["action"] == "cancelled"

        # Test cancel at COLLECT_DESCRIPTION
        ConversationStateManager.set_state(user_id, ConversationStateManager.STATE_COLLECT_DESCRIPTION)
        result = service.handle_guided_flow(user_id, "cancel")
        assert result["action"] == "cancelled"

    def test_complete_flow_without_name(self):
        """Test complete guided flow without customer name."""
        service = WhatsAppService()
        user_id = "254712345678"

        # Start flow
        result = service.handle_guided_flow(user_id, "invoice")
        assert result["action"] == "started"

        # Provide phone
        result = service.handle_guided_flow(user_id, "254787654321")
        assert result["action"] == "phone_collected"

        # Skip name
        result = service.handle_guided_flow(user_id, "-")
        assert result["action"] == "name_collected"

        # Provide amount
        result = service.handle_guided_flow(user_id, "1000")
        assert result["action"] == "amount_collected"

        # Provide description
        result = service.handle_guided_flow(user_id, "Consultation services")
        assert result["action"] == "ready"
        assert "Not provided" in result["response"]  # Name should show as "Not provided"

        # Confirm
        result = service.handle_guided_flow(user_id, "confirm")
        assert result["action"] == "confirmed"
        assert result["invoice_data"]["name"] is None

    def test_complete_flow_with_name(self):
        """Test complete guided flow with customer name."""
        service = WhatsAppService()
        user_id = "254712345678"

        # Start flow
        result = service.handle_guided_flow(user_id, "invoice")
        assert result["action"] == "started"

        # Provide phone
        result = service.handle_guided_flow(user_id, "254787654321")
        assert result["action"] == "phone_collected"

        # Provide name
        result = service.handle_guided_flow(user_id, "Jane Smith")
        assert result["action"] == "name_collected"

        # Provide amount
        result = service.handle_guided_flow(user_id, "2500")
        assert result["action"] == "amount_collected"

        # Provide description
        result = service.handle_guided_flow(user_id, "Professional photography session")
        assert result["action"] == "ready"
        assert "Jane Smith" in result["response"]

        # Confirm
        result = service.handle_guided_flow(user_id, "confirm")
        assert result["action"] == "confirmed"
        assert result["invoice_data"]["name"] == "Jane Smith"
        assert result["invoice_data"]["phone"] == "254787654321"
        assert result["invoice_data"]["amount_cents"] == 250000
        assert result["invoice_data"]["description"] == "Professional photography session"