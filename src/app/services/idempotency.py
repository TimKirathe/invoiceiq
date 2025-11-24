"""
Idempotency service for InvoiceIQ.

Provides helper functions for generating and validating idempotency keys
to prevent duplicate payment processing.
"""

import uuid
from typing import Optional

from supabase import Client

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
    key: str, supabase: Client
) -> Optional[dict]:
    """
    Check if an idempotency key already exists in the database.

    Args:
        key: The idempotency key to validate
        supabase: Supabase client for querying

    Returns:
        Existing Payment record (as dict) if key exists, None otherwise
    """
    response = supabase.table("payments").select("*").eq("idempotency_key", key).execute()
    existing_payment = response.data[0] if response.data else None

    if existing_payment:
        logger.info(
            "Idempotency key already exists",
            extra={
                "key": key,
                "payment_id": existing_payment["id"],
                "status": existing_payment["status"],
            },
        )
    else:
        logger.debug("Idempotency key is unique", extra={"key": key})

    return existing_payment


async def check_callback_processed(
    checkout_request_id: str, supabase: Client
) -> Optional[dict]:
    """
    Check if a callback for a CheckoutRequestID has already been processed.

    This is the primary idempotency check for M-PESA callbacks, using the
    CheckoutRequestID as a natural idempotency key.

    Args:
        checkout_request_id: The M-PESA CheckoutRequestID from callback
        supabase: Supabase client for querying

    Returns:
        Existing Payment record (as dict) if callback already processed, None otherwise
    """
    response = (
        supabase.table("payments")
        .select("*")
        .eq("checkout_request_id", checkout_request_id)
        .execute()
    )
    existing_payment = response.data[0] if response.data else None

    if existing_payment:
        # Check if payment is already in a final state (not INITIATED)
        if existing_payment["status"] != "INITIATED":
            logger.warning(
                "Duplicate callback detected - payment already processed",
                extra={
                    "checkout_request_id": checkout_request_id,
                    "payment_id": existing_payment["id"],
                    "current_status": existing_payment["status"],
                },
            )
            return existing_payment
        else:
            logger.debug(
                "Payment exists but not yet processed",
                extra={
                    "checkout_request_id": checkout_request_id,
                    "payment_id": existing_payment["id"],
                },
            )
    else:
        logger.warning(
            "No payment found for CheckoutRequestID",
            extra={"checkout_request_id": checkout_request_id},
        )

    return None if not existing_payment or existing_payment["status"] == "INITIATED" else existing_payment