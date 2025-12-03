"""
Metrics service for InvoiceIQ.

This module provides database query functions to calculate business metrics
and statistics for monitoring system health and performance.
"""

from typing import Optional

from supabase import Client

from ..utils.logging import get_logger

# Set up logger
logger = get_logger(__name__)


async def get_invoice_stats(supabase: Client) -> dict[str, int]:
    """
    Get invoice statistics by status.

    Queries the Invoice table to count invoices grouped by their status.
    Returns counts for all possible statuses (PENDING, SENT, PAID, FAILED,
    CANCELLED) as well as a total count.

    Args:
        supabase: Supabase client

    Returns:
        Dictionary with status counts:
        {
            "total": int,
            "pending": int,
            "sent": int,
            "paid": int,
            "failed": int,
            "cancelled": int
        }
    """
    try:
        # Query total count
        total_response = supabase.table("invoices").select("id", count="exact").execute()
        total = total_response.count or 0

        # Query counts by status
        stats = {
            "total": total,
            "pending": 0,
            "sent": 0,
            "paid": 0,
            "failed": 0,
            "cancelled": 0,
        }

        # Query each status count
        for status in ["PENDING", "SENT", "PAID", "FAILED", "CANCELLED"]:
            response = (
                supabase.table("invoices")
                .select("id", count="exact")
                .eq("status", status)
                .execute()
            )
            count = response.count or 0
            stats[status.lower()] = count

        logger.info(
            "Invoice statistics calculated",
            extra={
                "total": stats["total"],
                "paid": stats["paid"],
                "sent": stats["sent"],
            },
        )

        return stats

    except Exception as e:
        logger.error(
            "Failed to calculate invoice statistics",
            extra={"error": str(e)},
            exc_info=True,
        )
        raise


async def get_conversion_rate(supabase: Client) -> float:
    """
    Calculate the conversion rate (paid invoices / sent invoices).

    This metric shows what percentage of sent invoices result in successful
    payments. Returns 0.0 if no invoices have been sent yet.

    Args:
        supabase: Supabase client

    Returns:
        Conversion rate as a percentage (0.0 to 100.0)
    """
    try:
        # Count sent invoices (includes SENT, PAID, FAILED statuses)
        sent_response = (
            supabase.table("invoices")
            .select("id", count="exact")
            .in_("status", ["SENT", "PAID", "FAILED"])
            .execute()
        )
        sent_count = sent_response.count or 0

        # Count paid invoices
        paid_response = (
            supabase.table("invoices")
            .select("id", count="exact")
            .eq("status", "PAID")
            .execute()
        )
        paid_count = paid_response.count or 0

        # Calculate conversion rate
        if sent_count == 0:
            conversion_rate = 0.0
        else:
            conversion_rate = (paid_count / sent_count) * 100.0

        logger.info(
            "Conversion rate calculated",
            extra={
                "sent_count": sent_count,
                "paid_count": paid_count,
                "conversion_rate": f"{conversion_rate:.2f}%",
            },
        )

        return conversion_rate

    except Exception as e:
        logger.error(
            "Failed to calculate conversion rate",
            extra={"error": str(e)},
            exc_info=True,
        )
        raise


async def get_average_payment_time(supabase: Client) -> Optional[float]:
    """
    Calculate the average time (in seconds) from invoice SENT to PAID.

    This metric shows how long it takes on average for customers to complete
    payment after receiving an invoice. Returns None if there are no paid
    invoices yet.

    The calculation uses the difference between invoice.updated_at (when status
    changed to PAID) and the payment.created_at (when payment was initiated).

    Args:
        supabase: Supabase client

    Returns:
        Average payment time in seconds, or None if no paid invoices exist
    """
    try:
        # Query paid invoices with their successful payments
        # We join invoices and payments where status is PAID and payment status is SUCCESS
        response = (
            supabase.table("payments")
            .select("created_at, updated_at, invoices!inner(status)")
            .eq("invoices.status", "PAID")
            .eq("status", "SUCCESS")
            .execute()
        )

        payments = response.data

        if not payments:
            logger.info(
                "No paid invoices found for average payment time calculation"
            )
            return None

        # Calculate the average time difference in seconds
        from datetime import datetime

        total_seconds = 0.0
        count = 0

        for payment in payments:
            created_at_str = payment.get("created_at")
            updated_at_str = payment.get("updated_at")

            if created_at_str and updated_at_str:
                # Parse ISO format timestamps
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))

                time_diff = (updated_at - created_at).total_seconds()
                total_seconds += time_diff
                count += 1

        if count == 0:
            return None

        avg_time_seconds = total_seconds / count

        logger.info(
            "Average payment time calculated",
            extra={
                "average_payment_time_seconds": f"{avg_time_seconds:.2f}",
                "average_payment_time_minutes": f"{avg_time_seconds / 60:.2f}",
            },
        )

        return avg_time_seconds

    except Exception as e:
        logger.error(
            "Failed to calculate average payment time",
            extra={"error": str(e)},
            exc_info=True,
        )
        raise