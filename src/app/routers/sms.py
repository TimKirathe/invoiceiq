"""
SMS webhook router for InvoiceIQ.

This module handles SMS callbacks from Africa's Talking API, including
inbound SMS messages and delivery receipt callbacks.
"""

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import MessageLog
from ..services.sms import SMSService
from ..utils.logging import get_logger

# Set up logger
logger = get_logger(__name__)

# Create router
router = APIRouter()


@router.post("/inbound")
async def receive_inbound_sms(
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """
    Receive inbound SMS messages from Africa's Talking.

    This endpoint receives SMS replies from customers and merchants.
    Supports all merchant commands (invoice, remind, cancel, help)
    and payment commands (PAY).

    Args:
        payload: The callback payload from Africa's Talking
        db: Database session dependency

    Returns:
        Dictionary with status: received
    """
    logger.info(
        "Inbound SMS received",
        extra={
            "payload_keys": list(payload.keys()),
        },
    )

    # Log the full payload for debugging
    logger.debug(
        "Full inbound SMS payload",
        extra={"payload": payload},
    )

    # Initialize SMS service
    sms_service = SMSService()

    # Parse the callback
    parsed_sms = sms_service.parse_africas_talking_callback(payload)

    if parsed_sms:
        sender = parsed_sms["from"]
        message_text = parsed_sms["text"]
        message_id = parsed_sms["message_id"]

        logger.info(
            "Parsed inbound SMS",
            extra={
                "from": sender,
                "message_id": message_id,
                "text": message_text,
            },
        )

        # Parse command
        command_info = sms_service.parse_sms_command(message_text)
        command = command_info["command"]
        params = command_info["params"]

        logger.info(
            "SMS command parsed",
            extra={
                "command": command,
                "params": params,
                "from": sender,
            },
        )

        # Handle different commands
        # For Phase 9, we just log the commands and acknowledge receipt
        # Full command handling can be implemented in future phases
        if command == "pay":
            logger.info(
                "Payment command received via SMS",
                extra={"from": sender},
            )
            # TODO: Implement payment initiation in future phase

        elif command == "help":
            logger.info(
                "Help command received via SMS",
                extra={"from": sender},
            )
            # TODO: Send help response in future phase

        elif command == "start_guided":
            logger.info(
                "Start guided flow command received via SMS",
                extra={"from": sender},
            )
            # TODO: Implement guided flow for SMS in future phase

        elif command == "invoice":
            logger.info(
                "One-line invoice command received via SMS",
                extra={"params": params, "from": sender},
            )
            # TODO: Implement invoice creation from SMS in future phase

        elif command == "remind":
            logger.info(
                "Remind command received via SMS",
                extra={"invoice_id": params.get("invoice_id"), "from": sender},
            )
            # TODO: Implement remind functionality in future phase

        elif command == "cancel":
            logger.info(
                "Cancel command received via SMS",
                extra={"invoice_id": params.get("invoice_id"), "from": sender},
            )
            # TODO: Implement cancel functionality in future phase

        elif command == "unknown":
            logger.warning(
                "Unknown SMS command received",
                extra={"from": sender, "text": message_text},
            )
            # TODO: Send error response in future phase

        # Create MessageLog entry for inbound SMS
        try:
            message_log = MessageLog(
                invoice_id=None,  # No invoice context yet
                channel="SMS",
                direction="IN",
                event="sms_received",
                payload={
                    "from": sender,
                    "text": message_text,
                    "message_id": message_id,
                    "command": command,
                    "params": params,
                    "raw_payload": payload,
                },
            )
            db.add(message_log)
            await db.commit()

            logger.info(
                "Inbound SMS logged to database",
                extra={"message_log_id": message_log.id, "from": sender},
            )

        except Exception as e:
            logger.error(
                "Failed to log inbound SMS to database",
                extra={"error": str(e), "from": sender},
                exc_info=True,
            )
            # Don't fail the webhook - Africa's Talking expects 200 OK
            await db.rollback()

    else:
        logger.warning(
            "Failed to parse inbound SMS",
            extra={"payload": payload},
        )

    return {"status": "received"}


@router.post("/status")
async def receive_delivery_status(
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """
    Receive delivery receipt callbacks from Africa's Talking.

    This endpoint receives delivery status updates for sent SMS messages.
    Updates the message_log with delivery status.

    Args:
        payload: The delivery receipt payload from Africa's Talking
        db: Database session dependency

    Returns:
        Dictionary with status: received
    """
    logger.info(
        "SMS delivery receipt received",
        extra={
            "payload_keys": list(payload.keys()),
        },
    )

    # Log the full payload for debugging
    logger.debug(
        "Full delivery receipt payload",
        extra={"payload": payload},
    )

    # Initialize SMS service
    sms_service = SMSService()

    # Parse the delivery receipt
    parsed_receipt = sms_service.parse_delivery_receipt(payload)

    if parsed_receipt:
        message_id = parsed_receipt["message_id"]
        status = parsed_receipt["status"]
        phone_number = parsed_receipt["phone_number"]

        logger.info(
            "Parsed delivery receipt",
            extra={
                "message_id": message_id,
                "status": status,
                "phone_number": phone_number,
            },
        )

        # Create MessageLog entry for delivery status
        try:
            message_log = MessageLog(
                invoice_id=None,  # No invoice context (could be linked in future)
                channel="SMS",
                direction="OUT",
                event=f"delivery_{status.lower()}" if status else "delivery_status",
                payload={
                    "message_id": message_id,
                    "status": status,
                    "phone_number": phone_number,
                    "retry_count": parsed_receipt["retry_count"],
                    "raw_payload": payload,
                },
            )
            db.add(message_log)
            await db.commit()

            logger.info(
                "Delivery receipt logged to database",
                extra={
                    "message_log_id": message_log.id,
                    "status": status,
                },
            )

        except Exception as e:
            logger.error(
                "Failed to log delivery receipt to database",
                extra={"error": str(e)},
                exc_info=True,
            )
            # Don't fail the webhook - Africa's Talking expects 200 OK
            await db.rollback()

    else:
        logger.warning(
            "Failed to parse delivery receipt",
            extra={"payload": payload},
        )

    return {"status": "received"}