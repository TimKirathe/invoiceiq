"""
WhatsApp webhook router for InvoiceIQ.

This module handles WhatsApp Cloud API webhook verification and inbound message
processing. It implements the GET endpoint for webhook verification and POST
endpoint for receiving messages and interactive button responses.
"""

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db
from ..models import MessageLog
from ..services.whatsapp import WhatsAppService
from ..utils.logging import get_logger

# Set up logger
logger = get_logger(__name__)

# Create router
router = APIRouter()


class WebhookPayload(BaseModel):
    """
    Schema for WhatsApp webhook POST payload.

    This is a flexible schema that accepts any JSON structure from WhatsApp.
    The actual structure will be validated and parsed in future phases.
    """

    model_config = ConfigDict(
        extra="allow",  # Allow additional fields not explicitly defined
    )

    # WhatsApp sends complex nested structures, so we accept a dict
    object: Optional[str] = None
    entry: Optional[list[dict[str, Any]]] = None


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
) -> Response:
    """
    WhatsApp webhook verification endpoint (GET).

    This endpoint is called by Meta/WhatsApp to verify the webhook URL.
    It validates the verify token and returns the challenge string if valid.

    Args:
        hub_mode: Should be "subscribe" for subscription verification
        hub_verify_token: Token to verify against configured WABA_VERIFY_TOKEN
        hub_challenge: Challenge string to return if verification succeeds

    Returns:
        Plain text response with the challenge string

    Raises:
        HTTPException: 403 if verification fails
    """
    logger.info(
        "Webhook verification attempt",
        extra={
            "hub_mode": hub_mode,
            "hub_verify_token": hub_verify_token[:5] + "..." if hub_verify_token else None,
        },
    )

    # Validate hub.mode
    if hub_mode != "subscribe":
        logger.warning(
            "Webhook verification failed: invalid hub.mode",
            extra={"hub_mode": hub_mode},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid hub.mode - expected 'subscribe'",
        )

    # Validate verify token
    if hub_verify_token != settings.waba_verify_token:
        logger.warning(
            "Webhook verification failed: invalid verify token",
            extra={"provided_token_length": len(hub_verify_token)},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid verify token",
        )

    # Verification successful - return challenge
    logger.info(
        "Webhook verification successful",
        extra={"challenge_length": len(hub_challenge)},
    )

    return Response(content=hub_challenge, media_type="text/plain")


