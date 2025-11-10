"""
Comprehensive tests for Pydantic schemas.

Tests all validation rules, field constraints, and edge cases for
InvoiceCreate, InvoiceResponse, PaymentCreate, PaymentResponse, and
WhatsAppWebhookEvent schemas.
"""

from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.app.schemas import (
    ButtonReply,
    Change,
    Contact,
    Entry,
    InvoiceCreate,
    InvoiceResponse,
    InteractiveMessage,
    Message,
    Metadata,
    PaymentCreate,
    PaymentResponse,
    TextMessage,
    Value,
    WhatsAppWebhookEvent,
)


# ============================================================================
# InvoiceCreate Tests
# ============================================================================


class TestInvoiceCreate:
    """Test suite for InvoiceCreate schema."""

    def test_valid_invoice_create(self):
        """Test creating a valid invoice."""
        data = {
            "msisdn": "254712345678",
            "customer_name": "John Doe",
            "amount_cents": 10000,
            "description": "Payment for services",
        }
        invoice = InvoiceCreate(**data)
        assert invoice.msisdn == "254712345678"
        assert invoice.customer_name == "John Doe"
        assert invoice.amount_cents == 10000
        assert invoice.description == "Payment for services"

    def test_valid_invoice_create_without_customer_name(self):
        """Test creating an invoice without customer name (optional field)."""
        data = {
            "msisdn": "254712345678",
            "amount_cents": 5000,
            "description": "Test payment",
        }
        invoice = InvoiceCreate(**data)
        assert invoice.msisdn == "254712345678"
        assert invoice.customer_name is None
        assert invoice.amount_cents == 5000

    def test_valid_invoice_create_with_null_customer_name(self):
        """Test creating an invoice with explicitly null customer name."""
        data = {
            "msisdn": "254712345678",
            "customer_name": None,
            "amount_cents": 5000,
            "description": "Test payment",
        }
        invoice = InvoiceCreate(**data)
        assert invoice.customer_name is None

    @pytest.mark.parametrize(
        "invalid_msisdn,expected_error",
        [
            ("25471234567", "Invalid phone number format"),  # Too short
            ("2547123456789", "Invalid phone number format"),  # Too long
            ("254812345678", "Invalid phone number format"),  # Wrong prefix (not 7)
            ("+254712345678", "Invalid phone number format"),  # Has + prefix
            ("0712345678", "Invalid phone number format"),  # Local format
            ("712345678", "Invalid phone number format"),  # Missing country code
            ("", "Phone number cannot be empty"),  # Empty string
            ("   ", "Phone number cannot be empty"),  # Whitespace only
            ("254712ABCDEF", "Invalid phone number format"),  # Contains letters
            ("254-712-345678", "Invalid phone number format"),  # Contains dashes
        ],
    )
    def test_invalid_msisdn_formats(self, invalid_msisdn, expected_error):
        """Test validation fails for invalid MSISDN formats."""
        data = {
            "msisdn": invalid_msisdn,
            "amount_cents": 10000,
            "description": "Test payment",
        }
        with pytest.raises(ValidationError) as exc_info:
            InvoiceCreate(**data)
        errors = exc_info.value.errors()
        assert any(expected_error in str(error["ctx"]["error"]) for error in errors)

    @pytest.mark.parametrize(
        "amount,should_pass",
        [
            (99, False),  # Below minimum
            (100, True),  # Exactly minimum
            (101, True),  # Above minimum
            (10000, True),  # Normal amount
            (1000000, True),  # Large amount
        ],
    )
    def test_amount_validation(self, amount, should_pass):
        """Test amount validation (minimum 100 cents)."""
        data = {
            "msisdn": "254712345678",
            "amount_cents": amount,
            "description": "Test payment",
        }
        if should_pass:
            invoice = InvoiceCreate(**data)
            assert invoice.amount_cents == amount
        else:
            with pytest.raises(ValidationError) as exc_info:
                InvoiceCreate(**data)
            errors = exc_info.value.errors()
            # Pydantic's ge=100 constraint triggers with this error
            assert any(
                error["type"] == "greater_than_equal" and error["ctx"]["ge"] == 100
                for error in errors
            )

    @pytest.mark.parametrize(
        "description,should_pass",
        [
            ("AB", False),  # Too short (2 chars)
            ("ABC", True),  # Exactly 3 chars
            ("ABCD", True),  # 4 chars
            ("A valid description", True),  # Normal length
            ("X" * 120, True),  # Exactly 120 chars
            ("X" * 121, False),  # Too long (121 chars)
            ("   ABC   ", True),  # With whitespace (will be stripped)
        ],
    )
    def test_description_validation(self, description, should_pass):
        """Test description length validation (3-120 characters)."""
        data = {
            "msisdn": "254712345678",
            "amount_cents": 10000,
            "description": description,
        }
        if should_pass:
            invoice = InvoiceCreate(**data)
            assert len(invoice.description.strip()) >= 3
            assert len(invoice.description.strip()) <= 120
        else:
            with pytest.raises(ValidationError):
                InvoiceCreate(**data)

    @pytest.mark.parametrize(
        "customer_name,should_pass",
        [
            ("A", False),  # Too short (1 char)
            ("AB", True),  # Exactly 2 chars
            ("ABC", True),  # 3 chars
            ("John Doe", True),  # Normal name
            ("X" * 60, True),  # Exactly 60 chars
            ("X" * 61, False),  # Too long (61 chars)
            ("   John   ", True),  # With whitespace (will be stripped)
        ],
    )
    def test_customer_name_validation(self, customer_name, should_pass):
        """Test customer name length validation (2-60 characters if provided)."""
        data = {
            "msisdn": "254712345678",
            "amount_cents": 10000,
            "description": "Test payment",
            "customer_name": customer_name,
        }
        if should_pass:
            invoice = InvoiceCreate(**data)
            assert invoice.customer_name is not None
            assert len(invoice.customer_name.strip()) >= 2
            assert len(invoice.customer_name.strip()) <= 60
        else:
            with pytest.raises(ValidationError):
                InvoiceCreate(**data)

    def test_missing_required_fields(self):
        """Test validation fails when required fields are missing."""
        # Missing msisdn
        with pytest.raises(ValidationError) as exc_info:
            InvoiceCreate(amount_cents=10000, description="Test")
        assert any(
            error["loc"][0] == "msisdn" for error in exc_info.value.errors()
        )

        # Missing amount_cents
        with pytest.raises(ValidationError) as exc_info:
            InvoiceCreate(msisdn="254712345678", description="Test")
        assert any(
            error["loc"][0] == "amount_cents" for error in exc_info.value.errors()
        )

        # Missing description
        with pytest.raises(ValidationError) as exc_info:
            InvoiceCreate(msisdn="254712345678", amount_cents=10000)
        assert any(
            error["loc"][0] == "description" for error in exc_info.value.errors()
        )


