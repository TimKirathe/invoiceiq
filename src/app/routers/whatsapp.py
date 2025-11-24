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


def validate_webhook_signature(payload: dict[str, Any], signature: str) -> bool:
    """
    Validate WhatsApp webhook signature for production security.

    TODO: Implement 360 Dialog webhook signature verification.
    360 Dialog uses a different signature method than Meta WABA.
    See: https://docs.360dialog.com/webhooks/signature-verification

    This is a placeholder for MVP - production should validate webhooks
    using 360 Dialog's signature verification method.

    Args:
        payload: The webhook payload dict
        signature: The signature header value

    Returns:
        bool: True if signature is valid (always True in MVP)
    """
    # MVP: Log warning but don't block requests
    logger.warning(
        "Webhook signature validation is disabled in MVP. "
        "Implement 360 Dialog signature verification for production!",
        extra={"has_signature": bool(signature)},
    )
    return True  # Always allow in MVP


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

    # Validate verify token (standard WhatsApp webhook verification)
    if hub_verify_token != settings.webhook_verify_token:
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

                # Lookup invoice in database (Task 2.1)
                from ..models import Invoice, Payment
                from sqlalchemy import select
                from ..services.mpesa import MPesaService
                from uuid import uuid4
                import time

                invoice_stmt = select(Invoice).where(Invoice.id == invoice_id)
                invoice_result = await db.execute(invoice_stmt)
                invoice = invoice_result.scalar_one_or_none()

                # Validate invoice exists
                if not invoice:
                    logger.warning(
                        "Invoice not found for payment button",
                        extra={"invoice_id": invoice_id, "sender": sender},
                    )
                    response_text = (
                        f"Invoice {invoice_id} not found. Please contact the merchant."
                    )
                # Check if already paid
                elif invoice.status == "PAID":
                    logger.info(
                        "Invoice already paid",
                        extra={"invoice_id": invoice_id, "sender": sender},
                    )
                    response_text = (
                        f"Invoice {invoice_id} has already been paid. "
                        f"Receipt: {invoice.pay_ref or 'N/A'}"
                    )
                # Validate invoice status allows payment
                elif invoice.status not in ["SENT", "PENDING"]:
                    logger.warning(
                        "Invalid invoice status for payment",
                        extra={"invoice_id": invoice_id, "status": invoice.status},
                    )
                    response_text = (
                        f"Invoice {invoice_id} cannot be paid (status: {invoice.status}). "
                        f"Please contact the merchant."
                    )
                else:
                    # Invoice is valid for payment - initiate STK Push
                    try:
                        mpesa_service = MPesaService(environment=settings.mpesa_environment)

                        # Generate idempotency key
                        idempotency_key = f"{invoice_id}-button-{int(time.time())}"

                        # Check if payment already exists for this invoice (duplicate prevention)
                        existing_payment_stmt = select(Payment).where(
                            Payment.invoice_id == invoice_id,
                            Payment.status == "INITIATED"
                        )
                        existing_payment_result = await db.execute(existing_payment_stmt)
                        existing_payment = existing_payment_result.scalar_one_or_none()

                        if existing_payment:
                            logger.info(
                                "Payment already initiated for this invoice",
                                extra={"invoice_id": invoice_id, "payment_id": existing_payment.id},
                            )
                            response_text = (
                                "Payment request already sent! Check your phone for the M-PESA prompt. "
                                "If you didn't receive it, please try again in a moment."
                            )
                        else:
                            # Convert amount from cents to whole KES
                            amount_kes = round(invoice.amount_cents / 100)

                            # Create Payment record with status INITIATED
                            payment = Payment(
                                id=str(uuid4()),
                                invoice_id=invoice.id,
                                method="MPESA_STK",
                                status="INITIATED",
                                amount_cents=invoice.amount_cents,
                                idempotency_key=idempotency_key,
                                raw_request={},
                                raw_callback=None,
                                mpesa_receipt=None,
                            )

                            db.add(payment)
                            await db.commit()
                            await db.refresh(payment)

                            # Prepare STK Push request (M-PESA field limits)
                            account_reference = invoice.id[:20]  # Max 20 characters
                            transaction_desc = invoice.description[:20]  # Max 20 characters

                            # Initiate STK Push
                            stk_response = await mpesa_service.initiate_stk_push(
                                phone_number=sender,  # Customer's phone (sender of button click)
                                amount=amount_kes,
                                account_reference=account_reference,
                                transaction_desc=transaction_desc,
                            )

                            # Update payment with raw request and response
                            payment.raw_request = {
                                "phone_number": sender,
                                "amount": amount_kes,
                                "account_reference": account_reference,
                                "transaction_desc": transaction_desc,
                                "stk_response": stk_response,
                            }

                            # Store CheckoutRequestID and MerchantRequestID for callback matching
                            payment.checkout_request_id = stk_response.get("CheckoutRequestID")
                            payment.merchant_request_id = stk_response.get("MerchantRequestID")

                            await db.commit()
                            await db.refresh(payment)

                            logger.info(
                                "STK Push initiated from button click",
                                extra={
                                    "payment_id": payment.id,
                                    "invoice_id": invoice.id,
                                    "customer_msisdn": sender,
                                },
                            )

                            response_text = (
                                f"Check your phone for the M-PESA payment prompt!\n"
                                f"Amount: KES {amount_kes}\n"
                                f"You'll receive a receipt once payment is complete."
                            )

                    except Exception as stk_error:
                        logger.error(
                            "Failed to initiate STK Push from button click",
                            extra={
                                "error": str(stk_error),
                                "invoice_id": invoice_id,
                                "sender": sender,
                            },
                            exc_info=True,
                        )
                        # Update payment status to FAILED if it was created
                        if 'payment' in locals():
                            payment.status = "FAILED"
                            payment.raw_request = {
                                "error": str(stk_error),
                                "phone_number": sender,
                                "amount": amount_kes if 'amount_kes' in locals() else None,
                            }
                            await db.commit()

                        response_text = (
                            "Failed to initiate payment. Please try again or contact support. "
                            f"Error: {str(stk_error)}"
                        )

                # Create MessageLog entry for button click (Task 2.2)
                try:
                    button_click_log = MessageLog(
                        invoice_id=invoice_id,
                        channel="WHATSAPP",
                        direction="IN",
                        event="payment_button_clicked",
                        payload={
                            "button_id": message_text,
                            "invoice_id": invoice_id,
                            "payment_initiated": 'payment' in locals() and payment.status == "INITIATED",
                            "payment_id": payment.id if 'payment' in locals() else None,
                            "stk_request_sent": 'stk_response' in locals(),
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
                # One-line invoice command: invoice <phone> <amount> <description>
                logger.info(
                    "One-line invoice command received",
                    extra={"params": params, "sender": sender},
                )

                # Check if parser returned an error (Task 1.3)
                if "error" in params:
                    response_text = params["error"]
                    logger.warning(
                        "One-line invoice validation error",
                        extra={"error": params["error"], "sender": sender},
                    )
                else:
                    # Task 1.1: Implement invoice creation
                    # Validate that we have phone (not name)
                    if "name" in params:
                        # This should not happen due to Task 1.2, but defensive check
                        response_text = (
                            "For quick invoice, please use phone number format:\n"
                            "invoice 2547XXXXXXXX <amount> <description>"
                        )
                    elif "phone" not in params:
                        response_text = (
                            "Invalid invoice format. Use:\n"
                            "invoice <phone> <amount> <description>\n"
                            "Example: invoice 254712345678 1000 Web design services"
                        )
                    else:
                        # Extract parameters
                        customer_msisdn = params["phone"]
                        amount = params.get("amount")
                        description = params.get("description")

                        # Validate amount
                        if not amount or amount < 1:
                            response_text = "Amount must be at least 1 KES"
                        # Validate description
                        elif not description or len(description) < 3:
                            response_text = "Description must be at least 3 characters"
                        elif len(description) > 120:
                            response_text = "Description must not exceed 120 characters"
                        else:
                            # Create invoice
                            try:
                                from ..models import Invoice
                                from ..schemas import InvoiceCreate

                                invoice_create = InvoiceCreate(
                                    msisdn=customer_msisdn,
                                    customer_name=None,  # Not provided in one-line command
                                    merchant_msisdn=sender,
                                    amount_cents=amount * 100,  # Convert to cents
                                    description=description,
                                )

                                # Generate invoice ID
                                import random
                                import time
                                timestamp = int(time.time())
                                random_num = random.randint(1000, 9999)
                                invoice_id = f"INV-{timestamp}-{random_num}"

                                # Calculate VAT (16% of total amount)
                                # Total amount includes VAT, so VAT = (amount_cents * 16) / 116
                                vat_amount = int((invoice_create.amount_cents * 16) / 116)

                                # Create invoice record
                                invoice = Invoice(
                                    id=invoice_id,
                                    customer_name=invoice_create.customer_name,
                                    msisdn=invoice_create.msisdn,
                                    merchant_msisdn=invoice_create.merchant_msisdn,
                                    amount_cents=invoice_create.amount_cents,
                                    vat_amount=vat_amount,
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
                                    "Invoice created from one-line command",
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

                                    # Override response text (confirmation already sent)
                                    response_text = None
                                else:
                                    logger.warning(
                                        "Invoice created but failed to send (one-line command)",
                                        extra={"invoice_id": invoice.id},
                                    )
                                    response_text = (
                                        f"Invoice {invoice.id} created but failed to send to customer. "
                                        f"Status: PENDING. You can try again later."
                                    )

                            except ValueError as ve:
                                # Validation error
                                logger.warning(
                                    "Validation error in one-line invoice",
                                    extra={"error": str(ve), "sender": sender},
                                )
                                response_text = f"Invalid invoice data: {str(ve)}"

                            except Exception as e:
                                logger.error(
                                    "Failed to create invoice from one-line command",
                                    extra={"error": str(e), "merchant_msisdn": sender},
                                    exc_info=True,
                                )
                                await db.rollback()
                                response_text = (
                                    "Failed to create invoice. Please try again or use the guided flow "
                                    "(send 'invoice' without parameters)."
                                )

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
                        merchant_msisdn=sender,  # Merchant is the sender of the message
                        amount_cents=invoice_data["amount_cents"],
                        description=invoice_data["description"],
                    )

                    # Generate invoice ID
                    import random
                    import time
                    timestamp = int(time.time())
                    random_num = random.randint(1000, 9999)
                    invoice_id = f"INV-{timestamp}-{random_num}"

                    # Calculate VAT (16% of total amount)
                    # Total amount includes VAT, so VAT = (amount_cents * 16) / 116
                    vat_amount = int((invoice_create.amount_cents * 16) / 116)

                    # Create invoice record
                    invoice = Invoice(
                        id=invoice_id,
                        customer_name=invoice_create.customer_name,
                        msisdn=invoice_create.msisdn,
                        merchant_msisdn=invoice_create.merchant_msisdn,
                        amount_cents=invoice_create.amount_cents,
                        vat_amount=vat_amount,
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