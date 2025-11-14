"""
Analytics utilities for querying message logs and generating metrics.

This module provides helper functions to analyze message_log table data
for operational insights while maintaining privacy-first principles.
"""

from datetime import datetime, timedelta
from typing import Dict, Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import MessageLog
from ..utils.logging import get_logger

logger = get_logger(__name__)


async def get_delivery_rates(db: AsyncSession, days: int = 7) -> Dict[str, Any]:
    """
    Calculate message delivery rates by channel.

    Args:
        db: Database session
        days: Number of days to look back (default: 7)

    Returns:
        Dictionary with delivery rates:
        {
            "period_days": 7,
            "channels": {
                "WHATSAPP": {"sent": 100, "failed": 5, "rate": 0.95},
                "SMS": {"sent": 20, "failed": 1, "rate": 0.95}
            },
            "overall": {"sent": 120, "failed": 6, "rate": 0.95}
        }
    """
    start_date = datetime.utcnow() - timedelta(days=days)

    try:
        # Get sent messages by channel
        sent_query = (
            select(
                MessageLog.channel,
                func.count(MessageLog.id).label("count")
            )
            .where(
                MessageLog.created_at >= start_date,
                MessageLog.direction == "OUT",
                MessageLog.event.in_(["invoice_sent", "receipt_sent_customer", "receipt_sent_merchant"])
            )
            .group_by(MessageLog.channel)
        )
        sent_result = await db.execute(sent_query)
        sent_by_channel = {row.channel: row.count for row in sent_result}

        # Get failed messages by channel
        failed_query = (
            select(
                MessageLog.channel,
                func.count(MessageLog.id).label("count")
            )
            .where(
                MessageLog.created_at >= start_date,
                MessageLog.direction == "OUT",
                MessageLog.event.in_(["invoice_send_failed", "receipt_send_failed_customer", "receipt_send_failed_merchant"])
            )
            .group_by(MessageLog.channel)
        )
        failed_result = await db.execute(failed_query)
        failed_by_channel = {row.channel: row.count for row in failed_result}

        # Calculate rates by channel
        channels = {}
        total_sent = 0
        total_failed = 0

        for channel in set(list(sent_by_channel.keys()) + list(failed_by_channel.keys())):
            sent = sent_by_channel.get(channel, 0)
            failed = failed_by_channel.get(channel, 0)
            total = sent + failed
            rate = sent / total if total > 0 else 0.0

            channels[channel] = {
                "sent": sent,
                "failed": failed,
                "total": total,
                "rate": round(rate, 4)
            }

            total_sent += sent
            total_failed += failed

        # Calculate overall rate
        overall_total = total_sent + total_failed
        overall_rate = total_sent / overall_total if overall_total > 0 else 0.0

        result = {
            "period_days": days,
            "start_date": start_date.isoformat(),
            "channels": channels,
            "overall": {
                "sent": total_sent,
                "failed": total_failed,
                "total": overall_total,
                "rate": round(overall_rate, 4)
            }
        }

        logger.info(
            "Delivery rates calculated",
            extra={
                "days": days,
                "overall_rate": result["overall"]["rate"],
                "total_messages": result["overall"]["total"]
            }
        )

        return result

    except Exception as e:
        logger.error(
            "Failed to calculate delivery rates",
            extra={"error": str(e), "days": days},
            exc_info=True
        )
        raise


async def get_channel_distribution(db: AsyncSession, days: int = 7) -> Dict[str, Any]:
    """
    Get distribution of messages by channel (WhatsApp vs SMS).

    Args:
        db: Database session
        days: Number of days to look back (default: 7)

    Returns:
        Dictionary with channel distribution:
        {
            "period_days": 7,
            "distribution": {
                "WHATSAPP": {"count": 100, "percentage": 0.83},
                "SMS": {"count": 20, "percentage": 0.17}
            },
            "total": 120
        }
    """
    start_date = datetime.utcnow() - timedelta(days=days)

    try:
        # Get message count by channel
        query = (
            select(
                MessageLog.channel,
                func.count(MessageLog.id).label("count")
            )
            .where(
                MessageLog.created_at >= start_date,
                MessageLog.direction == "OUT"
            )
            .group_by(MessageLog.channel)
        )
        result = await db.execute(query)
        channel_counts = {row.channel: row.count for row in result}

        # Calculate percentages
        total = sum(channel_counts.values())
        distribution = {}

        for channel, count in channel_counts.items():
            percentage = count / total if total > 0 else 0.0
            distribution[channel] = {
                "count": count,
                "percentage": round(percentage, 4)
            }

        result_data = {
            "period_days": days,
            "start_date": start_date.isoformat(),
            "distribution": distribution,
            "total": total
        }

        logger.info(
            "Channel distribution calculated",
            extra={
                "days": days,
                "total_messages": total,
                "channels": list(distribution.keys())
            }
        )

        return result_data

    except Exception as e:
        logger.error(
            "Failed to calculate channel distribution",
            extra={"error": str(e), "days": days},
            exc_info=True
        )
        raise


