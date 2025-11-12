"""
SQLAlchemy ORM models for InvoiceIQ.

Defines the database schema using SQLAlchemy 2.0 declarative mapping with
Mapped types and mapped_column. Models include Invoice, Payment, and MessageLog.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    JSON,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Invoice(Base):
    """
    Invoice model representing customer invoices.

    Tracks invoice lifecycle from creation through payment. Status progresses
    through: PENDING → SENT → PAID/CANCELLED/FAILED
    """

    __tablename__ = "invoices"

    # Primary key
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # Customer information
    customer_name: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    msisdn: Mapped[str] = mapped_column(
        String(12), nullable=False
    )  # E.164 format: 2547XXXXXXXX

    # Merchant information
    merchant_msisdn: Mapped[str] = mapped_column(
        String(12), nullable=False
    )  # Merchant's phone number (E.164 format)

    # Invoice details
    amount_cents: Mapped[int] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="KES")
    description: Mapped[str] = mapped_column(String(120), nullable=False)

    # Status and payment info
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    pay_ref: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    pay_link: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    payments: Mapped[List["Payment"]] = relationship(
        "Payment", back_populates="invoice", cascade="all, delete-orphan"
    )
    messages: Mapped[List["MessageLog"]] = relationship(
        "MessageLog", back_populates="invoice", cascade="all, delete-orphan"
    )

    # Constraints
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'SENT', 'PAID', 'CANCELLED', 'FAILED')",
            name="ck_invoice_status",
        ),
        CheckConstraint(
            "amount_cents >= 100", name="ck_invoice_amount_min"
        ),  # Min 1 KES
        CheckConstraint(
            "LENGTH(msisdn) = 12", name="ck_invoice_msisdn_length"
        ),  # Exactly 12 chars
        CheckConstraint(
            "LENGTH(description) >= 3 AND LENGTH(description) <= 120",
            name="ck_invoice_description_length",
        ),
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"Invoice(id={self.id!r}, msisdn={self.msisdn!r}, "
            f"amount_cents={self.amount_cents}, status={self.status!r})"
        )


class Payment(Base):
    """
    Payment model representing M-PESA STK Push transactions.

    Tracks payment lifecycle from initiation through completion/failure.
    Includes idempotency key to prevent duplicate charges.
    """

    __tablename__ = "payments"

    # Primary key
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # Foreign key to invoice
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )

    # Payment details
    method: Mapped[str] = mapped_column(
        String(20), nullable=False, default="MPESA_STK"
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="INITIATED")
    mpesa_receipt: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    amount_cents: Mapped[int] = mapped_column(nullable=False)

    # Request/callback payloads (stored as JSON)
    raw_request: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    raw_callback: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Idempotency
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)

    # M-PESA identifiers for callback matching
    checkout_request_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    merchant_request_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="payments")

    # Constraints
    __table_args__ = (
        CheckConstraint(
            "method IN ('MPESA_STK')", name="ck_payment_method"
        ),  # Only M-PESA for now
        CheckConstraint(
            "status IN ('INITIATED', 'SUCCESS', 'FAILED', 'EXPIRED')",
            name="ck_payment_status",
        ),
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"Payment(id={self.id!r}, invoice_id={self.invoice_id!r}, "
            f"status={self.status!r}, amount_cents={self.amount_cents})"
        )


class MessageLog(Base):
    """
    Message log model for audit trail of WhatsApp/SMS communications.

    Records all inbound and outbound messages for debugging and compliance.
    Links to invoice when message is related to a specific invoice.
    """

    __tablename__ = "message_log"

    # Primary key
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # Foreign key to invoice (nullable - not all messages are invoice-related)
    invoice_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True
    )

    # Message metadata
    channel: Mapped[str] = mapped_column(String(20), nullable=False)  # WHATSAPP or SMS
    direction: Mapped[str] = mapped_column(
        String(3), nullable=False
    )  # IN (inbound) or OUT (outbound)
    event: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # SENT, DELIVERED, FAILED, etc.

    # Message payload (stored as JSON)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, default=datetime.utcnow
    )

    # Relationships
    invoice: Mapped[Optional["Invoice"]] = relationship(
        "Invoice", back_populates="messages"
    )

    # Constraints
    __table_args__ = (
        CheckConstraint(
            "channel IN ('WHATSAPP', 'SMS')", name="ck_message_log_channel"
        ),
        CheckConstraint("direction IN ('IN', 'OUT')", name="ck_message_log_direction"),
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"MessageLog(id={self.id!r}, channel={self.channel!r}, "
            f"direction={self.direction!r}, event={self.event!r})"
        )