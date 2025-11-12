"""
Unit tests for WhatsApp message and command parser.

This module tests the WhatsAppService's ability to parse incoming WhatsApp
messages and recognize various commands.
"""

from src.app.services.whatsapp import WhatsAppService


class TestMessageParser:
    """Tests for parse_incoming_message method."""

    def test_parse_text_message(self):
        """Test parsing a simple text message."""
        service = WhatsAppService()
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "123",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "15550783881",
                                    "phone_number_id": "106540352242922",
                                },
                                "messages": [
                                    {
                                        "from": "254712345678",
                                        "id": "wamid.123",
                                        "timestamp": "1749416383",
                                        "type": "text",
                                        "text": {"body": "Hello, this is a test message"},
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }

        result = service.parse_incoming_message(payload)
        assert result is not None
        assert result["text"] == "Hello, this is a test message"
        assert result["from"] == "254712345678"
        assert result["type"] == "text"

    def test_parse_interactive_button_message(self):
        """Test parsing an interactive button click message."""
        service = WhatsAppService()
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "123",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "15550783881",
                                    "phone_number_id": "106540352242922",
                                },
                                "messages": [
                                    {
                                        "from": "254712345678",
                                        "id": "wamid.123",
                                        "timestamp": "1749416383",
                                        "type": "interactive",
                                        "interactive": {
                                            "type": "button_reply",
                                            "button_reply": {
                                                "id": "confirm_button",
                                                "title": "Confirm",
                                            },
                                        },
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }

        result = service.parse_incoming_message(payload)
        assert result is not None
        assert result["text"] == "confirm_button"
        assert result["from"] == "254712345678"
        assert result["type"] == "interactive"

    def test_parse_invalid_phone_number(self):
        """Test parsing message with invalid phone number format."""
        service = WhatsAppService()
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
                                        "from": "123456",  # Invalid MSISDN
                                        "id": "wamid.123",
                                        "timestamp": "1749416383",
                                        "type": "text",
                                        "text": {"body": "Hello"},
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }

        result = service.parse_incoming_message(payload)
        assert result is None

    def test_parse_empty_payload(self):
        """Test parsing empty or malformed payload."""
        service = WhatsAppService()

        # Empty payload
        assert service.parse_incoming_message({}) is None

        # No entry
        assert service.parse_incoming_message({"object": "whatsapp_business_account"}) is None

        # No changes
        assert service.parse_incoming_message({"entry": [{}]}) is None

        # No messages
        assert service.parse_incoming_message({"entry": [{"changes": [{"value": {}}]}]}) is None

    def test_parse_status_update(self):
        """Test parsing a status update (no messages field)."""
        service = WhatsAppService()
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "123",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "15550783881",
                                    "phone_number_id": "106540352242922",
                                },
                                "statuses": [
                                    {
                                        "id": "wamid.123",
                                        "status": "delivered",
                                        "timestamp": "1749416383",
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }

        result = service.parse_incoming_message(payload)
        assert result is None


class TestCommandParser:
    """Tests for parse_command method."""

    def test_help_command(self):
        """Test parsing help command."""
        service = WhatsAppService()
        result = service.parse_command("help")
        assert result["command"] == "help"
        assert result["params"] == {}

    def test_start_guided_flow_invoice(self):
        """Test parsing 'invoice' to start guided flow."""
        service = WhatsAppService()
        result = service.parse_command("invoice")
        assert result["command"] == "start_guided"
        assert result["params"] == {}

    def test_start_guided_flow_new_invoice(self):
        """Test parsing 'new invoice' to start guided flow."""
        service = WhatsAppService()
        result = service.parse_command("new invoice")
        assert result["command"] == "start_guided"
        assert result["params"] == {}

    def test_one_line_invoice_with_phone(self):
        """Test parsing one-line invoice command with phone number."""
        service = WhatsAppService()
        result = service.parse_command("invoice 254712345678 500 Website design")
        assert result["command"] == "invoice"
        assert result["params"]["phone"] == "254712345678"
        assert result["params"]["amount"] == 500
        assert result["params"]["description"] == "Website design"

    def test_one_line_invoice_with_name(self):
        """Test parsing one-line invoice command with customer name."""
        service = WhatsAppService()
        result = service.parse_command("invoice John Doe 1000 Consultation fee")
        assert result["command"] == "invoice"
        assert result["params"]["name"] == "John Doe"
        assert result["params"]["amount"] == 1000
        assert result["params"]["description"] == "Consultation fee"

    def test_one_line_invoice_with_long_description(self):
        """Test parsing one-line invoice command with multi-word description."""
        service = WhatsAppService()
        result = service.parse_command(
            "invoice 254712345678 2500 Professional web development services for e-commerce site"
        )
        assert result["command"] == "invoice"
        assert result["params"]["phone"] == "254712345678"
        assert result["params"]["amount"] == 2500
        assert (
            result["params"]["description"]
            == "Professional web development services for e-commerce site"
        )

    def test_one_line_invoice_case_insensitive(self):
        """Test that invoice command is case-insensitive."""
        service = WhatsAppService()
        result = service.parse_command("INVOICE 254712345678 500 Test")
        assert result["command"] == "invoice"
        assert result["params"]["phone"] == "254712345678"

    def test_remind_command(self):
        """Test parsing remind command."""
        service = WhatsAppService()
        result = service.parse_command("remind INV-123")
        assert result["command"] == "remind"
        assert result["params"]["invoice_id"] == "inv-123"

    def test_cancel_command(self):
        """Test parsing cancel command."""
        service = WhatsAppService()
        result = service.parse_command("cancel INV-456")
        assert result["command"] == "cancel"
        assert result["params"]["invoice_id"] == "inv-456"

    def test_unknown_command(self):
        """Test parsing unknown command."""
        service = WhatsAppService()
        result = service.parse_command("random text that doesn't match")
        assert result["command"] == "unknown"
        assert result["params"] == {}

    def test_empty_string(self):
        """Test parsing empty string."""
        service = WhatsAppService()
        result = service.parse_command("")
        assert result["command"] == "unknown"
        assert result["params"] == {}

    def test_whitespace_handling(self):
        """Test that extra whitespace is handled correctly."""
        service = WhatsAppService()

        # Leading/trailing whitespace
        result = service.parse_command("  help  ")
        assert result["command"] == "help"

        # Multiple spaces in command
        result = service.parse_command("invoice  254712345678  500  Test")
        assert result["command"] == "invoice"
        assert result["params"]["phone"] == "254712345678"
        assert result["params"]["amount"] == 500

    def test_special_characters_in_description(self):
        """Test handling special characters in description."""
        service = WhatsAppService()
        result = service.parse_command("invoice 254712345678 500 Website design & development!")
        assert result["command"] == "invoice"
        assert result["params"]["description"] == "Website design & development!"

    def test_invoice_short_description(self):
        """Test invoice command with minimum length description (3 chars)."""
        service = WhatsAppService()
        result = service.parse_command("invoice 254712345678 500 ABC")
        assert result["command"] == "invoice"
        assert result["params"]["description"] == "ABC"

    def test_invoice_too_short_description(self):
        """Test invoice command with too short description (< 3 chars)."""
        service = WhatsAppService()
        result = service.parse_command("invoice 254712345678 500 AB")
        # Should not match because description is too short
        assert result["command"] == "unknown"

    def test_invoice_with_name_containing_spaces(self):
        """Test invoice with multi-word customer name."""
        service = WhatsAppService()
        result = service.parse_command("invoice Mary Jane Watson 750 Photography session")
        assert result["command"] == "invoice"
        assert result["params"]["name"] == "Mary Jane Watson"
        assert result["params"]["amount"] == 750
        assert result["params"]["description"] == "Photography session"