"""
Pydantic schemas for API request/response validation.

Defines Pydantic v2 models for invoice creation, payment initiation, and
WhatsApp webhook events. All schemas use modern Pydantic v2 syntax with
@field_validator and ConfigDict.
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .utils.phone import validate_msisdn


class InvoiceCreate(BaseModel):
    """
    Request schema for creating a new invoice.

    Validates customer information, amount, and description according to
    business rules defined in the Invoice model.
    """

    msisdn: str = Field(
        ...,
        description="Customer phone number in E.164 format without + (2547XXXXXXXX)",
        examples=["254712345678"],
    )
    customer_name: Optional[str] = Field(
        None,
        description="Optional customer name (2-60 characters)",
        min_length=2,
        max_length=60,
        examples=["John Doe"],
    )
    merchant_msisdn: str = Field(
        ...,
        description="Merchant phone number in E.164 format without + (2547XXXXXXXX)",
        examples=["254712345678"],
    )
    amount_cents: int = Field(
        ...,
        description="Invoice amount in cents (minimum 100 = 1 KES)",
        ge=100,
        examples=[10000],
    )
    description: str = Field(
        ...,
        description="Invoice description (3-120 characters)",
        min_length=3,
        max_length=120,
        examples=["Payment for services rendered"],
    )

    @field_validator("msisdn", "merchant_msisdn")
    @classmethod
    def validate_msisdn_format(cls, v: str) -> str:
        """Validate MSISDN using phone.py utility."""
        return validate_msisdn(v)

    @field_validator("customer_name")
    @classmethod
    def validate_customer_name_length(cls, v: Optional[str]) -> Optional[str]:
        """Validate customer name length if provided."""
        if v is not None:
            v = v.strip()
            if len(v) < 2 or len(v) > 60:
                raise ValueError("Customer name must be between 2 and 60 characters")
        return v

    @field_validator("amount_cents")
    @classmethod
    def validate_amount(cls, v: int) -> int:
        """Validate amount is at least 100 cents (1 KES)."""
        if v < 100:
            raise ValueError("Amount must be at least 100 cents (1 KES)")
        return v

    @field_validator("description")
    @classmethod
    def validate_description_length(cls, v: str) -> str:
        """Validate description length."""
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Description must be at least 3 characters")
        if len(v) > 120:
            raise ValueError("Description must not exceed 120 characters")
        return v


class InvoiceResponse(BaseModel):
    """
    Response schema for invoice data.

    Returns all invoice fields from the Invoice model, configured for
    ORM compatibility using from_attributes.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Invoice UUID")
    customer_name: Optional[str] = Field(None, description="Customer name")
    msisdn: str = Field(..., description="Customer phone number")
    amount_cents: int = Field(..., description="Invoice amount in cents")
    currency: str = Field(..., description="Currency code (KES)")
    description: str = Field(..., description="Invoice description")
    status: str = Field(..., description="Invoice status")
    pay_ref: Optional[str] = Field(None, description="Payment reference")
    pay_link: Optional[str] = Field(None, description="Payment link")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class PaymentCreate(BaseModel):
    """
    Request schema for initiating a payment (M-PESA STK Push).

    Requires invoice ID and idempotency key to prevent duplicate charges.
    """

    invoice_id: str = Field(
        ...,
        description="Invoice UUID to pay",
        examples=["123e4567-e89b-12d3-a456-426614174000"],
    )
    idempotency_key: str = Field(
        ...,
        description="Unique key to prevent duplicate charges",
        min_length=1,
        max_length=255,
        examples=["invoice-123-payment-1"],
    )

    @field_validator("invoice_id")
    @classmethod
    def validate_invoice_id_format(cls, v: str) -> str:
        """Validate invoice_id is a valid UUID format."""
        try:
            UUID(v)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format for invoice_id: {e}")
        return v

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, v: str) -> str:
        """Validate idempotency_key is not empty and within length limit."""
        v = v.strip()
        if not v:
            raise ValueError("Idempotency key cannot be empty")
        if len(v) > 255:
            raise ValueError("Idempotency key must not exceed 255 characters")
        return v