@router.post("/webhook")
async def receive_webhook(
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """
    WhatsApp webhook receiver endpoint (POST).

    This endpoint receives inbound messages, delivery receipts, and interactive
    button responses from WhatsApp Cloud API. For Phase 4, it simply logs the
    payload and stores it in the message_log table.

    Args:
        payload: The JSON payload from WhatsApp
        db: Database session dependency

    Returns:
        Dictionary with status: received
    """
    logger.info(
        "Webhook received",
        extra={
            "payload_keys": list(payload.keys()),
            "object_type": payload.get("object"),
        },
    )

    # Log the full payload for debugging
    logger.debug(
        "Full webhook payload",
        extra={"payload": payload},
    )

    # Initialize WhatsApp service
    whatsapp_service = WhatsAppService()

    # Parse incoming message
    parsed_message = whatsapp_service.parse_incoming_message(payload)

    if parsed_message:
        sender = parsed_message["from"]
        message_text = parsed_message["text"]
        message_type = parsed_message["type"]

        logger.info(
            "Parsed message",
            extra={
                "sender": sender,
                "message_type": message_type,
                "text": message_text,
            },
        )

        # Handle button clicks (interactive messages)
        response_text = None
        if message_type == "interactive":
            # Check if it's a payment button click
            if message_text.startswith("pay_"):
                invoice_id = message_text[4:]  # Remove "pay_" prefix
                logger.info(
                    "Payment button clicked",
                    extra={"sender": sender, "invoice_id": invoice_id},
                )

                # For now, just acknowledge - Phase 7 will implement STK Push
                response_text = (
                    f"Payment request received for invoice {invoice_id}. "
                    f"STK Push will be implemented in Phase 7."
                )

                # Create MessageLog entry for button click (metadata only - privacy-first)
                try:
                    button_click_log = MessageLog(
                        invoice_id=invoice_id,
                        channel="WHATSAPP",
                        direction="IN",
                        event="payment_button_clicked",
                        payload={
                            "button_id": message_text,
                            "invoice_id": invoice_id,
                            "timestamp": datetime.utcnow().isoformat(),
                        },
                    )
                    db.add(button_click_log)
                    await db.commit()
                    logger.info(
                        "Button click logged",
                        extra={"invoice_id": invoice_id, "message_log_id": button_click_log.id},
                    )
                except Exception as log_error:
                    logger.error(
                        "Failed to log button click",
                        extra={"error": str(log_error), "invoice_id": invoice_id},
                    )
            else:
                # Unknown button click
                logger.warning(
                    "Unknown button clicked",
                    extra={"sender": sender, "button_id": message_text},
                )
                response_text = "Button received. I'm not sure what to do with this."

        # Check if user has active state or is starting a new flow
        state_info = whatsapp_service.state_manager.get_state(sender)
        is_in_flow = state_info["state"] != whatsapp_service.state_manager.STATE_IDLE

        # Parse command if not in flow and not already handled
        if not is_in_flow and response_text is None:
            command_info = whatsapp_service.parse_command(message_text)
            command = command_info["command"]
            params = command_info["params"]

            logger.info(
                "Command parsed",
                extra={"command": command, "params": params, "sender": sender},
            )

            if command == "start_guided":
                # Start guided flow
                flow_result = whatsapp_service.handle_guided_flow(sender, message_text)
                response_text = flow_result["response"]

            elif command == "help":
                response_text = (
                    "InvoiceIQ Bot Commands:\n\n"
                    "- invoice: Start guided invoice creation\n"
                    "- invoice <phone> <amount> <desc>: Quick invoice\n"
                    "- remind <invoice_id>: Send reminder\n"
                    "- cancel <invoice_id>: Cancel invoice\n"
                    "- help: Show this help"
                )

            elif command == "invoice":
                # One-line invoice command (will be implemented in Phase 6)
                logger.info(
                    "One-line invoice command received",
                    extra={"params": params, "sender": sender},
                )
                response_text = "One-line invoice creation will be implemented in Phase 6."

            elif command == "remind":
                # Remind command (will be implemented later)
                logger.info(
                    "Remind command received",
                    extra={"invoice_id": params.get("invoice_id"), "sender": sender},
                )
                response_text = f"Reminder for invoice {params.get('invoice_id')} will be sent in a future phase."

            elif command == "cancel":
                # Cancel command (will be implemented later)
                logger.info(
                    "Cancel command received",
                    extra={"invoice_id": params.get("invoice_id"), "sender": sender},
                )
                response_text = f"Cancellation of invoice {params.get('invoice_id')} will be implemented in a future phase."

            elif command == "unknown":
                response_text = (
                    "I didn't understand that command. Send 'help' for available commands."
                )

        elif is_in_flow and response_text is None:
            # User is in guided flow
            flow_result = whatsapp_service.handle_guided_flow(sender, message_text)
            response_text = flow_result["response"]
            logger.info(
                "Guided flow processed",
                extra={
                    "sender": sender,
                    "action": flow_result.get("action"),
                    "state": state_info["state"],
                },
            )

            # If user confirmed, create the invoice
            if flow_result.get("action") == "confirmed" and flow_result.get("invoice_data"):
                from ..models import Invoice
                from ..schemas import InvoiceCreate

                invoice_data = flow_result["invoice_data"]

                # Create InvoiceCreate schema
                try:
                    invoice_create = InvoiceCreate(
                        msisdn=invoice_data["phone"],
                        customer_name=invoice_data.get("name"),
                        amount_cents=invoice_data["amount_cents"],
                        description=invoice_data["description"],
                    )

                    # Generate invoice ID
                    import random
                    import time
                    timestamp = int(time.time())
                    random_num = random.randint(1000, 9999)
                    invoice_id = f"INV-{timestamp}-{random_num}"

                    # Create invoice record
                    invoice = Invoice(
                        id=invoice_id,
                        customer_name=invoice_create.customer_name,
                        msisdn=invoice_create.msisdn,
                        amount_cents=invoice_create.amount_cents,
                        currency="KES",
                        description=invoice_create.description,
                        status="PENDING",
                        pay_ref=None,
                        pay_link=None,
                    )

                    db.add(invoice)
                    await db.commit()
                    await db.refresh(invoice)

                    logger.info(
                        "Invoice created from guided flow",
                        extra={"invoice_id": invoice.id, "merchant_msisdn": sender},
                    )

                    # Send invoice to customer
                    send_success = await whatsapp_service.send_invoice_to_customer(
                        invoice_id=invoice.id,
                        customer_msisdn=invoice.msisdn,
                        customer_name=invoice.customer_name,
                        amount_cents=invoice.amount_cents,
                        description=invoice.description,
                        db_session=db,
                    )

                    # Update invoice status
                    if send_success:
                        invoice.status = "SENT"
                        await db.commit()
                        await db.refresh(invoice)

                        # Send merchant confirmation
                        await whatsapp_service.send_merchant_confirmation(
                            merchant_msisdn=sender,
                            invoice_id=invoice.id,
                            customer_msisdn=invoice.msisdn,
                            amount_cents=invoice.amount_cents,
                            status=invoice.status,
                        )

                        # Override response text
                        response_text = None  # Merchant confirmation already sent
                    else:
                        logger.warning(
                            "Invoice created but failed to send",
                            extra={"invoice_id": invoice.id},
                        )
                        response_text = (
                            f"Invoice {invoice.id} created but failed to send to customer. "
                            f"Status: PENDING. You can try again later."
                        )

                except Exception as invoice_error:
                    logger.error(
                        "Failed to create invoice from guided flow",
                        extra={"error": str(invoice_error), "merchant_msisdn": sender},
                        exc_info=True,
                    )
                    await db.rollback()
                    response_text = (
                        "Failed to create invoice. Please try again by sending 'invoice'."
                    )

        # Send response to user
        if response_text:
            try:
                await whatsapp_service.send_message(sender, response_text)
                logger.info(
                    "Response sent to user",
                    extra={"sender": sender, "response_length": len(response_text)},
                )
            except Exception as send_error:
                logger.error(
                    "Failed to send response to user",
                    extra={"error": str(send_error), "sender": sender},
                    exc_info=True,
                )

    try:
        # Create MessageLog entry (metadata only - privacy-first)
        # Extract minimal metadata from payload
        try:
            entry = payload.get("entry", [{}])[0]
            changes = entry.get("changes", [{}])[0]
            value = changes.get("value", {})
            messages = value.get("messages", [{}])
            event_type = "message" if messages else "status_update"
            message_id = messages[0].get("id") if messages else None
        except (IndexError, KeyError, TypeError):
            event_type = "unknown"
            message_id = None

        message_log = MessageLog(
            invoice_id=None,  # No invoice context yet
            channel="WHATSAPP",
            direction="IN",
            event="webhook_received",
            payload={
                "event_type": event_type,
                "message_id": message_id,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

        db.add(message_log)
        await db.commit()

        logger.info(
            "Webhook payload logged to database",
            extra={"message_log_id": message_log.id},
        )

    except Exception as e:
        logger.error(
            "Failed to log webhook payload to database",
            extra={"error": str(e)},
            exc_info=True,
        )
        # Don't fail the webhook - WhatsApp expects 200 OK
        # Just log the error and continue
        await db.rollback()

    return {"status": "received"}