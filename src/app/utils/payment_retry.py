"""
Payment retry utilities for InvoiceIQ.

This module provides helper functions for managing payment retry logic,
including retry eligibility checks, rate limiting, and retry count validation.
"""

from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from ..utils.logging import get_logger

logger = get_logger(__name__)

# Constants
MAX_RETRY_COUNT = 1  # Total of 2 attempts (1 original + 1 retry)
RETRY_RATE_LIMIT_SECONDS = 90  # 90 seconds between retry attempts


def get_payment_by_invoice_id(invoice_id: str, supabase) -> Optional[Dict]:
    """
    Get the most recent payment record for an invoice.

    Args:
        invoice_id: Invoice ID to lookup
        supabase: Supabase client instance

    Returns:
        Payment record dict if found, None otherwise
    """
    try:
        response = (
            supabase.table("payments")
            .select("*")
            .eq("invoice_id", invoice_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if response.data:
            return response.data[0]
        return None

    except Exception as e:
        logger.error(
            "Failed to get payment by invoice_id",
            extra={"error": str(e), "invoice_id": invoice_id},
            exc_info=True,
        )
        return None


def can_retry_payment(payment: Dict) -> Tuple[bool, Optional[str]]:
    """
    Check if a payment can be retried based on retry_count and rate limiting.

    Args:
        payment: Payment record dict with 'retry_count' and 'updated_at' fields

    Returns:
        Tuple of (can_retry: bool, error_message: Optional[str])
        - If can_retry is True, error_message is None
        - If can_retry is False, error_message contains the reason
    """
    # Check retry count limit
    retry_count = payment.get("retry_count", 0)
    if retry_count >= MAX_RETRY_COUNT:
        logger.warning(
            "Payment retry blocked: max retry count reached",
            extra={
                "payment_id": payment.get("id"),
                "retry_count": retry_count,
                "max_retry_count": MAX_RETRY_COUNT,
            },
        )
        return False, "Maximum payment attempts reached. Please contact support."

    # Check rate limit (time since last callback)
    try:
        updated_at_str = payment.get("updated_at")
        if not updated_at_str:
            logger.warning(
                "Payment has no updated_at timestamp",
                extra={"payment_id": payment.get("id")},
            )
            # Allow retry if no timestamp exists
            return True, None

        # Parse updated_at timestamp (handle both with and without timezone)
        if updated_at_str.endswith("Z"):
            updated_at_str = updated_at_str[:-1] + "+00:00"

        updated_at = datetime.fromisoformat(updated_at_str)

        # Ensure timezone-aware comparison
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        time_since_failure = (now - updated_at).total_seconds()

        if time_since_failure < RETRY_RATE_LIMIT_SECONDS:
            seconds_remaining = int(RETRY_RATE_LIMIT_SECONDS - time_since_failure)
            logger.info(
                "Payment retry blocked: rate limit not met",
                extra={
                    "payment_id": payment.get("id"),
                    "time_since_failure": time_since_failure,
                    "seconds_remaining": seconds_remaining,
                },
            )
            return (
                False,
                f"Please wait {seconds_remaining} seconds before retrying payment.",
            )

        logger.info(
            "Payment retry allowed",
            extra={
                "payment_id": payment.get("id"),
                "retry_count": retry_count,
                "time_since_failure": time_since_failure,
            },
        )
        return True, None

    except (ValueError, TypeError) as e:
        logger.error(
            "Failed to parse payment timestamp for retry check",
            extra={
                "error": str(e),
                "payment_id": payment.get("id"),
                "updated_at": payment.get("updated_at"),
            },
            exc_info=True,
        )
        # Allow retry if timestamp parsing fails
        return True, None


def increment_retry_count(payment_id: str, supabase) -> bool:
    """
    Increment the retry_count for a payment record.

    Args:
        payment_id: Payment ID to update
        supabase: Supabase client instance

    Returns:
        True if successful, False otherwise
    """
    try:
        supabase.table("payments").update(
            {"retry_count": supabase.rpc("increment", {"x": 1, "row_id": payment_id})}
        ).eq("id", payment_id).execute()

        logger.info(
            "Incremented payment retry_count",
            extra={"payment_id": payment_id},
        )
        return True

    except Exception as e:
        logger.error(
            "Failed to increment payment retry_count",
            extra={"error": str(e), "payment_id": payment_id},
            exc_info=True,
        )
        return False


def reset_invoice_to_pending(invoice_id: str, supabase) -> bool:
    """
    Reset invoice status from FAILED to PENDING to allow retry.

    Args:
        invoice_id: Invoice ID to update
        supabase: Supabase client instance

    Returns:
        True if successful, False otherwise
    """
    try:
        supabase.table("invoices").update({"status": "PENDING"}).eq(
            "id", invoice_id
        ).execute()

        logger.info(
            "Reset invoice status to PENDING for retry",
            extra={"invoice_id": invoice_id},
        )
        return True

    except Exception as e:
        logger.error(
            "Failed to reset invoice status",
            extra={"error": str(e), "invoice_id": invoice_id},
            exc_info=True,
        )
        return False
