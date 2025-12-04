"""
WhatsApp webhook router for InvoiceIQ.

This module handles WhatsApp Cloud API webhook verification and inbound message
processing. It implements the GET endpoint for webhook verification and POST
endpoint for receiving messages and interactive button responses.
"""

import random
import time
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict

from ..config import settings
from ..db import get_supabase
from ..services.mpesa import MPesaService
from ..services.whatsapp import WhatsAppService
from ..utils.logging import get_logger
from ..utils.payment_retry import (
    can_retry_payment,
    get_payment_by_invoice_id,
    reset_invoice_to_pending,
)

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
            "hub_verify_token": hub_verify_token[:5] + "..."
            if hub_verify_token
            else None,
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
) -> dict[str, str]:
    """
    WhatsApp webhook receiver endpoint (POST).

    This endpoint receives inbound messages, delivery receipts, and interactive
    button responses from WhatsApp Cloud API. For Phase 4, it simply logs the
    payload and stores it in the message_log table.

    Args:
        payload: The JSON payload from WhatsApp

    Returns:
        Dictionary with status: received
    """
    supabase = get_supabase()

    logger.info(
        "Webhook received",
        extra={
            "payload_keys": list(payload.keys()),
            "object_type": payload.get("object"),
        },
    )

    # Initialize WhatsApp service
    whatsapp_service = WhatsAppService()

    # Parse incoming message
    parsed_message = whatsapp_service.parse_incoming_message(payload)

    if not parsed_message:
        logger.warning(
            "Message parsing returned None - skipping message handling",
            extra={"payload": payload},
        )
        # Continue to database logging...

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
                invoice_response = (
                    supabase.table("invoices")
                    .select("*")
                    .eq("id", invoice_id)
                    .execute()
                )
                invoice = invoice_response.data[0] if invoice_response.data else None

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
                elif invoice["status"] == "PAID":
                    logger.info(
                        "Invoice already paid",
                        extra={"invoice_id": invoice_id, "sender": sender},
                    )
                    response_text = (
                        f"Invoice {invoice_id} has already been paid. "
                        f"Receipt: {invoice.get('pay_ref') or 'N/A'}"
                    )
                # Validate invoice status allows payment - allow SENT, PENDING, and FAILED (for retries)
                elif invoice["status"] not in ["SENT", "PENDING", "FAILED"]:
                    logger.warning(
                        "Invalid invoice status for payment",
                        extra={"invoice_id": invoice_id, "status": invoice["status"]},
                    )
                    response_text = (
                        f"Invoice {invoice_id} cannot be paid (status: {invoice['status']}). "
                        f"Please contact the merchant."
                    )
                # Handle FAILED status with retry logic
                elif invoice["status"] == "FAILED":
                    logger.info(
                        "Attempting payment retry for FAILED invoice",
                        extra={"invoice_id": invoice_id},
                    )

                    # Get existing payment record
                    existing_payment_for_retry = get_payment_by_invoice_id(
                        invoice_id, supabase
                    )

                    if not existing_payment_for_retry:
                        logger.warning(
                            "No payment record found for FAILED invoice",
                            extra={"invoice_id": invoice_id},
                        )
                        response_text = (
                            "Payment record not found. Please contact support."
                        )
                    else:
                        # Check if retry is allowed
                        can_retry_result, error_message = can_retry_payment(
                            existing_payment_for_retry
                        )

                        if not can_retry_result:
                            logger.info(
                                "Payment retry blocked",
                                extra={
                                    "invoice_id": invoice_id,
                                    "reason": error_message,
                                },
                            )
                            response_text = error_message
                        else:
                            # Increment retry count on existing payment
                            from datetime import timezone

                            current_retry_count = existing_payment_for_retry.get(
                                "retry_count", 0
                            )
                            try:
                                supabase.table("payments").update(
                                    {
                                        "retry_count": current_retry_count + 1,
                                        "updated_at": datetime.now(
                                            timezone.utc
                                        ).isoformat(),
                                    }
                                ).eq("id", existing_payment_for_retry["id"]).execute()

                                logger.info(
                                    "Incremented payment retry_count for retry attempt",
                                    extra={
                                        "payment_id": existing_payment_for_retry["id"],
                                        "new_retry_count": current_retry_count + 1,
                                    },
                                )
                            except Exception as retry_error:
                                logger.error(
                                    "Failed to increment retry_count",
                                    extra={
                                        "error": str(retry_error),
                                        "payment_id": existing_payment_for_retry["id"],
                                    },
                                    exc_info=True,
                                )
                                response_text = "Failed to update payment retry count. Please try again."

                            # Reset invoice status to PENDING for retry (only if increment succeeded)
                            if response_text is None:
                                if not reset_invoice_to_pending(invoice_id, supabase):
                                    logger.error(
                                        "Failed to reset invoice status for retry",
                                        extra={"invoice_id": invoice_id},
                                    )
                                    response_text = "Failed to reset invoice status. Please try again."
                                else:
                                    # Update local invoice object for processing below
                                    invoice["status"] = "PENDING"

                                    logger.info(
                                        "Payment retry approved - proceeding with STK Push",
                                        extra={
                                            "invoice_id": invoice_id,
                                            "retry_count": current_retry_count + 1,
                                        },
                                    )
                # Verify button clicker is the invoice customer (Task 4.1)
                elif sender != invoice["msisdn"]:
                    logger.warning(
                        "Payment button clicked by different phone number",
                        extra={
                            "invoice_id": invoice_id,
                            "invoice_customer": invoice["msisdn"],
                            "button_clicker": sender,
                        },
                    )
                    response_text = (
                        f"This invoice is for {invoice['msisdn']}. "
                        f"If you are the customer, please use the phone number that received the invoice."
                    )
                else:
                    # Invoice is valid for payment - initiate STK Push
                    try:
                        mpesa_service = MPesaService(
                            environment=settings.mpesa_environment
                        )

                        # Generate idempotency key
                        idempotency_key = f"{invoice_id}-button-{int(time.time())}"

                        # Check if payment already exists for this invoice (any status) - Enhanced duplicate prevention (Task 4.3)
                        existing_payment_response = (
                            supabase.table("payments")
                            .select("*")
                            .eq("invoice_id", invoice_id)
                            .order("created_at", desc=True)
                            .limit(1)
                            .execute()
                        )
                        existing_payment = (
                            existing_payment_response.data[0]
                            if existing_payment_response.data
                            else None
                        )

                        if existing_payment:
                            if existing_payment["status"] == "INITIATED":
                                # Payment in progress
                                logger.info(
                                    "Payment already initiated",
                                    extra={
                                        "invoice_id": invoice_id,
                                        "payment_id": existing_payment["id"],
                                    },
                                )
                                response_text = (
                                    "Payment request already sent! Check your phone for the M-PESA prompt. "
                                    "If you didn't receive it, please wait 2 minutes and try again."
                                )
                            elif existing_payment["status"] == "SUCCESS":
                                # Already paid
                                response_text = f"This invoice has already been paid. Receipt: {existing_payment.get('mpesa_receipt') or 'N/A'}"
                            elif existing_payment["status"] == "FAILED":
                                # Previous attempt failed, check cooldown period
                                updated_at = datetime.fromisoformat(
                                    existing_payment["updated_at"].replace(
                                        "Z", "+00:00"
                                    )
                                )
                                time_since_failure = (
                                    datetime.utcnow() - updated_at.replace(tzinfo=None)
                                ).total_seconds()
                                if time_since_failure < 120:  # 2 minutes cooldown
                                    response_text = f"Previous payment failed. Please wait {int(120 - time_since_failure)} seconds before retrying."
                                else:
                                    # Allow retry (continue with STK Push creation)
                                    logger.info(
                                        "Retrying payment after previous failure",
                                        extra={
                                            "invoice_id": invoice_id,
                                            "previous_payment_id": existing_payment[
                                                "id"
                                            ],
                                        },
                                    )
                                    # Continue to STK Push initiation (don't set response_text)
                                    existing_payment = (
                                        None  # Reset to allow continuation
                                    )
                            else:
                                # Unknown status
                                logger.warning(
                                    "Payment exists with unknown status",
                                    extra={
                                        "invoice_id": invoice_id,
                                        "status": existing_payment["status"],
                                    },
                                )
                                response_text = f"Payment status unclear ({existing_payment['status']}). Please contact the merchant."

                        if not existing_payment:
                            # Convert amount from cents to whole KES
                            amount_kes = round(invoice["amount_cents"] / 100)

                            # Create Payment record with status INITIATED
                            payment_data = {
                                "id": str(uuid4()),
                                "invoice_id": invoice["id"],
                                "method": "MPESA_STK",
                                "status": "INITIATED",
                                "amount_cents": invoice["amount_cents"],
                                "idempotency_key": idempotency_key,
                                "raw_request": {},
                                "raw_callback": None,
                                "mpesa_receipt": None,
                            }

                            payment_response = (
                                supabase.table("payments")
                                .insert(payment_data)
                                .execute()
                            )
                            payment = payment_response.data[0]

                            # Prepare STK Push request (M-PESA field limits)
                            # Determine account_reference based on payment method
                            payment_method = invoice.get("mpesa_method")
                            if payment_method == "PAYBILL":
                                # For PAYBILL: use the merchant's paybill account number
                                account_reference = invoice.get(
                                    "mpesa_account_number", invoice["id"][:20]
                                )
                            else:
                                # For TILL (or fallback): use invoice ID
                                account_reference = invoice["id"][:20]

                            # Ensure account_reference is max 20 characters
                            account_reference = account_reference[:20]
                            transaction_desc = "Invoice Payment"  # Generic description

                            # Initiate STK Push
                            # Pass payment method from invoice (PAYBILL or TILL)
                            stk_response = await mpesa_service.initiate_stk_push(
                                phone_number=sender,  # Customer's phone (sender of button click)
                                amount=amount_kes,
                                account_reference=account_reference,
                                transaction_desc=transaction_desc,
                                payment_method=payment_method,
                            )

                            # Update payment with raw request and response
                            updated_payment_data = {
                                "raw_request": {
                                    "phone_number": sender,
                                    "amount": amount_kes,
                                    "account_reference": account_reference,
                                    "transaction_desc": transaction_desc,
                                    "stk_response": stk_response,
                                },
                                "checkout_request_id": stk_response.get(
                                    "CheckoutRequestID"
                                ),
                                "merchant_request_id": stk_response.get(
                                    "MerchantRequestID"
                                ),
                            }

                            supabase.table("payments").update(updated_payment_data).eq(
                                "id", payment["id"]
                            ).execute()
                            payment.update(updated_payment_data)

                            logger.info(
                                "STK Push initiated from button click",
                                extra={
                                    "payment_id": payment["id"],
                                    "invoice_id": invoice["id"],
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
                        if "payment" in locals():
                            failed_payment_data = {
                                "status": "FAILED",
                                "raw_request": {
                                    "error": str(stk_error),
                                    "phone_number": sender,
                                    "amount": amount_kes
                                    if "amount_kes" in locals()
                                    else None,
                                },
                            }
                            supabase.table("payments").update(failed_payment_data).eq(
                                "id", payment["id"]
                            ).execute()

                        response_text = (
                            "Failed to initiate payment. Please try again or contact support. "
                            f"Error: {str(stk_error)}"
                        )

                # Create MessageLog entry for button click (Task 2.2)
                try:
                    button_click_log_data = {
                        "id": str(uuid4()),
                        "invoice_id": invoice_id,
                        "channel": "WHATSAPP",
                        "direction": "IN",
                        "event": "payment_button_clicked",
                        "payload": {
                            "button_id": message_text,
                            "invoice_id": invoice_id,
                            "payment_initiated": "payment" in locals()
                            and payment.get("status") == "INITIATED",
                            "payment_id": payment["id"]
                            if "payment" in locals()
                            else None,
                            "stk_request_sent": "stk_response" in locals(),
                            "timestamp": datetime.utcnow().isoformat(),
                        },
                    }
                    button_click_response = (
                        supabase.table("message_log")
                        .insert(button_click_log_data)
                        .execute()
                    )
                    button_click_log = button_click_response.data[0]

                    logger.info(
                        "Button click logged",
                        extra={
                            "invoice_id": invoice_id,
                            "message_log_id": button_click_log["id"],
                        },
                    )
                except Exception as log_error:
                    logger.error(
                        "Failed to log button click",
                        extra={"error": str(log_error), "invoice_id": invoice_id},
                    )

            elif message_text == "undo":
                # Handle undo button click
                # Get user state to check if they're in a flow
                state_info = whatsapp_service.state_manager.get_state(sender)
                is_in_flow = (
                    state_info["state"] != whatsapp_service.state_manager.STATE_IDLE
                )

                if is_in_flow:
                    # User is in flow, process the undo action
                    flow_result = whatsapp_service.go_back(sender)
                    response_text = flow_result.get(
                        "response",
                        "Sorry, something went wrong. Please start over by sending 'invoice'.",
                    )

                    logger.info(
                        "Undo button clicked and processed",
                        extra={"sender": sender, "action": flow_result.get("action")},
                    )
                else:
                    # User clicked undo but is not in a flow
                    logger.warning(
                        "Undo button clicked but user not in flow",
                        extra={"sender": sender},
                    )
                    response_text = "No active flow to undo. Send 'invoice' to start."

            else:
                # Unknown button click
                logger.warning(
                    "Unknown button clicked",
                    extra={"sender": sender, "button_id": message_text},
                )
                response_text = "Button received. I'm not sure what to do with this."

        # Initialize flow_result to track show_back_button flag
        # (may have been set by undo button handler above)
        if "flow_result" not in locals():
            flow_result = {}

        # Check if user has active state or is starting a new flow (if not already checked)
        if "state_info" not in locals():
            state_info = whatsapp_service.state_manager.get_state(sender)
            is_in_flow = (
                state_info["state"] != whatsapp_service.state_manager.STATE_IDLE
            )

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
                    "📋 InvoiceIQ Commands:\n\n"
                    "📝 invoice - Start guided invoice creation\n"
                    "🔔 remind <invoice_id> - Send reminder\n"
                    "❌ cancel <invoice_id> - Cancel invoice\n"
                    "❓ help - Show this help"
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
                response_text = "I didn't understand that command. Send 'help' for available commands."

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
            if flow_result.get("action") == "confirmed" and flow_result.get(
                "invoice_data"
            ):
                invoice_data_from_flow = flow_result["invoice_data"]

                # Import invoice_parser utilities
                from ..utils.invoice_parser import calculate_invoice_totals

                # Create invoice with all new fields
                try:
                    # Extract data from flow
                    merchant_name = invoice_data_from_flow.get("merchant_name")
                    line_items = invoice_data_from_flow.get("line_items", [])
                    include_vat = invoice_data_from_flow.get("include_vat", False)
                    due_date = invoice_data_from_flow.get("due_date")
                    customer_phone = invoice_data_from_flow["phone"]
                    customer_name = invoice_data_from_flow.get("name")
                    mpesa_method = invoice_data_from_flow.get("mpesa_method")
                    mpesa_paybill_number = invoice_data_from_flow.get(
                        "mpesa_paybill_number"
                    )
                    mpesa_account_number = invoice_data_from_flow.get(
                        "mpesa_account_number"
                    )
                    mpesa_till_number = invoice_data_from_flow.get("mpesa_till_number")
                    mpesa_phone_number = invoice_data_from_flow.get(
                        "mpesa_phone_number"
                    )
                    save_payment_method = invoice_data_from_flow.get(
                        "save_payment_method", False
                    )
                    used_saved_method = invoice_data_from_flow.get(
                        "used_saved_method", False
                    )
                    c2b_notifications_enabled = invoice_data_from_flow.get(
                        "c2b_notifications_enabled", False
                    )

                    # Calculate totals from line items
                    totals = calculate_invoice_totals(line_items, include_vat)
                    total_cents = totals["total_cents"]
                    vat_cents = totals["vat_cents"]

                    # Generate invoice ID
                    timestamp = int(time.time())
                    random_num = random.randint(1000, 9999)
                    invoice_id = f"INV-{timestamp}-{random_num}"

                    # Create invoice record with all new fields
                    invoice_data = {
                        "id": invoice_id,
                        "merchant_name": merchant_name,
                        "customer_name": customer_name,
                        "msisdn": customer_phone,
                        "merchant_msisdn": sender,
                        "amount_cents": total_cents,
                        "vat_amount": vat_cents,
                        "currency": "KES",
                        "line_items": line_items,  # Store as JSONB
                        "due_date": due_date,
                        "mpesa_method": mpesa_method,
                        "mpesa_paybill_number": mpesa_paybill_number,
                        "mpesa_account_number": mpesa_account_number,
                        "mpesa_till_number": mpesa_till_number,
                        "mpesa_phone_number": mpesa_phone_number,
                        "c2b_notifications_enabled": c2b_notifications_enabled,
                        "status": "PENDING",
                        "pay_ref": None,
                        "pay_link": None,
                    }

                    invoice_response = (
                        supabase.table("invoices").insert(invoice_data).execute()
                    )
                    invoice = invoice_response.data[0]

                    logger.info(
                        "Invoice created from guided flow",
                        extra={"invoice_id": invoice["id"], "merchant_msisdn": sender},
                    )

                    # Register C2B URLs if notifications enabled
                    # Skip C2B registration for PHONE payment method (only PAYBILL and TILL supported)
                    if c2b_notifications_enabled and mpesa_method != "PHONE":
                        try:
                            # Determine shortcode and type
                            shortcode = mpesa_paybill_number or mpesa_till_number
                            shortcode_type = (
                                "PAYBILL" if mpesa_paybill_number else "TILL"
                            )
                            account_number = (
                                mpesa_account_number
                                if shortcode_type == "PAYBILL"
                                else None
                            )

                            logger.info(
                                "Checking C2B registration status",
                                extra={
                                    "shortcode": shortcode,
                                    "shortcode_type": shortcode_type,
                                    "account_number": account_number,
                                },
                            )

                            # Check if already registered
                            registration_query = (
                                supabase.table("c2b_registrations")
                                .select("id, registration_status")
                                .eq("shortcode", shortcode)
                            )

                            # Add account_number filter for PAYBILL
                            if account_number:
                                registration_query = registration_query.eq(
                                    "account_number", account_number
                                )
                            else:
                                registration_query = registration_query.is_(
                                    "account_number", "null"
                                )

                            existing_registration = registration_query.execute()

                            if existing_registration.data:
                                logger.info(
                                    "C2B URLs already registered for shortcode",
                                    extra={
                                        "shortcode": shortcode,
                                        "account_number": account_number,
                                        "registration_id": existing_registration.data[
                                            0
                                        ]["id"],
                                        "status": existing_registration.data[0][
                                            "registration_status"
                                        ],
                                    },
                                )
                            else:
                                # Register with Daraja
                                logger.info(
                                    "Registering C2B URLs with M-PESA",
                                    extra={
                                        "shortcode": shortcode,
                                        "shortcode_type": shortcode_type,
                                    },
                                )

                                mpesa_service = MPesaService(
                                    environment=settings.mpesa_environment
                                )
                                registration_result = (
                                    await mpesa_service.register_c2b_url(
                                        shortcode=shortcode,
                                        shortcode_type=shortcode_type,
                                        account_number=account_number,
                                    )
                                )

                                # Store registration result
                                registration_status = (
                                    "SUCCESS"
                                    if registration_result["success"]
                                    else "FAILED"
                                )
                                registration_data = {
                                    "shortcode": shortcode,
                                    "shortcode_type": shortcode_type,
                                    "account_number": account_number,
                                    "vendor_phone": sender,
                                    "confirmation_url": settings.c2b_confirmation_url,
                                    "registration_status": registration_status,
                                    "daraja_response": registration_result,
                                }

                                supabase.table("c2b_registrations").insert(
                                    registration_data
                                ).execute()

                                if registration_result["success"]:
                                    logger.info(
                                        "C2B URLs registered successfully",
                                        extra={
                                            "shortcode": shortcode,
                                            "account_number": account_number,
                                            "response_code": registration_result[
                                                "response_code"
                                            ],
                                        },
                                    )
                                else:
                                    logger.warning(
                                        "C2B URL registration failed but invoice creation continues",
                                        extra={
                                            "shortcode": shortcode,
                                            "account_number": account_number,
                                            "response_code": registration_result[
                                                "response_code"
                                            ],
                                            "response_description": registration_result[
                                                "response_description"
                                            ],
                                        },
                                    )

                        except Exception as c2b_error:
                            logger.error(
                                "C2B registration failed but invoice creation continues",
                                extra={
                                    "error": str(c2b_error),
                                    "shortcode": shortcode
                                    if "shortcode" in locals()
                                    else None,
                                    "invoice_id": invoice["id"],
                                },
                                exc_info=True,
                            )
                            # Don't fail invoice creation if C2B registration fails

                    # Save payment method if requested and not using saved method
                    if save_payment_method and not used_saved_method:
                        try:
                            payment_method_data = {
                                "id": str(uuid4()),
                                "merchant_msisdn": sender,
                                "method_type": mpesa_method,
                                "paybill_number": mpesa_paybill_number
                                if mpesa_method == "PAYBILL"
                                else None,
                                "account_number": mpesa_account_number
                                if mpesa_method == "PAYBILL"
                                else None,
                                "till_number": mpesa_till_number
                                if mpesa_method == "TILL"
                                else None,
                                "phone_number": mpesa_phone_number
                                if mpesa_method == "PHONE"
                                else None,
                                "is_default": False,
                            }
                            supabase.table("merchant_payment_methods").insert(
                                payment_method_data
                            ).execute()
                            logger.info(
                                "Payment method saved",
                                extra={
                                    "merchant_msisdn": sender,
                                    "method_type": mpesa_method,
                                },
                            )
                        except Exception as save_error:
                            logger.error(
                                "Failed to save payment method",
                                extra={
                                    "error": str(save_error),
                                    "merchant_msisdn": sender,
                                },
                                exc_info=True,
                            )
                            # Don't fail invoice creation if payment method save fails

                    # Send invoice to customer (will use WhatsApp template)
                    send_success = await whatsapp_service.send_invoice_to_customer(
                        invoice_id=invoice["id"],
                        customer_msisdn=invoice["msisdn"],
                        customer_name=invoice.get("customer_name"),
                        amount_cents=invoice["amount_cents"],
                        db_session=supabase,
                        invoice=invoice,  # Pass full invoice for template rendering
                    )

                    # Update invoice status
                    if send_success:
                        supabase.table("invoices").update({"status": "SENT"}).eq(
                            "id", invoice["id"]
                        ).execute()
                        invoice["status"] = "SENT"

                        # Send merchant confirmation
                        await whatsapp_service.send_merchant_confirmation(
                            merchant_msisdn=sender,
                            invoice_id=invoice["id"],
                            customer_msisdn=invoice["msisdn"],
                            amount_cents=invoice["amount_cents"],
                            status=invoice["status"],
                        )

                        # Override response text
                        response_text = None  # Merchant confirmation already sent
                    else:
                        logger.warning(
                            "Invoice created but failed to send",
                            extra={"invoice_id": invoice["id"]},
                        )
                        response_text = (
                            f"Invoice {invoice['id']} created but failed to send to customer. "
                            f"Status: PENDING. You can try again later."
                        )

                except Exception as invoice_error:
                    logger.error(
                        "Failed to create invoice from guided flow",
                        extra={"error": str(invoice_error), "merchant_msisdn": sender},
                        exc_info=True,
                    )
                    response_text = "Failed to create invoice. Please try again by sending 'invoice'."

        # Send response to user
        if response_text:
            # Check if we should show back button
            show_back_button = flow_result.get("show_back_button", False)

            logger.info(
                "Attempting to send response to user",
                extra={
                    "sender": sender,
                    "response_length": len(response_text),
                    "response_preview": response_text[:100]
                    if len(response_text) > 100
                    else response_text,
                    "show_back_button": show_back_button,
                },
            )
            try:
                if show_back_button:
                    await whatsapp_service.send_message_with_back_button(
                        sender, response_text
                    )
                else:
                    await whatsapp_service.send_message(sender, response_text)
                logger.info(
                    "Response sent to user successfully",
                    extra={
                        "sender": sender,
                        "response_length": len(response_text),
                        "with_back_button": show_back_button,
                    },
                )
            except Exception as send_error:
                logger.error(
                    "Failed to send response to user",
                    extra={
                        "error": str(send_error),
                        "error_type": type(send_error).__name__,
                        "sender": sender,
                        "response_text": response_text,
                    },
                    exc_info=True,
                )
        else:
            logger.info(
                "No response text to send to user",
                extra={"sender": sender, "parsed_message": bool(parsed_message)},
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

        message_log_data = {
            "id": str(uuid4()),
            "invoice_id": None,  # No invoice context yet
            "channel": "WHATSAPP",
            "direction": "IN",
            "event": "webhook_received",
            "payload": {
                "event_type": event_type,
                "message_id": message_id,
                "timestamp": datetime.utcnow().isoformat(),
            },
        }

        message_log_response = (
            supabase.table("message_log").insert(message_log_data).execute()
        )
        message_log = message_log_response.data[0]

        logger.info(
            "Webhook payload logged to database",
            extra={"message_log_id": message_log["id"]},
        )

    except Exception as e:
        logger.error(
            "Failed to log webhook payload to database",
            extra={"error": str(e)},
            exc_info=True,
        )
        # Don't fail the webhook - WhatsApp expects 200 OK
        # Just log the error and continue

    return {"status": "received"}

