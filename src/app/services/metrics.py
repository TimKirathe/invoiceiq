"""
Metrics service for InvoiceIQ.

This module provides database query functions to calculate business metrics
and statistics for monitoring system health and performance.
"""

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Invoice, Payment
from ..utils.logging import get_logger

# Set up logger
logger = get_logger(__name__)


async def get_invoice_stats(db: AsyncSession) -> dict[str, int]:
    """
    Get invoice statistics by status.

    Queries the Invoice table to count invoices grouped by their status.
    Returns counts for all possible statuses (PENDING, SENT, PAID, FAILED,
    CANCELLED) as well as a total count.

    Args:
        db: Database session

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
    logger.debug("Calculating invoice statistics")

    try:
        # Query total count
        total_stmt = select(func.count(Invoice.id))
        total_result = await db.execute(total_stmt)
        total = total_result.scalar() or 0

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
            stmt = select(func.count(Invoice.id)).where(Invoice.status == status)
            result = await db.execute(stmt)
            count = result.scalar() or 0
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


async def get_conversion_rate(db: AsyncSession) -> float:
    """
    Calculate the conversion rate (paid invoices / sent invoices).

    This metric shows what percentage of sent invoices result in successful
    payments. Returns 0.0 if no invoices have been sent yet.

    Args:
        db: Database session

    Returns:
        Conversion rate as a percentage (0.0 to 100.0)
    """
    logger.debug("Calculating conversion rate")

    try:
        # Count sent invoices (includes SENT, PAID, FAILED statuses)
        sent_stmt = select(func.count(Invoice.id)).where(
            Invoice.status.in_(["SENT", "PAID", "FAILED"])
        )
        sent_result = await db.execute(sent_stmt)
        sent_count = sent_result.scalar() or 0

        # Count paid invoices
        paid_stmt = select(func.count(Invoice.id)).where(Invoice.status == "PAID")
        paid_result = await db.execute(paid_stmt)
        paid_count = paid_result.scalar() or 0

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


async def get_average_payment_time(db: AsyncSession) -> Optional[float]:
    """
    Calculate the average time (in seconds) from invoice SENT to PAID.

    This metric shows how long it takes on average for customers to complete
    payment after receiving an invoice. Returns None if there are no paid
    invoices yet.

    The calculation uses the difference between invoice.updated_at (when status
    changed to PAID) and the payment.created_at (when payment was initiated).

    Args:
        db: Database session

    Returns:
        Average payment time in seconds, or None if no paid invoices exist
    """
    logger.debug("Calculating average payment time")

    try:
        # Query paid invoices with their successful payments
        # We calculate time from when payment was initiated to when it succeeded
        stmt = (
            select(Payment.created_at, Payment.updated_at)
            .select_from(Invoice)
            .join(Payment, Invoice.id == Payment.invoice_id)
            .where(Invoice.status == "PAID")
            .where(Payment.status == "SUCCESS")
        )

        result = await db.execute(stmt)
        payments = result.all()

        if not payments:
            logger.info(
                "No paid invoices found for average payment time calculation"
            )
            return None

        # Calculate the average time difference in seconds
        total_seconds = 0.0
        count = 0

        for created_at, updated_at in payments:
            if created_at and updated_at:
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