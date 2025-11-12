"""
Idempotency service for InvoiceIQ.

Provides helper functions for generating and validating idempotency keys
to prevent duplicate payment processing.
"""

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Payment
from ..utils.logging import get_logger

logger = get_logger(__name__)


def generate_idempotency_key() -> str:
    """
    Generate a UUID-based idempotency key.

    Returns:
        A unique UUID string suitable for use as an idempotency key
    """
    key = str(uuid.uuid4())
    logger.debug("Generated idempotency key", extra={"key": key})
    return key


async def validate_idempotency_key(
    key: str, db_session: AsyncSession
) -> Optional[Payment]:
    """
    Check if an idempotency key already exists in the database.

    Args:
        key: The idempotency key to validate
        db_session: Database session for querying

    Returns:
        Existing Payment record if key exists, None otherwise
    """
    stmt = select(Payment).where(Payment.idempotency_key == key)
    result = await db_session.execute(stmt)
    existing_payment = result.scalar_one_or_none()

    if existing_payment:
        logger.info(
            "Idempotency key already exists",
            extra={
                "key": key,
                "payment_id": existing_payment.id,
                "status": existing_payment.status,
            },
        )
    else:
        logger.debug("Idempotency key is unique", extra={"key": key})

    return existing_payment


async def check_callback_processed(
    checkout_request_id: str, db_session: AsyncSession
) -> Optional[Payment]:
    """
    Check if a callback for a CheckoutRequestID has already been processed.

    This is the primary idempotency check for M-PESA callbacks, using the
    CheckoutRequestID as a natural idempotency key.

    Args:
        checkout_request_id: The M-PESA CheckoutRequestID from callback
        db_session: Database session for querying

    Returns:
        Existing Payment record if callback already processed, None otherwise
    """
    stmt = select(Payment).where(Payment.checkout_request_id == checkout_request_id)
    result = await db_session.execute(stmt)
    existing_payment = result.scalar_one_or_none()

    if existing_payment:
        # Check if payment is already in a final state (not INITIATED)
        if existing_payment.status != "INITIATED":
            logger.warning(
                "Duplicate callback detected - payment already processed",
                extra={
                    "checkout_request_id": checkout_request_id,
                    "payment_id": existing_payment.id,
                    "current_status": existing_payment.status,
                },
            )
            return existing_payment
        else:
            logger.debug(
                "Payment exists but not yet processed",
                extra={
                    "checkout_request_id": checkout_request_id,
                    "payment_id": existing_payment.id,
                },
            )
    else:
        logger.warning(
            "No payment found for CheckoutRequestID",
            extra={"checkout_request_id": checkout_request_id},
        )

    return None if not existing_payment or existing_payment.status == "INITIATED" else existing_payment