async def get_performance_metrics(db: AsyncSession, days: int = 7) -> Dict[str, Any]:
    """
    Get performance metrics from message logs.

    Includes:
    - Total messages sent (by channel)
    - Message events breakdown
    - Failure analysis

    Args:
        db: Database session
        days: Number of days to look back (default: 7)

    Returns:
        Dictionary with performance metrics
    """
    start_date = datetime.utcnow() - timedelta(days=days)

    try:
        # Get event counts
        event_query = (
            select(
                MessageLog.event,
                func.count(MessageLog.id).label("count")
            )
            .where(MessageLog.created_at >= start_date)
            .group_by(MessageLog.event)
        )
        event_result = await db.execute(event_query)
        events = {row.event: row.count for row in event_result}

        # Get direction counts
        direction_query = (
            select(
                MessageLog.direction,
                func.count(MessageLog.id).label("count")
            )
            .where(MessageLog.created_at >= start_date)
            .group_by(MessageLog.direction)
        )
        direction_result = await db.execute(direction_query)
        directions = {row.direction: row.count for row in direction_result}

        # Get channel counts
        channel_query = (
            select(
                MessageLog.channel,
                func.count(MessageLog.id).label("count")
            )
            .where(MessageLog.created_at >= start_date)
            .group_by(MessageLog.channel)
        )
        channel_result = await db.execute(channel_query)
        channels = {row.channel: row.count for row in channel_result}

        # Get SMS fallback count
        sms_fallback_query = (
            select(func.count(MessageLog.id))
            .where(
                MessageLog.created_at >= start_date,
                MessageLog.event == "sms_fallback_failed"
            )
        )
        sms_fallback_result = await db.execute(sms_fallback_query)
        sms_fallback_count = sms_fallback_result.scalar() or 0

        result_data = {
            "period_days": days,
            "start_date": start_date.isoformat(),
            "events": events,
            "directions": directions,
            "channels": channels,
            "sms_fallback_triggered": sms_fallback_count,
            "total_messages": sum(directions.values())
        }

        logger.info(
            "Performance metrics calculated",
            extra={
                "days": days,
                "total_messages": result_data["total_messages"],
                "sms_fallbacks": sms_fallback_count
            }
        )

        return result_data

    except Exception as e:
        logger.error(
            "Failed to calculate performance metrics",
            extra={"error": str(e), "days": days},
            exc_info=True
        )
        raise


async def get_message_stats_summary(db: AsyncSession) -> Dict[str, Any]:
    """
    Get summary statistics for all message logs.

    Returns overall counts and key metrics without time filtering.

    Args:
        db: Database session

    Returns:
        Dictionary with summary statistics
    """
    try:
        # Total messages
        total_query = select(func.count(MessageLog.id))
        total_result = await db.execute(total_query)
        total = total_result.scalar() or 0

        # Messages by channel
        channel_query = (
            select(
                MessageLog.channel,
                func.count(MessageLog.id).label("count")
            )
            .group_by(MessageLog.channel)
        )
        channel_result = await db.execute(channel_query)
        by_channel = {row.channel: row.count for row in channel_result}

        # Messages by direction
        direction_query = (
            select(
                MessageLog.direction,
                func.count(MessageLog.id).label("count")
            )
            .group_by(MessageLog.direction)
        )
        direction_result = await db.execute(direction_query)
        by_direction = {row.direction: row.count for row in direction_result}

        # Failed messages
        failed_query = (
            select(func.count(MessageLog.id))
            .where(MessageLog.event.like("%failed%"))
        )
        failed_result = await db.execute(failed_query)
        failed_count = failed_result.scalar() or 0

        result_data = {
            "total_messages": total,
            "by_channel": by_channel,
            "by_direction": by_direction,
            "failed_messages": failed_count,
            "success_rate": round((total - failed_count) / total, 4) if total > 0 else 0.0
        }

        logger.info(
            "Message stats summary calculated",
            extra={
                "total": total,
                "success_rate": result_data["success_rate"]
            }
        )

        return result_data

    except Exception as e:
        logger.error(
            "Failed to calculate message stats summary",
            extra={"error": str(e)},
            exc_info=True
        )
        raise