# ============================================================================
# InvoiceResponse Tests
# ============================================================================


class TestInvoiceResponse:
    """Test suite for InvoiceResponse schema."""

    def test_invoice_response_from_dict(self):
        """Test creating InvoiceResponse from dictionary."""
        data = {
            "id": str(uuid4()),
            "customer_name": "John Doe",
            "msisdn": "254712345678",
            "amount_cents": 10000,
            "currency": "KES",
            "description": "Test payment",
            "status": "PENDING",
            "pay_ref": None,
            "pay_link": None,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        response = InvoiceResponse(**data)
        assert response.id == data["id"]
        assert response.customer_name == "John Doe"
        assert response.msisdn == "254712345678"
        assert response.amount_cents == 10000
        assert response.currency == "KES"
        assert response.status == "PENDING"

    def test_invoice_response_with_nullable_fields(self):
        """Test InvoiceResponse handles nullable fields correctly."""
        data = {
            "id": str(uuid4()),
            "customer_name": None,
            "msisdn": "254712345678",
            "amount_cents": 10000,
            "currency": "KES",
            "description": "Test payment",
            "status": "PENDING",
            "pay_ref": None,
            "pay_link": None,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        response = InvoiceResponse(**data)
        assert response.customer_name is None
        assert response.pay_ref is None
        assert response.pay_link is None

    def test_invoice_response_with_all_fields_populated(self):
        """Test InvoiceResponse with all optional fields populated."""
        data = {
            "id": str(uuid4()),
            "customer_name": "Jane Smith",
            "msisdn": "254723456789",
            "amount_cents": 50000,
            "currency": "KES",
            "description": "Premium service payment",
            "status": "PAID",
            "pay_ref": "ABC123XYZ",
            "pay_link": "https://pay.example.com/abc123",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        response = InvoiceResponse(**data)
        assert response.customer_name == "Jane Smith"
        assert response.status == "PAID"
        assert response.pay_ref == "ABC123XYZ"
        assert response.pay_link == "https://pay.example.com/abc123"


# ============================================================================
# PaymentCreate Tests
# ============================================================================


class TestPaymentCreate:
    """Test suite for PaymentCreate schema."""

    def test_valid_payment_create(self):
        """Test creating a valid payment request."""
        invoice_id = str(uuid4())
        data = {
            "invoice_id": invoice_id,
            "idempotency_key": "payment-123-retry-1",
        }
        payment = PaymentCreate(**data)
        assert payment.invoice_id == invoice_id
        assert payment.idempotency_key == "payment-123-retry-1"

    @pytest.mark.parametrize(
        "invalid_uuid",
        [
            "not-a-uuid",
            "123456",
            "abc-def-ghi",
            "",
            "123e4567-e89b-12d3-a456",  # Incomplete UUID
            "123e4567-e89b-12d3-a456-426614174000-extra",  # Too long
        ],
    )
    def test_invalid_invoice_id_format(self, invalid_uuid):
        """Test validation fails for invalid UUID formats."""
        data = {
            "invoice_id": invalid_uuid,
            "idempotency_key": "test-key",
        }
        with pytest.raises(ValidationError) as exc_info:
            PaymentCreate(**data)
        errors = exc_info.value.errors()
        assert any("UUID" in str(error) for error in errors)

    def test_valid_uuid_formats(self):
        """Test various valid UUID formats are accepted."""
        valid_uuids = [
            str(uuid4()),
            "123e4567-e89b-12d3-a456-426614174000",
            "550e8400-e29b-41d4-a716-446655440000",
        ]
        for valid_uuid in valid_uuids:
            data = {
                "invoice_id": valid_uuid,
                "idempotency_key": "test-key",
            }
            payment = PaymentCreate(**data)
            assert payment.invoice_id == valid_uuid

    @pytest.mark.parametrize(
        "idempotency_key,should_pass",
        [
            ("", False),  # Empty string
            ("   ", False),  # Whitespace only
            ("a", True),  # Single character
            ("valid-key-123", True),  # Normal key
            ("X" * 255, True),  # Exactly 255 chars
            ("X" * 256, False),  # Too long
        ],
    )
    def test_idempotency_key_validation(self, idempotency_key, should_pass):
        """Test idempotency key validation."""
        data = {
            "invoice_id": str(uuid4()),
            "idempotency_key": idempotency_key,
        }
        if should_pass:
            payment = PaymentCreate(**data)
            assert payment.idempotency_key.strip() != ""
            assert len(payment.idempotency_key.strip()) <= 255
        else:
            with pytest.raises(ValidationError):
                PaymentCreate(**data)

    def test_missing_required_fields(self):
        """Test validation fails when required fields are missing."""
        # Missing invoice_id
        with pytest.raises(ValidationError) as exc_info:
            PaymentCreate(idempotency_key="test-key")
        assert any(
            error["loc"][0] == "invoice_id" for error in exc_info.value.errors()
        )

        # Missing idempotency_key
        with pytest.raises(ValidationError) as exc_info:
            PaymentCreate(invoice_id=str(uuid4()))
        assert any(
            error["loc"][0] == "idempotency_key" for error in exc_info.value.errors()
        )


# ============================================================================
# PaymentResponse Tests
# ============================================================================


class TestPaymentResponse:
    """Test suite for PaymentResponse schema."""

    def test_payment_response_from_dict(self):
        """Test creating PaymentResponse from dictionary."""
        data = {
            "id": str(uuid4()),
            "invoice_id": str(uuid4()),
            "method": "MPESA_STK",
            "status": "SUCCESS",
            "mpesa_receipt": "QGH12345",
            "amount_cents": 10000,
            "idempotency_key": "payment-123",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        response = PaymentResponse(**data)
        assert response.id == data["id"]
        assert response.invoice_id == data["invoice_id"]
        assert response.method == "MPESA_STK"
        assert response.status == "SUCCESS"
        assert response.mpesa_receipt == "QGH12345"
        assert response.amount_cents == 10000

    def test_payment_response_excludes_sensitive_fields(self):
        """Test PaymentResponse excludes raw_request and raw_callback fields."""
        data = {
            "id": str(uuid4()),
            "invoice_id": str(uuid4()),
            "method": "MPESA_STK",
            "status": "SUCCESS",
            "mpesa_receipt": "QGH12345",
            "amount_cents": 10000,
            "idempotency_key": "payment-123",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        response = PaymentResponse(**data)
        # Verify sensitive fields are not in the schema
        assert not hasattr(response, "raw_request")
        assert not hasattr(response, "raw_callback")

    def test_payment_response_with_nullable_fields(self):
        """Test PaymentResponse handles nullable mpesa_receipt."""
        data = {
            "id": str(uuid4()),
            "invoice_id": str(uuid4()),
            "method": "MPESA_STK",
            "status": "INITIATED",
            "mpesa_receipt": None,
            "amount_cents": 10000,
            "idempotency_key": "payment-123",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        response = PaymentResponse(**data)
        assert response.mpesa_receipt is None
        assert response.status == "INITIATED"


# ============================================================================
# WhatsAppWebhookEvent Tests
# ============================================================================


class TestWhatsAppWebhookEvent:
    """Test suite for WhatsAppWebhookEvent and related schemas."""

    def test_parse_text_message(self):
        """Test parsing a WhatsApp text message webhook event."""
        data = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "123456789",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "254700000000",
                                    "phone_number_id": "987654321",
                                },
                                "contacts": [
                                    {
                                        "profile": {"name": "John Doe"},
                                        "wa_id": "254712345678",
                                    }
                                ],
                                "messages": [
                                    {
                                        "from": "254712345678",
                                        "id": "msg_123",
                                        "timestamp": "1234567890",
                                        "type": "text",
                                        "text": {"body": "Hello, I need help"},
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }
        event = WhatsAppWebhookEvent(**data)
        assert event.object == "whatsapp_business_account"
        assert len(event.entry) == 1
        assert len(event.entry[0].changes) == 1

        # Test helper methods
        message = event.get_first_message()
        assert message is not None
        assert message.from_ == "254712345678"
        assert message.type == "text"

        sender = event.get_sender_msisdn()
        assert sender == "254712345678"

        text = event.get_message_text()
        assert text == "Hello, I need help"

        button = event.get_button_reply()
        assert button is None

    def test_parse_button_click(self):
        """Test parsing a WhatsApp button click webhook event."""
        data = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "123456789",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "254700000000",
                                    "phone_number_id": "987654321",
                                },
                                "messages": [
                                    {
                                        "from": "254712345678",
                                        "id": "msg_456",
                                        "timestamp": "1234567890",
                                        "type": "interactive",
                                        "interactive": {
                                            "type": "button_reply",
                                            "button_reply": {
                                                "id": "btn_confirm",
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
        event = WhatsAppWebhookEvent(**data)

        # Test helper methods
        message = event.get_first_message()
        assert message is not None
        assert message.type == "interactive"

        button = event.get_button_reply()
        assert button is not None
        assert button.id == "btn_confirm"
        assert button.title == "Confirm"

        text = event.get_message_text()
        assert text is None

    def test_handle_missing_optional_fields(self):
        """Test handling webhook events with missing optional fields."""
        data = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "123456789",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "254700000000",
                                    "phone_number_id": "987654321",
                                },
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }
        event = WhatsAppWebhookEvent(**data)
        assert event.object == "whatsapp_business_account"

        # Test helper methods return None when no messages
        message = event.get_first_message()
        assert message is None

        sender = event.get_sender_msisdn()
        assert sender is None

        text = event.get_message_text()
        assert text is None

    def test_invalid_webhook_structure(self):
        """Test validation error for invalid webhook structure."""
        # Missing required 'object' field
        with pytest.raises(ValidationError) as exc_info:
            WhatsAppWebhookEvent(entry=[])
        assert any(
            error["loc"][0] == "object" for error in exc_info.value.errors()
        )

        # Missing required 'entry' field
        with pytest.raises(ValidationError) as exc_info:
            WhatsAppWebhookEvent(object="whatsapp_business_account")
        assert any(
            error["loc"][0] == "entry" for error in exc_info.value.errors()
        )

    def test_extract_sender_msisdn_correctly(self):
        """Test extracting sender MSISDN from various webhook formats."""
        data = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "123456789",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "254700000000",
                                    "phone_number_id": "987654321",
                                },
                                "messages": [
                                    {
                                        "from": "254723456789",
                                        "id": "msg_789",
                                        "timestamp": "1234567890",
                                        "type": "text",
                                        "text": {"body": "Test message"},
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }
        event = WhatsAppWebhookEvent(**data)
        sender = event.get_sender_msisdn()
        assert sender == "254723456789"

    def test_empty_entry_list(self):
        """Test handling webhook with empty entry list."""
        data = {
            "object": "whatsapp_business_account",
            "entry": [],
        }
        event = WhatsAppWebhookEvent(**data)
        assert event.get_first_message() is None
        assert event.get_sender_msisdn() is None
        assert event.get_message_text() is None
        assert event.get_button_reply() is None


# ============================================================================
# Integration Tests
# ============================================================================


class TestSchemaIntegration:
    """Integration tests for schema interactions."""

    def test_invoice_create_to_response_flow(self):
        """Test data flow from InvoiceCreate to InvoiceResponse."""
        # Create invoice request
        create_data = {
            "msisdn": "254712345678",
            "customer_name": "John Doe",
            "amount_cents": 10000,
            "description": "Test payment",
        }
        invoice_create = InvoiceCreate(**create_data)

        # Simulate response (would come from ORM)
        response_data = {
            "id": str(uuid4()),
            "customer_name": invoice_create.customer_name,
            "msisdn": invoice_create.msisdn,
            "amount_cents": invoice_create.amount_cents,
            "currency": "KES",
            "description": invoice_create.description,
            "status": "PENDING",
            "pay_ref": None,
            "pay_link": None,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        invoice_response = InvoiceResponse(**response_data)

        assert invoice_response.msisdn == invoice_create.msisdn
        assert invoice_response.amount_cents == invoice_create.amount_cents
        assert invoice_response.description == invoice_create.description

    def test_payment_create_validation_with_real_uuid(self):
        """Test payment creation with actual UUID from invoice."""
        invoice_id = str(uuid4())
        payment_data = {
            "invoice_id": invoice_id,
            "idempotency_key": f"invoice-{invoice_id}-payment-1",
        }
        payment_create = PaymentCreate(**payment_data)
        assert payment_create.invoice_id == invoice_id
        assert invoice_id in payment_create.idempotency_key