class PaymentResponse(BaseModel):
    """
    Response schema for payment data.

    Returns payment fields from the Payment model, excluding sensitive
    raw request/callback data. Configured for ORM compatibility.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Payment UUID")
    invoice_id: str = Field(..., description="Related invoice UUID")
    method: str = Field(..., description="Payment method (MPESA_STK)")
    status: str = Field(..., description="Payment status")
    mpesa_receipt: Optional[str] = Field(None, description="M-PESA receipt number")
    amount_cents: int = Field(..., description="Payment amount in cents")
    idempotency_key: str = Field(..., description="Idempotency key")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


# WhatsApp Webhook Event Schemas
# Based on WhatsApp Cloud API webhook structure


class ButtonReply(BaseModel):
    """WhatsApp interactive button reply."""

    id: str = Field(..., description="Button ID")
    title: str = Field(..., description="Button title")


class InteractiveMessage(BaseModel):
    """WhatsApp interactive message (button clicks)."""

    type: str = Field(..., description="Interactive type")
    button_reply: Optional[ButtonReply] = Field(None, description="Button reply data")


class TextMessage(BaseModel):
    """WhatsApp text message."""

    body: str = Field(..., description="Message text content")


class Message(BaseModel):
    """WhatsApp message from webhook."""

    from_: str = Field(..., alias="from", description="Sender phone number (MSISDN)")
    id: str = Field(..., description="Message ID")
    timestamp: str = Field(..., description="Message timestamp")
    type: str = Field(..., description="Message type (text, interactive, etc.)")
    text: Optional[TextMessage] = Field(None, description="Text message data")
    interactive: Optional[InteractiveMessage] = Field(
        None, description="Interactive message data"
    )


class Contact(BaseModel):
    """WhatsApp contact information."""

    profile: dict = Field(..., description="Contact profile")
    wa_id: str = Field(..., description="WhatsApp ID (phone number)")


class Metadata(BaseModel):
    """WhatsApp metadata."""

    display_phone_number: str = Field(..., description="Display phone number")
    phone_number_id: str = Field(..., description="Phone number ID")


class Value(BaseModel):
    """WhatsApp webhook value object."""

    messaging_product: str = Field(..., description="Messaging product (whatsapp)")
    metadata: Metadata = Field(..., description="Metadata")
    contacts: Optional[List[Contact]] = Field(None, description="Contact list")
    messages: Optional[List[Message]] = Field(None, description="Message list")


class Change(BaseModel):
    """WhatsApp webhook change object."""

    value: Value = Field(..., description="Change value")
    field: str = Field(..., description="Changed field")


class Entry(BaseModel):
    """WhatsApp webhook entry object."""

    id: str = Field(..., description="Entry ID")
    changes: List[Change] = Field(..., description="List of changes")


class WhatsAppWebhookEvent(BaseModel):
    """
    Schema for parsing WhatsApp Cloud API webhook events.

    Represents the complete webhook payload structure for incoming
    WhatsApp messages and status updates.
    """

    object: str = Field(..., description="Webhook object type (whatsapp_business_account)")
    entry: List[Entry] = Field(..., description="List of entries")

    def get_first_message(self) -> Optional[Message]:
        """
        Extract the first message from the webhook event.

        Returns:
            The first Message object if present, None otherwise.
        """
        if not self.entry:
            return None
        for entry in self.entry:
            for change in entry.changes:
                if change.value.messages:
                    return change.value.messages[0]
        return None

    def get_sender_msisdn(self) -> Optional[str]:
        """
        Extract the sender's phone number from the first message.

        Returns:
            The sender's MSISDN (phone number) if present, None otherwise.
        """
        message = self.get_first_message()
        return message.from_ if message else None

    def get_message_text(self) -> Optional[str]:
        """
        Extract the message text from the first message.

        Returns:
            The message text if present, None otherwise.
        """
        message = self.get_first_message()
        if message and message.text:
            return message.text.body
        return None

    def get_button_reply(self) -> Optional[ButtonReply]:
        """
        Extract the button reply from the first message.

        Returns:
            The ButtonReply object if present, None otherwise.
        """
        message = self.get_first_message()
        if message and message.interactive and message.interactive.button_reply:
            return message.interactive.button_reply
        return None