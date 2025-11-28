"""
WhatsApp Cloud API service for InvoiceIQ.

This module provides the WhatsAppService class for interacting with the WhatsApp
Cloud API, including message parsing, command recognition, state machine management,
and message sending functionality.
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import settings
from ..utils.logging import get_logger
from ..utils.phone import validate_msisdn, validate_phone_number

# Set up logger
logger = get_logger(__name__)


class ConversationStateManager:
    """
    Manages conversation states for users in the WhatsApp bot.

    Uses an in-memory dictionary to track user states and collected data
    during the guided invoice creation flow.
    """

    # Class variable for persistent state storage across instances
    states: Dict[str, Dict[str, Any]] = {}

    # State constants
    STATE_IDLE = "IDLE"
    STATE_COLLECT_MERCHANT_NAME = "COLLECT_MERCHANT_NAME"
    STATE_COLLECT_LINE_ITEMS = "COLLECT_LINE_ITEMS"
    STATE_COLLECT_VAT = "COLLECT_VAT"
    STATE_COLLECT_DUE_DATE = "COLLECT_DUE_DATE"
    STATE_COLLECT_PHONE = "COLLECT_PHONE"
    STATE_COLLECT_NAME = "COLLECT_NAME"
    STATE_COLLECT_AMOUNT = "COLLECT_AMOUNT"
    STATE_COLLECT_DESCRIPTION = "COLLECT_DESCRIPTION"
    STATE_COLLECT_MPESA_METHOD = "COLLECT_MPESA_METHOD"
    STATE_COLLECT_PAYBILL_DETAILS = "COLLECT_PAYBILL_DETAILS"
    STATE_COLLECT_PAYBILL_ACCOUNT = "COLLECT_PAYBILL_ACCOUNT"
    STATE_COLLECT_TILL_DETAILS = "COLLECT_TILL_DETAILS"
    STATE_COLLECT_PHONE_DETAILS = "COLLECT_PHONE_DETAILS"
    STATE_ASK_SAVE_PAYMENT_METHOD = "ASK_SAVE_PAYMENT_METHOD"
    STATE_READY = "READY"

    # State back navigation map - defines which state to return to when "Undo" is clicked
    # None means no previous state (either first step or dynamic handling required)
    STATE_BACK_MAP = {
        STATE_COLLECT_LINE_ITEMS: STATE_COLLECT_MERCHANT_NAME,
        STATE_COLLECT_VAT: STATE_COLLECT_LINE_ITEMS,
        STATE_COLLECT_DUE_DATE: STATE_COLLECT_VAT,
        STATE_COLLECT_PHONE: STATE_COLLECT_DUE_DATE,
        STATE_COLLECT_NAME: STATE_COLLECT_PHONE,
        STATE_COLLECT_MPESA_METHOD: STATE_COLLECT_NAME,
        STATE_COLLECT_PAYBILL_DETAILS: STATE_COLLECT_MPESA_METHOD,
        STATE_COLLECT_PAYBILL_ACCOUNT: STATE_COLLECT_PAYBILL_DETAILS,
        STATE_COLLECT_TILL_DETAILS: STATE_COLLECT_MPESA_METHOD,
        STATE_COLLECT_PHONE_DETAILS: STATE_COLLECT_MPESA_METHOD,
        STATE_ASK_SAVE_PAYMENT_METHOD: None,  # Dynamic - depends on payment method
    }

    @classmethod
    def get_state(cls, user_id: str) -> Dict[str, Any]:
        """
        Get the current state for a user.

        Args:
            user_id: The user's phone number (MSISDN)

        Returns:
            Dictionary with 'state' and 'data' keys
        """
        if user_id not in cls.states:
            cls.states[user_id] = {"state": cls.STATE_IDLE, "data": {}}
        return cls.states[user_id]

    @classmethod
    def set_state(
        cls,
        user_id: str,
        state: str,
        data: Optional[Dict[str, Any]] = None,
        trigger: Optional[str] = None,
    ) -> None:
        """
        Set the state for a user with transition logging.

        Args:
            user_id: The user's phone number (MSISDN)
            state: The new state
            data: Optional data to store with the state
            trigger: Optional event trigger that caused the state change
        """
        if data is None:
            data = {}

        # Get current state for transition logging
        current_state_info = cls.states.get(
            user_id, {"state": cls.STATE_IDLE, "data": {}}
        )
        from_state = current_state_info["state"]

        # Update state
        cls.states[user_id] = {"state": state, "data": data}

        # Log state transition (privacy-compliant - no PII)
        logger.info(
            "State transition",
            extra={
                "from_state": from_state,
                "to_state": state,
                "trigger": trigger or "manual",
                "data_keys": list(data.keys()),
            },
        )

    @classmethod
    def update_data(cls, user_id: str, key: str, value: Any) -> None:
        """
        Update a specific data field for a user's current state.

        Args:
            user_id: The user's phone number (MSISDN)
            key: The data key to update
            value: The value to store
        """
        state_info = cls.get_state(user_id)
        state_info["data"][key] = value
        logger.debug(
            "State data updated",
            extra={"user_id": user_id, "key": key, "value": value},
        )

    @classmethod
    def clear_state(cls, user_id: str) -> None:
        """
        Clear the state for a user (reset to IDLE).

        Args:
            user_id: The user's phone number (MSISDN)
        """
        cls.states[user_id] = {"state": cls.STATE_IDLE, "data": {}}
        logger.info("State cleared", extra={"user_id": user_id})


def get_user_friendly_error_message(error: Exception) -> str:
    """
    Convert technical error messages to user-friendly messages.

    Maps common exceptions to simple, actionable messages for users.
    Avoids exposing technical details to end users.

    Args:
        error: The exception that occurred

    Returns:
        A user-friendly error message string

    Examples:
        >>> get_user_friendly_error_message(ValueError("Invalid MSISDN"))
        "Invalid phone number. Please use format: 2547XXXXXXXX"

        >>> get_user_friendly_error_message(httpx.TimeoutException())
        "Service temporarily unavailable. Please try again in a moment."
    """
    error_type = type(error).__name__
    error_message = str(error).lower()

    # Network and timeout errors
    if error_type in ("TimeoutException", "ConnectTimeout", "ReadTimeout"):
        return "Service temporarily unavailable. Please try again in a moment."

    if error_type in ("RequestError", "ConnectError", "NetworkError"):
        return "Connection issue. Please check your internet and try again."

    # Validation errors
    if "invalid" in error_message and "phone" in error_message:
        return "Invalid phone number. Please use format: 2547XXXXXXXX"

    if "invalid" in error_message and "amount" in error_message:
        return "Invalid amount. Please enter a valid number (minimum 1 KES)."

    if "description" in error_message and (
        "short" in error_message or "long" in error_message
    ):
        return "Description must be between 3 and 120 characters."

    # M-PESA specific errors
    if "circuit breaker" in error_message.lower():
        return "Payment service is temporarily unavailable. Please try again later."

    if "stk push" in error_message.lower() or "mpesa" in error_message.lower():
        return "Payment initiation failed. Please try again or contact support."

    # WhatsApp API errors
    if "whatsapp" in error_message and "40" in error_message:
        return "Message delivery failed. Please check the phone number."

    if "rate limit" in error_message.lower() or "too many" in error_message.lower():
        return "Too many requests. Please wait a moment and try again."

    # Database errors
    if "database" in error_message or "connection" in error_message:
        return "System temporarily unavailable. Please try again shortly."

    # Generic fallback
    logger.warning(
        "Unmapped error type in user-friendly message helper",
        extra={"error_type": error_type, "error_message": error_message},
    )
    return "Something went wrong. Please try again or contact support if this persists."


class WhatsAppService:
    """
    Service for interacting with WhatsApp Cloud API.

    Provides methods for parsing incoming messages, recognizing commands,
    managing conversation state, and sending messages.
    """

    def __init__(self) -> None:
        """
        Initialize the WhatsApp service with 360 Dialog configuration.

        360 Dialog acts as a Business Solution Provider (BSP) for WhatsApp,
        providing a simplified API proxy. Key differences from direct WABA:
        - Uses API key authentication (D360-API-KEY header) instead of bearer tokens
        - Phone number mapping is managed internally by 360 Dialog
        - No phone_id needed in endpoint URLs
        """
        # 360 Dialog uses API key authentication instead of bearer tokens
        self.api_key = settings.d360_api_key
        # Phone ID not needed - 360 Dialog manages phone number mapping internally
        # based on the API key configuration in the Partner Portal
        self.base_url = settings.d360_webhook_base_url
        self.state_manager = ConversationStateManager

        logger.info(
            "WhatsAppService initialized",
            extra={
                "base_url": self.base_url,
                "provider": "360dialog",
            },
        )

    def parse_incoming_message(
        self, payload: Dict[str, Any]
    ) -> Optional[Dict[str, str]]:
        """
        Parse an incoming WhatsApp webhook payload to extract message details.

        Args:
            payload: The webhook payload from WhatsApp Cloud API

        Returns:
            Dictionary with 'text', 'from', and 'type' keys, or None if parsing fails
        """
        try:
            # Log the full payload structure for debugging
            logger.debug(
                "Parsing webhook payload",
                extra={
                    "payload_object": payload.get("object"),
                    "has_entry": bool(payload.get("entry")),
                    "payload_keys": list(payload.keys()) if payload else [],
                },
            )

            # Navigate the webhook structure: payload['entry'][0]['changes'][0]['value']['messages'][0]
            entry = payload.get("entry", [])
            if not entry:
                logger.debug(
                    "No entry field in webhook payload - likely a non-message event"
                )
                return None

            # Log entry details
            logger.debug(
                "Processing entry",
                extra={
                    "entry_count": len(entry),
                    "entry_id": entry[0].get("id") if entry else None,
                },
            )

            changes = entry[0].get("changes", [])
            if not changes:
                logger.debug(
                    "No changes field in webhook entry - likely a non-message event"
                )
                return None

            # Log changes details
            change = changes[0] if changes else {}
            field = change.get("field")
            logger.debug(
                "Processing change",
                extra={"field": field, "changes_count": len(changes)},
            )

            # Check if this is a message event
            if field and field not in ["messages"]:
                logger.debug(
                    "Webhook is not a message event",
                    extra={"field": field, "expected": "messages"},
                )
                return None

            value = change.get("value", {})

            # Log value structure
            logger.debug(
                "Processing value",
                extra={
                    "value_keys": list(value.keys()),
                    "has_messages": "messages" in value,
                    "has_statuses": "statuses" in value,
                    "messaging_product": value.get("messaging_product"),
                },
            )

            # Handle status updates (delivery/read receipts) - these don't have messages
            if "statuses" in value and "messages" not in value:
                logger.debug("Webhook contains status update, not a message")
                return None

            messages = value.get("messages", [])
            if not messages:
                logger.debug(
                    "No messages in webhook payload - might be a status update or other event",
                    extra={"value_keys": list(value.keys())},
                )
                return None

            message = messages[0]
            message_type = message.get("type")
            sender = message.get("from")

            logger.debug(
                "Processing message",
                extra={
                    "message_type": message_type,
                    "sender": sender,
                    "message_id": message.get("id"),
                    "message_keys": list(message.keys()),
                },
            )

            if not sender:
                logger.warning(
                    "No sender in message", extra={"message_keys": list(message.keys())}
                )
                return None

            # Normalize and validate phone number with more flexibility
            normalized_sender = sender

            # Try to normalize the phone number if it's not in the expected format
            if not sender.startswith("254"):
                # If it starts with +, remove it
                if sender.startswith("+"):
                    normalized_sender = sender[1:]
                # If it's a local format (0XXXXXXXXX), convert to international
                elif sender.startswith("0") and len(sender) >= 10:
                    normalized_sender = "254" + sender[1:]

            # For testing/development, accept any phone number that looks valid
            # In production, you may want stricter validation
            if not normalized_sender.startswith("254") or len(normalized_sender) < 12:
                logger.warning(
                    "Phone number doesn't match expected Kenyan format, but proceeding anyway",
                    extra={
                        "original": sender,
                        "normalized": normalized_sender,
                        "expected_format": "254XXXXXXXXX",
                    },
                )
                # For now, use the original sender to avoid breaking existing flows
                normalized_sender = sender

            # Log validation attempt
            try:
                from ..utils.phone import validate_msisdn

                validate_msisdn(normalized_sender)
                logger.debug(
                    f"Phone number validated successfully: {normalized_sender}"
                )
            except ValueError as e:
                # Log the validation error but don't fail - let the message through
                logger.warning(
                    "Phone number validation failed, but continuing to process message",
                    extra={
                        "sender": sender,
                        "normalized": normalized_sender,
                        "error": str(e),
                    },
                )
                # Use original sender to maintain compatibility
                normalized_sender = sender

            # Extract text based on message type
            text = None
            if message_type == "text":
                text_obj = message.get("text", {})
                text = text_obj.get("body")
                logger.debug(
                    f"Extracted text message: {text[:50] if text else 'None'}..."
                )
            elif message_type == "interactive":
                # Handle button clicks
                interactive = message.get("interactive", {})
                interactive_type = interactive.get("type")

                if interactive_type == "button_reply":
                    button_reply = interactive.get("button_reply", {})
                    button_id = button_reply.get("id")

                    # Check if it's the undo button
                    if button_id == "undo":
                        text = "undo"  # Special command
                        logger.info(
                            "Undo button clicked",
                            extra={"sender": normalized_sender}
                        )
                    else:
                        text = button_id or button_reply.get("title")
                    logger.debug(f"Extracted button reply: {text}")
                elif interactive_type == "list_reply":
                    list_reply = interactive.get("list_reply", {})
                    text = list_reply.get("id") or list_reply.get("title")
                    logger.debug(f"Extracted list reply: {text}")
                else:
                    logger.debug(f"Unknown interactive type: {interactive_type}")

            elif message_type == "button":
                # Handle quick reply buttons (different from interactive buttons)
                button = message.get("button", {})
                text = button.get("payload") or button.get("text")
                logger.debug(f"Extracted button text: {text}")
            else:
                logger.info(
                    "Message type not supported for text extraction",
                    extra={
                        "message_type": message_type,
                        "supported_types": ["text", "interactive", "button"],
                    },
                )
                return None

            if not text:
                logger.warning(
                    "No text content extracted from message",
                    extra={
                        "message_type": message_type,
                        "message_keys": list(message.keys()),
                    },
                )
                return None

            result = {
                "text": text.strip(),
                "from": normalized_sender,
                "type": message_type,
            }
            logger.info(
                "Message parsed successfully",
                extra={
                    "sender": normalized_sender,
                    "type": message_type,
                    "text_length": len(text),
                    "text_preview": text[:50] if len(text) > 50 else text,
                },
            )
            return result

        except (KeyError, IndexError, TypeError) as e:
            logger.error(
                "Exception while parsing webhook payload",
                extra={
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "payload_keys": list(payload.keys()) if payload else None,
                },
                exc_info=True,
            )
            return None
        except Exception as e:
            logger.error(
                "Unexpected error parsing webhook payload",
                extra={"error": str(e), "error_type": type(e).__name__},
                exc_info=True,
            )
            return None

    def parse_command(self, message_text: str) -> Dict[str, Any]:
        """
        Parse a message text to recognize commands and extract parameters.

        Supported commands:
        - invoice <phone_or_name> <amount> <desc...>: One-line invoice creation
        - remind <invoice_id>: Send reminder
        - cancel <invoice_id>: Cancel invoice
        - help: Show help
        - invoice / new invoice: Start guided flow

        Args:
            message_text: The message text to parse

        Returns:
            Dictionary with 'command' and 'params' keys
        """
        text = message_text.strip().lower()

        # Help command
        if text == "help":
            return {"command": "help", "params": {}}

        # Start guided flow
        if text == "invoice" or text == "new invoice":
            return {"command": "start_guided", "params": {}}

        # Remind command: remind <invoice_id>
        remind_pattern = r"^remind\s+(.+)$"
        match = re.match(remind_pattern, text)
        if match:
            return {
                "command": "remind",
                "params": {"invoice_id": match.group(1).strip()},
            }

        # Cancel command: cancel <invoice_id>
        cancel_pattern = r"^cancel\s+(.+)$"
        match = re.match(cancel_pattern, text)
        if match:
            return {
                "command": "cancel",
                "params": {"invoice_id": match.group(1).strip()},
            }

        # One-line invoice command: invoice <phone_or_name> <amount> <desc...>
        # This regex matches: invoice followed by either a phone number or name, then amount, then description
        invoice_pattern = r"^invoice\s+(\S+(?:\s+\S+)*?)\s+(\d+)\s+(.{3,})$"
        match = re.match(invoice_pattern, message_text.strip(), re.IGNORECASE)
        if match:
            phone_or_name = match.group(1).strip()
            amount_str = match.group(2).strip()
            description = match.group(3).strip()

            # Validate amount is numeric and positive
            try:
                amount = int(amount_str)
                if amount < 1:
                    return {
                        "command": "invoice",
                        "params": {"error": "Amount must be at least 1 KES"},
                    }
            except ValueError:
                return {
                    "command": "invoice",
                    "params": {"error": "Amount must be a number"},
                }

            # Validate description length
            if len(description) < 3:
                return {
                    "command": "invoice",
                    "params": {"error": "Description must be at least 3 characters"},
                }
            if len(description) > 120:
                return {
                    "command": "invoice",
                    "params": {"error": "Description must not exceed 120 characters"},
                }

            # Check if it's a phone number (starts with 254 and is numeric)
            if re.match(r"^2547\d{8}$", phone_or_name):
                # It's a phone number
                return {
                    "command": "invoice",
                    "params": {
                        "phone": phone_or_name,
                        "amount": amount,
                        "description": description,
                    },
                }
            else:
                # It's a name - return error for MVP (name lookup not implemented)
                return {
                    "command": "invoice",
                    "params": {
                        "error": "For quick invoice, please use phone number format: invoice 2547XXXXXXXX <amount> <description>",
                    },
                }

        # Unknown command
        return {"command": "unknown", "params": {}}

    def handle_guided_flow(self, user_id: str, message_text: str) -> Dict[str, Any]:
        """
        Handle the guided invoice creation flow based on current state.

        Args:
            user_id: The user's phone number (MSISDN)
            message_text: The message text from the user

        Returns:
            Dictionary with 'response' (message to send) and optional 'action' keys
        """
        from ..db import get_supabase
        from ..utils.invoice_parser import (
            parse_due_date,
            parse_line_items,
        )

        state_info = self.state_manager.get_state(user_id)
        current_state = state_info["state"]
        data = state_info["data"]
        text = message_text.strip()

        # Handle cancel at any state
        if text.lower() == "cancel":
            self.state_manager.clear_state(user_id)
            return {
                "response": "Invoice cancelled. Send 'invoice' to start again.",
                "action": "cancelled",
            }

        # STATE: IDLE - Start guided flow (now starts with merchant name)
        if current_state == self.state_manager.STATE_IDLE:
            self.state_manager.set_state(
                user_id, self.state_manager.STATE_COLLECT_MERCHANT_NAME
            )
            return {
                "response": "Let's create an invoice!\n\nFirst, what is your business/merchant name? (2-100 characters)",
                "action": "started",
            }

        # STATE: COLLECT_MERCHANT_NAME - Validate and store merchant name
        elif current_state == self.state_manager.STATE_COLLECT_MERCHANT_NAME:
            if len(text) < 2 or len(text) > 100:
                return {
                    "response": "Merchant name must be between 2 and 100 characters. Please try again:",
                    "action": "validation_error",
                }

            self.state_manager.update_data(user_id, "merchant_name", text)
            self.state_manager.set_state(
                user_id, self.state_manager.STATE_COLLECT_LINE_ITEMS, data
            )
            return {
                "response": (
                    "Please enter your line items in the following format:\n\n"
                    "Item - Unit Price - Quantity\n\n"
                    "Example:\n"
                    "Full Home Deep Clean - 1500 - 3\n"
                    "Kitchen Deep Clean - 800 - 1\n"
                    "Bathroom Scrub - 600 - 1\n\n"
                    "Send all items in one message."
                ),
                "action": "merchant_name_collected",
            }

        # STATE: COLLECT_LINE_ITEMS - Parse and store line items
        elif current_state == self.state_manager.STATE_COLLECT_LINE_ITEMS:
            try:
                line_items = parse_line_items(text)
                self.state_manager.update_data(user_id, "line_items", line_items)
                self.state_manager.set_state(
                    user_id, self.state_manager.STATE_COLLECT_VAT, data
                )
                return {
                    "response": (
                        "Would you like to include VAT on this invoice?\n\n"
                        "Reply with:\n"
                        "1 – Yes, add VAT (16%)\n"
                        "2 – No, no VAT"
                    ),
                    "action": "line_items_collected",
                    "show_back_button": True,
                }
            except ValueError as e:
                return {
                    "response": f"Error parsing line items: {str(e)}\n\nPlease try again following the format:\nItem - Price - Quantity",
                    "action": "validation_error",
                }

        # STATE: COLLECT_VAT - Parse VAT choice
        elif current_state == self.state_manager.STATE_COLLECT_VAT:
            # Accept both "1"/"2" and "yes"/"no"
            text_lower = text.lower()
            if text in ["1", "2"] or text_lower in ["yes", "no"]:
                include_vat = text == "1" or text_lower == "yes"
                self.state_manager.update_data(user_id, "include_vat", include_vat)
                self.state_manager.set_state(
                    user_id, self.state_manager.STATE_COLLECT_DUE_DATE, data
                )
                return {
                    "response": (
                        "When is this invoice due?\n\n"
                        "Reply with one of:\n"
                        "0 = Due on receipt\n"
                        "7 = In 7 days\n"
                        "14 = In 14 days\n"
                        "30 = In 30 days\n"
                        "N = In N days (where N is a number)\n\n"
                        "Or send a date like: 30/11 or 30/11/2025."
                    ),
                    "action": "vat_collected",
                    "show_back_button": True,
                }
            else:
                return {
                    "response": 'Please reply with "1" or "yes" for VAT, or "2" or "no" for no VAT.',
                    "action": "validation_error",
                }

        # STATE: COLLECT_DUE_DATE - Parse and store due date
        elif current_state == self.state_manager.STATE_COLLECT_DUE_DATE:
            try:
                due_date_formatted = parse_due_date(text)
                self.state_manager.update_data(user_id, "due_date", due_date_formatted)
                self.state_manager.set_state(
                    user_id, self.state_manager.STATE_COLLECT_PHONE, data
                )
                return {
                    "response": "Great! Now, please send the customer's phone number with country code (e.g., 254712345678 for Kenya, 447123456789 for UK):",
                    "action": "due_date_collected",
                    "show_back_button": True,
                }
            except ValueError as e:
                return {
                    "response": f"Invalid due date: {str(e)}\n\nPlease try again.",
                    "action": "validation_error",
                }

        # STATE: COLLECT_PHONE - Validate and store phone (supports international numbers)
        elif current_state == self.state_manager.STATE_COLLECT_PHONE:
            try:
                validated_phone = validate_phone_number(text)  # Supports any country
                self.state_manager.update_data(user_id, "phone", validated_phone)
                self.state_manager.set_state(
                    user_id, self.state_manager.STATE_COLLECT_NAME, data
                )
                return {
                    "response": "Perfect! What is the customer's name? (or send '-' to skip)",
                    "action": "phone_collected",
                    "show_back_button": True,
                }
            except ValueError as e:
                return {
                    "response": f"Invalid phone number. Please try again with country code (e.g., 254712345678 or +254712345678):\n{str(e)}",
                    "action": "validation_error",
                }

        # STATE: COLLECT_NAME - Store name (or skip) - OPTIONAL
        elif current_state == self.state_manager.STATE_COLLECT_NAME:
            if text == "-":
                self.state_manager.update_data(user_id, "name", None)
            else:
                # Validate name length
                if len(text) < 2 or len(text) > 60:
                    return {
                        "response": "Name must be between 2 and 60 characters. Please try again (or send '-' to skip):",
                        "action": "validation_error",
                    }
                self.state_manager.update_data(user_id, "name", text)

            self.state_manager.set_state(
                user_id, self.state_manager.STATE_COLLECT_MPESA_METHOD, data
            )
            return {
                "response": (
                    "How would you like to receive the payment via M-PESA?\n"
                    "Reply with:\n\n"
                    "1 – Paybill\n"
                    "2 – Till Number\n"
                    "3 – Phone Number (Send Money)"
                ),
                "action": "name_collected",
                "show_back_button": True,
            }

        # STATE: COLLECT_MPESA_METHOD - Choose payment method
        elif current_state == self.state_manager.STATE_COLLECT_MPESA_METHOD:
            if text not in ["1", "2", "3"]:
                return {
                    "response": "Please reply with 1 (Paybill), 2 (Till), or 3 (Phone Number).",
                    "action": "validation_error",
                }

            method_map = {"1": "PAYBILL", "2": "TILL", "3": "PHONE"}
            mpesa_method = method_map[text]
            self.state_manager.update_data(user_id, "mpesa_method", mpesa_method)

            # Get merchant MSISDN (user_id) to query saved payment methods
            supabase = get_supabase()

            if mpesa_method == "PAYBILL":
                # Query saved paybill methods
                saved_response = (
                    supabase.table("merchant_payment_methods")
                    .select("*")
                    .eq("merchant_msisdn", user_id)
                    .eq("method_type", "PAYBILL")
                    .execute()
                )
                saved_methods = saved_response.data if saved_response.data else []
                self.state_manager.update_data(
                    user_id, "saved_paybill_methods", saved_methods
                )

                if saved_methods:
                    # Show saved methods
                    methods_list = "\n".join(
                        [
                            f"{idx + 1} - Paybill Number: {m['paybill_number']}; Account Number: {m['account_number']}"
                            for idx, m in enumerate(saved_methods)
                        ]
                    )
                    response_msg = (
                        f"Select the paybill you want to use:\n\n"
                        f"{methods_list}\n\n"
                        f"Or, please enter the paybill number you want to use:"
                    )
                else:
                    response_msg = "Please enter your paybill number:"

                self.state_manager.set_state(
                    user_id, self.state_manager.STATE_COLLECT_PAYBILL_DETAILS, data
                )
                return {
                    "response": response_msg,
                    "action": "mpesa_method_selected",
                    "show_back_button": True,
                }

            elif mpesa_method == "TILL":
                # Query saved till methods
                saved_response = (
                    supabase.table("merchant_payment_methods")
                    .select("*")
                    .eq("merchant_msisdn", user_id)
                    .eq("method_type", "TILL")
                    .execute()
                )
                saved_methods = saved_response.data if saved_response.data else []
                self.state_manager.update_data(
                    user_id, "saved_till_methods", saved_methods
                )

                if saved_methods:
                    # Show saved methods
                    methods_list = "\n".join(
                        [
                            f"{idx + 1} - Till Number: {m['till_number']}"
                            for idx, m in enumerate(saved_methods)
                        ]
                    )
                    response_msg = (
                        f"Select the till you want to use:\n\n"
                        f"{methods_list}\n\n"
                        f"Or, please enter the till number you want to use:"
                    )
                else:
                    response_msg = "Please enter your till number:"

                self.state_manager.set_state(
                    user_id, self.state_manager.STATE_COLLECT_TILL_DETAILS, data
                )
                return {
                    "response": response_msg,
                    "action": "mpesa_method_selected",
                    "show_back_button": True,
                }

            elif mpesa_method == "PHONE":
                # Query saved phone methods
                saved_response = (
                    supabase.table("merchant_payment_methods")
                    .select("*")
                    .eq("merchant_msisdn", user_id)
                    .eq("method_type", "PHONE")
                    .execute()
                )
                saved_methods = saved_response.data if saved_response.data else []
                self.state_manager.update_data(
                    user_id, "saved_phone_methods", saved_methods
                )

                if saved_methods:
                    # Show saved methods
                    methods_list = "\n".join(
                        [
                            f"{idx + 1} - Phone Number: {m['phone_number']}"
                            for idx, m in enumerate(saved_methods)
                        ]
                    )
                    response_msg = (
                        f"Select the phone number you want to use:\n\n"
                        f"{methods_list}\n\n"
                        f"Or, please enter the phone number you want to use (format: 2547XXXXXXXX):"
                    )
                else:
                    response_msg = (
                        "Please enter your phone number (format: 2547XXXXXXXX):"
                    )

                self.state_manager.set_state(
                    user_id, self.state_manager.STATE_COLLECT_PHONE_DETAILS, data
                )
                return {
                    "response": response_msg,
                    "action": "mpesa_method_selected",
                    "show_back_button": True,
                }

        # STATE: COLLECT_PAYBILL_DETAILS - Handle paybill selection or new entry
        elif current_state == self.state_manager.STATE_COLLECT_PAYBILL_DETAILS:
            saved_methods = data.get("saved_paybill_methods", [])

            # If no saved methods, treat input directly as new paybill number
            if len(saved_methods) == 0:
                # Validate paybill number (5-7 digits)
                if not re.match(r"^\d{5,7}$", text):
                    return {
                        "response": "Invalid paybill number. Must be 5-7 digits. Please try again:",
                        "action": "validation_error",
                    }

                self.state_manager.update_data(user_id, "mpesa_paybill_number", text)
                self.state_manager.update_data(user_id, "used_saved_method", False)
                self.state_manager.set_state(
                    user_id, self.state_manager.STATE_COLLECT_PAYBILL_ACCOUNT, data
                )
                return {
                    "response": "Enter the account number the customer should use:",
                    "action": "paybill_number_collected",
                    "show_back_button": True,
                }

            # Saved methods exist - try to parse as selection number
            try:
                selection_num = int(text)
                if 1 <= selection_num <= len(saved_methods):
                    # User selected a saved method
                    selected_method = saved_methods[selection_num - 1]
                    self.state_manager.update_data(
                        user_id,
                        "mpesa_paybill_number",
                        selected_method["paybill_number"],
                    )
                    self.state_manager.update_data(
                        user_id,
                        "mpesa_account_number",
                        selected_method["account_number"],
                    )
                    self.state_manager.update_data(user_id, "used_saved_method", True)
                    self.state_manager.set_state(
                        user_id, self.state_manager.STATE_READY, data
                    )

                    # Show preview
                    return self._generate_invoice_preview(data)
                else:
                    # Number is greater than saved methods - treat as new paybill number
                    # Validate paybill number (5-7 digits)
                    if not re.match(r"^\d{5,7}$", text):
                        return {
                            "response": "Invalid paybill number. Must be 5-7 digits. Please try again:",
                            "action": "validation_error",
                        }

                    self.state_manager.update_data(
                        user_id, "mpesa_paybill_number", text
                    )
                    self.state_manager.update_data(user_id, "used_saved_method", False)
                    self.state_manager.set_state(
                        user_id, self.state_manager.STATE_COLLECT_PAYBILL_ACCOUNT, data
                    )
                    return {
                        "response": "Enter the account number the customer should use:",
                        "action": "paybill_number_collected",
                        "show_back_button": True,
                    }
            except ValueError:
                # Not a number - treat as new paybill number
                # Validate paybill number (5-7 digits)
                if not re.match(r"^\d{5,7}$", text):
                    return {
                        "response": "Invalid paybill number. Must be 5-7 digits. Please try again:",
                        "action": "validation_error",
                    }

                self.state_manager.update_data(user_id, "mpesa_paybill_number", text)
                self.state_manager.update_data(user_id, "used_saved_method", False)
                self.state_manager.set_state(
                    user_id, self.state_manager.STATE_COLLECT_PAYBILL_ACCOUNT, data
                )
                return {
                    "response": "Enter the account number the customer should use:",
                    "action": "paybill_number_collected",
                    "show_back_button": True,
                }

        # STATE: COLLECT_PAYBILL_ACCOUNT - Collect account number for paybill
        elif current_state == self.state_manager.STATE_COLLECT_PAYBILL_ACCOUNT:
            # Validate account number (1-20 alphanumeric characters)
            if not re.match(r"^[a-zA-Z0-9\-]{1,20}$", text):
                return {
                    "response": "Invalid account number. Must be 1-20 alphanumeric characters. Please try again:",
                    "action": "validation_error",
                }

            self.state_manager.update_data(user_id, "mpesa_account_number", text)
            self.state_manager.set_state(
                user_id, self.state_manager.STATE_ASK_SAVE_PAYMENT_METHOD, data
            )
            return {
                "response": "Would you like to save this paybill for future invoices?\n\nReply 'yes' or 'no':",
                "action": "account_number_collected",
                "show_back_button": True,
            }

        # STATE: COLLECT_TILL_DETAILS - Handle till selection or new entry
        elif current_state == self.state_manager.STATE_COLLECT_TILL_DETAILS:
            saved_methods = data.get("saved_till_methods", [])

            # If no saved methods, treat input directly as new till number
            if len(saved_methods) == 0:
                # Validate till number (5-7 digits)
                if not re.match(r"^\d{5,7}$", text):
                    return {
                        "response": "Invalid till number. Must be 5-7 digits. Please try again:",
                        "action": "validation_error",
                    }

                self.state_manager.update_data(user_id, "mpesa_till_number", text)
                self.state_manager.update_data(user_id, "used_saved_method", False)
                self.state_manager.set_state(
                    user_id, self.state_manager.STATE_ASK_SAVE_PAYMENT_METHOD, data
                )
                return {
                    "response": "Would you like to save this till number for future invoices?\n\nReply 'yes' or 'no':",
                    "action": "till_number_collected",
                    "show_back_button": True,
                }

            # Saved methods exist - try to parse as selection number
            try:
                selection_num = int(text)
                if 1 <= selection_num <= len(saved_methods):
                    # User selected a saved method
                    selected_method = saved_methods[selection_num - 1]
                    self.state_manager.update_data(
                        user_id, "mpesa_till_number", selected_method["till_number"]
                    )
                    self.state_manager.update_data(user_id, "used_saved_method", True)
                    self.state_manager.set_state(
                        user_id, self.state_manager.STATE_READY, data
                    )

                    # Show preview
                    return self._generate_invoice_preview(data)
                else:
                    # Number is greater than saved methods - treat as new till number
                    # Validate till number (5-7 digits)
                    if not re.match(r"^\d{5,7}$", text):
                        return {
                            "response": "Invalid till number. Must be 5-7 digits. Please try again:",
                            "action": "validation_error",
                        }

                    self.state_manager.update_data(user_id, "mpesa_till_number", text)
                    self.state_manager.update_data(user_id, "used_saved_method", False)
                    self.state_manager.set_state(
                        user_id, self.state_manager.STATE_ASK_SAVE_PAYMENT_METHOD, data
                    )
                    return {
                        "response": "Would you like to save this till number for future invoices?\n\nReply 'yes' or 'no':",
                        "action": "till_number_collected",
                        "show_back_button": True,
                    }
            except ValueError:
                # Not a number - treat as new till number
                # Validate till number (5-7 digits)
                if not re.match(r"^\d{5,7}$", text):
                    return {
                        "response": "Invalid till number. Must be 5-7 digits. Please try again:",
                        "action": "validation_error",
                    }

                self.state_manager.update_data(user_id, "mpesa_till_number", text)
                self.state_manager.update_data(user_id, "used_saved_method", False)
                self.state_manager.set_state(
                    user_id, self.state_manager.STATE_ASK_SAVE_PAYMENT_METHOD, data
                )
                return {
                    "response": "Would you like to save this till number for future invoices?\n\nReply 'yes' or 'no':",
                    "action": "till_number_collected",
                    "show_back_button": True,
                }

        # STATE: COLLECT_PHONE_DETAILS - Handle phone selection or new entry
        elif current_state == self.state_manager.STATE_COLLECT_PHONE_DETAILS:
            saved_methods = data.get("saved_phone_methods", [])

            # If no saved methods, treat input directly as new phone number
            if len(saved_methods) == 0:
                # Validate phone number
                try:
                    validated_phone = validate_msisdn(text)
                    self.state_manager.update_data(
                        user_id, "mpesa_phone_number", validated_phone
                    )
                    self.state_manager.update_data(user_id, "used_saved_method", False)
                    self.state_manager.set_state(
                        user_id, self.state_manager.STATE_ASK_SAVE_PAYMENT_METHOD, data
                    )
                    return {
                        "response": "Would you like to save this phone number for future invoices?\n\nReply 'yes' or 'no':",
                        "action": "phone_number_collected",
                        "show_back_button": True,
                    }
                except ValueError:
                    return {
                        "response": "Invalid phone number. Please use format 2547XXXXXXXX:",
                        "action": "validation_error",
                    }

            # Saved methods exist - try to parse as selection number
            try:
                selection_num = int(text)
                if 1 <= selection_num <= len(saved_methods):
                    # User selected a saved method
                    selected_method = saved_methods[selection_num - 1]
                    self.state_manager.update_data(
                        user_id, "mpesa_phone_number", selected_method["phone_number"]
                    )
                    self.state_manager.update_data(user_id, "used_saved_method", True)
                    self.state_manager.set_state(
                        user_id, self.state_manager.STATE_READY, data
                    )

                    # Show preview
                    return self._generate_invoice_preview(data)
                else:
                    # Number is greater than saved methods - treat as new phone number
                    # Validate phone number
                    try:
                        validated_phone = validate_msisdn(text)
                        self.state_manager.update_data(
                            user_id, "mpesa_phone_number", validated_phone
                        )
                        self.state_manager.update_data(
                            user_id, "used_saved_method", False
                        )
                        self.state_manager.set_state(
                            user_id,
                            self.state_manager.STATE_ASK_SAVE_PAYMENT_METHOD,
                            data,
                        )
                        return {
                            "response": "Would you like to save this phone number for future invoices?\n\nReply 'yes' or 'no':",
                            "action": "phone_number_collected",
                            "show_back_button": True,
                        }
                    except ValueError:
                        return {
                            "response": "Invalid phone number. Please use format 2547XXXXXXXX:",
                            "action": "validation_error",
                        }
            except ValueError:
                # Not a number - treat as new phone number
                # Validate phone number
                try:
                    validated_phone = validate_msisdn(text)
                    self.state_manager.update_data(
                        user_id, "mpesa_phone_number", validated_phone
                    )
                    self.state_manager.update_data(user_id, "used_saved_method", False)
                    self.state_manager.set_state(
                        user_id, self.state_manager.STATE_ASK_SAVE_PAYMENT_METHOD, data
                    )
                    return {
                        "response": "Would you like to save this phone number for future invoices?\n\nReply 'yes' or 'no':",
                        "action": "phone_number_collected",
                        "show_back_button": True,
                    }
                except ValueError:
                    return {
                        "response": "Invalid phone number. Please use format 2547XXXXXXXX:",
                        "action": "validation_error",
                    }

        # STATE: ASK_SAVE_PAYMENT_METHOD - Ask if merchant wants to save (only for NEW details)
        elif current_state == self.state_manager.STATE_ASK_SAVE_PAYMENT_METHOD:
            text_lower = text.lower()
            if text_lower in ["yes", "no", "y", "n"]:
                save_method = text_lower in ["yes", "y"]
                self.state_manager.update_data(
                    user_id, "save_payment_method", save_method
                )
                self.state_manager.set_state(
                    user_id, self.state_manager.STATE_READY, data
                )

                # Show preview - add back button flag
                preview_result = self._generate_invoice_preview(data)
                preview_result["show_back_button"] = True
                return preview_result
            else:
                return {
                    "response": "Please reply 'yes' or 'no':",
                    "action": "validation_error",
                }

        # STATE: READY - Wait for confirmation
        elif current_state == self.state_manager.STATE_READY:
            if text.lower() == "confirm":
                # Clear state and return data for invoice creation
                # The webhook handler will create the invoice
                self.state_manager.clear_state(user_id)
                return {
                    "response": None,  # Will be set after invoice creation
                    "action": "confirmed",
                    "invoice_data": data,
                }
            elif text.lower() == "cancel":
                self.state_manager.clear_state(user_id)
                return {
                    "response": "Invoice cancelled. Send 'invoice' to start again.",
                    "action": "cancelled",
                }
            else:
                return {
                    "response": "Please send 'confirm' to create the invoice or 'cancel' to start over.",
                    "action": "awaiting_confirmation",
                }

        # Unknown state (shouldn't happen)
        logger.error(
            "Unknown state", extra={"state": current_state, "user_id": user_id}
        )
        self.state_manager.clear_state(user_id)
        return {
            "response": "An error occurred. Please start again by sending 'invoice'.",
            "action": "error",
        }

    def _generate_invoice_preview(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate invoice preview for confirmation.

        Args:
            data: Invoice data collected so far

        Returns:
            Dictionary with response and action
        """
        from ..utils.invoice_parser import (
            calculate_invoice_totals,
            format_line_items_preview,
        )

        # Extract data
        merchant_name = data.get("merchant_name")
        line_items = data.get("line_items", [])
        include_vat = data.get("include_vat", False)
        due_date = data.get("due_date")
        customer_phone = data.get("phone")
        customer_name = data.get("name") or "Not provided"
        mpesa_method = data.get("mpesa_method")

        # Calculate totals
        totals = calculate_invoice_totals(line_items, include_vat)
        subtotal_kes = totals["subtotal_cents"] / 100
        vat_kes = totals["vat_cents"] / 100
        total_kes = totals["total_cents"] / 100

        # Format line items
        line_items_formatted = format_line_items_preview(line_items)

        # Format M-PESA details
        mpesa_details = ""
        if mpesa_method == "PAYBILL":
            paybill_num = data.get("mpesa_paybill_number")
            account_num = data.get("mpesa_account_number")
            mpesa_details = f"Paybill: {paybill_num}\nAccount: {account_num}"
        elif mpesa_method == "TILL":
            till_num = data.get("mpesa_till_number")
            mpesa_details = f"Till Number: {till_num}"
        elif mpesa_method == "PHONE":
            phone_num = data.get("mpesa_phone_number")
            mpesa_details = f"Phone Number: {phone_num}"

        # Build preview
        preview_lines = [
            "Ready to send!\n",
            f"Invoice From: {merchant_name}",
            "\nLine Items:",
            line_items_formatted,
            f"\nSubtotal: KES {subtotal_kes:,.2f}",
        ]

        if include_vat:
            preview_lines.append(f"VAT (16%): KES {vat_kes:,.2f}")

        preview_lines.extend(
            [
                f"Total: KES {total_kes:,.2f}",
                f"\nInvoice Due: {due_date}",
                f"\nCustomer: {customer_name}",
                f"Phone: {customer_phone}",
                "\nM-PESA Payment Details:",
                mpesa_details,
                "\nSend 'confirm' to proceed or 'cancel' to start over.",
            ]
        )

        preview = "\n".join(preview_lines)

        return {"response": preview, "action": "ready"}

    @retry(
        retry=retry_if_exception_type((httpx.RequestError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        before_sleep=before_sleep_log(logger, logging.INFO),
        reraise=True,
    )
    async def send_message(self, to: str, message: str) -> Optional[Dict[str, Any]]:
        """
        Send a text message to a WhatsApp user with retry logic.

        Retries on network errors with exponential backoff:
        - 3 attempts total
        - Wait times: 1s, 2s, 4s
        - Retries only on network/timeout errors, not API errors

        Args:
            to: Recipient's phone number (MSISDN)
            message: Text message to send

        Returns:
            Response data from WhatsApp API, or None on failure

        Raises:
            Exception: If all retry attempts fail or API returns error
        """
        # 360 Dialog endpoint - no phone_id in URL
        url = f"{self.base_url}/messages"
        headers = {
            "D360-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": message},
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = await response.json()

                logger.info(
                    "Message sent successfully",
                    extra={
                        "to": to,
                        "message_length": len(message),
                        "message_id": data.get("messages", [{}])[0].get("id"),
                    },
                )
                return data

        except httpx.HTTPStatusError as e:
            logger.error(
                "WhatsApp API returned error status",
                extra={
                    "status_code": e.response.status_code,
                    "response": e.response.text,
                    "to": to,
                },
                exc_info=True,
            )
            raise Exception(
                f"WhatsApp API error: {e.response.status_code} - {e.response.text}"
            )

        except httpx.RequestError as e:
            logger.error(
                "Failed to send WhatsApp message",
                extra={"error": str(e), "to": to},
                exc_info=True,
            )
            raise Exception(f"Failed to send message: {str(e)}")

        except Exception as e:
            logger.error(
                "Unexpected error sending WhatsApp message",
                extra={"error": str(e), "to": to},
                exc_info=True,
            )
            raise

    async def send_message_with_back_button(
        self,
        recipient: str,
        message_text: str
    ) -> bool:
        """
        Send a WhatsApp interactive message with an Undo button.

        Uses 360Dialog API to send an interactive button message that allows
        merchants to go back one step in the invoice creation flow.

        Args:
            recipient: Phone number to send to (E.164 format without +)
            message_text: The prompt text to display

        Returns:
            True if sent successfully, False otherwise

        Example:
            >>> await service.send_message_with_back_button(
            ...     "254712345678",
            ...     "Please enter the customer's phone number:"
            ... )
        """
        url = f"{self.base_url}/messages"
        headers = {
            "D360-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }

        payload = {
            "to": recipient,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {
                    "text": message_text
                },
                "action": {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {
                                "id": "undo",
                                "title": "Undo"
                            }
                        }
                    ]
                }
            }
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()

                logger.info(
                    "Interactive message with back button sent successfully",
                    extra={
                        "recipient": recipient,
                        "message_length": len(message_text)
                    }
                )
                return True

        except httpx.HTTPStatusError as e:
            logger.error(
                "WhatsApp API returned error status",
                extra={
                    "status_code": e.response.status_code,
                    "response": e.response.text,
                    "recipient": recipient
                },
                exc_info=True
            )
            return False

        except httpx.RequestError as e:
            logger.error(
                "Failed to send WhatsApp interactive message",
                extra={"error": str(e), "recipient": recipient},
                exc_info=True
            )
            return False

        except Exception as e:
            logger.error(
                "Unexpected error sending WhatsApp interactive message",
                extra={"error": str(e), "recipient": recipient},
                exc_info=True
            )
            return False

    async def send_invoice_to_customer(
        self,
        invoice_id: str,
        customer_msisdn: str,
        customer_name: Optional[str],
        amount_cents: int,
        db_session: Any,
        invoice: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Send an invoice to a customer via WhatsApp.

        Uses WhatsApp template for new guided flow invoices, or interactive button for legacy one-line invoices.
        Creates a MessageLog entry for the outbound message.

        Args:
            invoice_id: The invoice ID
            customer_msisdn: Customer's phone number (MSISDN)
            customer_name: Customer's name (optional)
            amount_cents: Invoice amount in cents
            db_session: Database session for logging
            invoice: Full invoice object (new flow with all fields)

        Returns:
            True if message sent successfully, False otherwise
        """
        # Prepare WhatsApp API request
        url = f"{self.base_url}/messages"
        headers = {
            "D360-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }

        # If invoice object is provided, use WhatsApp template (new guided flow)
        if invoice:
            from ..utils.invoice_parser import (
                format_line_items_for_template,
                format_mpesa_details,
            )

            # Extract invoice fields
            merchant_name = invoice.get("merchant_name", "Unknown Merchant")
            line_items = invoice.get("line_items", [])
            due_date = invoice.get("due_date", "Not specified")
            mpesa_method = invoice.get("mpesa_method")
            total_cents = invoice.get("total_cents", amount_cents)

            # Format line items for template (40-char threshold)
            invoice_for = (
                format_line_items_for_template(line_items)
                if line_items
                else "Various items"
            )

            # Format M-PESA details
            mpesa_details = format_mpesa_details(
                method_type=mpesa_method,
                paybill_number=invoice.get("mpesa_paybill_number"),
                account_number=invoice.get("mpesa_account_number"),
                till_number=invoice.get("mpesa_till_number"),
                phone_number=invoice.get("mpesa_phone_number"),
            )

            # Format total amount
            total_kes = total_cents / 100
            invoice_total = f"KES {total_kes:,.2f}"

            # Build WhatsApp template payload
            payload = {
                "to": customer_msisdn,
                "messaging_product": "whatsapp",
                "type": "template",
                "template": {
                    "name": "invoice_alert",
                    "language": {"policy": "deterministic", "code": "en"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": invoice_id},
                                {"type": "text", "text": merchant_name},
                                {"type": "text", "text": invoice_for},
                                {
                                    "type": "text",
                                    "text": "Yes"
                                    if invoice.get("include_vat")
                                    else "No",
                                },
                                {"type": "text", "text": invoice_total},
                                {"type": "text", "text": due_date},
                                {"type": "text", "text": mpesa_details},
                            ],
                        },
                        {
                            "type": "button",
                            "sub_type": "url",
                            "index": "0",
                            "parameters": [{"type": "text", "text": invoice_id}],
                        },
                    ],
                },
            }

        else:
            # Legacy: Use interactive button (old one-line command flow)
            amount_kes = amount_cents / 100
            invoice_link = f"{settings.api_base_url}/invoices/{invoice_id}"
            message_text = (
                f"Invoice {invoice_id}\n"
                f"Amount: KES {amount_kes:.2f}\n"
                f"View: {invoice_link}"
            )

            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": customer_msisdn,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": message_text},
                    "action": {
                        "buttons": [
                            {
                                "type": "reply",
                                "reply": {
                                    "id": f"pay_{invoice_id}",
                                    "title": "Pay with M-PESA",
                                },
                            }
                        ]
                    },
                },
            }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url, json=payload, headers=headers, timeout=30.0
                )
                response.raise_for_status()
                response_data = response.json()

                # Calculate amount_kes for logging
                amount_kes = amount_cents / 100
                if invoice:
                    amount_kes = invoice.get("total_cents", amount_cents) / 100

                logger.info(
                    "Invoice sent to customer successfully",
                    extra={
                        "invoice_id": invoice_id,
                        "customer_msisdn": customer_msisdn,
                        "amount_kes": amount_kes,
                        "message_id": response_data.get("messages", [{}])[0].get("id"),
                        "message_type": "template" if invoice else "interactive",
                    },
                )

                # Create MessageLog entry (metadata only - privacy-first)
                message_log_data = {
                    "id": str(uuid4()),
                    "invoice_id": invoice_id,
                    "channel": "WHATSAPP",
                    "direction": "OUT",
                    "event": "invoice_sent",
                    "payload": {
                        "message_id": response_data.get("messages", [{}])[0].get("id"),
                        "status": "sent",
                        "status_code": response.status_code,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                }
                message_log_response = (
                    db_session.table("message_log").insert(message_log_data).execute()
                )
                message_log = message_log_response.data[0]

                logger.info(
                    "MessageLog created for invoice send",
                    extra={
                        "invoice_id": invoice_id,
                        "message_log_id": message_log["id"],
                    },
                )

                return True

        except httpx.HTTPStatusError as e:
            logger.error(
                "WhatsApp API returned error status when sending invoice",
                extra={
                    "invoice_id": invoice_id,
                    "status_code": e.response.status_code,
                    "response": e.response.text,
                    "customer_msisdn": customer_msisdn,
                },
                exc_info=True,
            )
            # Create MessageLog entry for failure (metadata only - privacy-first)
            try:
                message_log_data = {
                    "id": str(uuid4()),
                    "invoice_id": invoice_id,
                    "channel": "WHATSAPP",
                    "direction": "OUT",
                    "event": "invoice_send_failed",
                    "payload": {
                        "status": "failed",
                        "status_code": e.response.status_code,
                        "error_type": "http_error",
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                }
                db_session.table("message_log").insert(message_log_data).execute()
            except Exception as log_error:
                logger.error(
                    "Failed to create MessageLog for failed invoice send",
                    extra={"error": str(log_error)},
                )
            return False

        except httpx.RequestError as e:
            logger.error(
                "Failed to send invoice to customer (network error)",
                extra={"invoice_id": invoice_id, "error": str(e)},
                exc_info=True,
            )
            # Create MessageLog entry for failure (metadata only - privacy-first)
            try:
                message_log_data = {
                    "id": str(uuid4()),
                    "invoice_id": invoice_id,
                    "channel": "WHATSAPP",
                    "direction": "OUT",
                    "event": "invoice_send_failed",
                    "payload": {
                        "status": "failed",
                        "error_type": "network_error",
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                }
                db_session.table("message_log").insert(message_log_data).execute()
            except Exception as log_error:
                logger.error(
                    "Failed to create MessageLog for failed invoice send",
                    extra={"error": str(log_error)},
                )

            # SMS FALLBACK: Try sending via SMS
            logger.info(
                "Attempting SMS fallback after WhatsApp network error",
                extra={"invoice_id": invoice_id, "whatsapp_error": str(e)},
            )
            return await self._fallback_to_sms(
                invoice_id=invoice_id,
                customer_msisdn=customer_msisdn,
                customer_name=customer_name,
                amount_cents=amount_cents,
                db_session=db_session,
                whatsapp_error=str(e),
            )

        except Exception as e:
            logger.error(
                "Unexpected error sending invoice to customer",
                extra={"invoice_id": invoice_id, "error": str(e)},
                exc_info=True,
            )
            # Create MessageLog entry for failure (metadata only - privacy-first)
            try:
                message_log_data = {
                    "id": str(uuid4()),
                    "invoice_id": invoice_id,
                    "channel": "WHATSAPP",
                    "direction": "OUT",
                    "event": "invoice_send_failed",
                    "payload": {
                        "status": "failed",
                        "error_type": "unexpected_error",
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                }
                db_session.table("message_log").insert(message_log_data).execute()
            except Exception as log_error:
                logger.error(
                    "Failed to create MessageLog for failed invoice send",
                    extra={"error": str(log_error)},
                )

            # SMS FALLBACK: Try sending via SMS
            logger.info(
                "Attempting SMS fallback after WhatsApp unexpected error",
                extra={"invoice_id": invoice_id, "whatsapp_error": str(e)},
            )
            return await self._fallback_to_sms(
                invoice_id=invoice_id,
                customer_msisdn=customer_msisdn,
                customer_name=customer_name,
                amount_cents=amount_cents,
                db_session=db_session,
                whatsapp_error=str(e),
            )

    async def send_merchant_confirmation(
        self,
        merchant_msisdn: str,
        invoice_id: str,
        customer_msisdn: str,
        amount_cents: int,
        status: str,
    ) -> bool:
        """
        Send confirmation message to merchant after invoice is sent.

        Args:
            merchant_msisdn: Merchant's phone number (MSISDN)
            invoice_id: The invoice ID
            customer_msisdn: Customer's phone number
            amount_cents: Invoice amount in cents
            status: Invoice status

        Returns:
            True if message sent successfully, False otherwise
        """
        # Convert amount from cents to KES
        amount_kes = amount_cents / 100

        # Format confirmation message (keep ≤ 2 lines as per CLAUDE.md)
        message_text = (
            f"✓ Invoice {invoice_id} sent to {customer_msisdn}\n"
            f"Amount: KES {amount_kes:.2f} | Status: {status}"
        )

        try:
            await self.send_message(merchant_msisdn, message_text)

            logger.info(
                "Merchant confirmation sent successfully",
                extra={
                    "merchant_msisdn": merchant_msisdn,
                    "invoice_id": invoice_id,
                    "status": status,
                },
            )
            return True

        except Exception as e:
            logger.error(
                "Failed to send merchant confirmation",
                extra={
                    "merchant_msisdn": merchant_msisdn,
                    "invoice_id": invoice_id,
                    "error": str(e),
                },
                exc_info=True,
            )
            return False

    async def send_receipt_to_customer(
        self,
        customer_msisdn: str,
        invoice_id: str,
        amount_kes: float,
        mpesa_receipt: str,
        db_session: Any,
    ) -> bool:
        """
        Send payment receipt to customer via WhatsApp.

        Formats and sends a receipt confirmation message to the customer after
        successful payment. Message is kept to 2 lines per CLAUDE.md standards.

        Args:
            customer_msisdn: Customer's phone number (MSISDN)
            invoice_id: The invoice ID
            amount_kes: Payment amount in KES (float)
            mpesa_receipt: M-PESA receipt number
            db_session: Database session for logging

        Returns:
            True if message sent successfully, False otherwise
        """
        # Format receipt message (keep ≤ 2 lines as per CLAUDE.md)
        message_text = (
            f"✓ Payment received! Receipt: {mpesa_receipt}\n"
            f"Invoice {invoice_id} | KES {amount_kes:.2f} | Thank you!"
        )

        try:
            await self.send_message(customer_msisdn, message_text)

            logger.info(
                "Receipt sent to customer successfully",
                extra={
                    "invoice_id": invoice_id,
                    "customer_msisdn": customer_msisdn,
                    "mpesa_receipt": mpesa_receipt,
                },
            )

            # Create MessageLog entry (metadata only - privacy-first)
            message_log_data = {
                "id": str(uuid4()),
                "invoice_id": invoice_id,
                "channel": "WHATSAPP",
                "direction": "OUT",
                "event": "receipt_sent_customer",
                "payload": {
                    "status": "sent",
                    "timestamp": datetime.utcnow().isoformat(),
                },
            }
            message_log_response = (
                db_session.table("message_log").insert(message_log_data).execute()
            )
            message_log = message_log_response.data[0]

            logger.info(
                "MessageLog created for customer receipt",
                extra={"invoice_id": invoice_id, "message_log_id": message_log["id"]},
            )

            return True

        except Exception as e:
            logger.error(
                "Failed to send receipt to customer",
                extra={
                    "invoice_id": invoice_id,
                    "customer_msisdn": customer_msisdn,
                    "error": str(e),
                },
                exc_info=True,
            )
            # Create MessageLog entry for failure (metadata only - privacy-first)
            try:
                message_log_data = {
                    "id": str(uuid4()),
                    "invoice_id": invoice_id,
                    "channel": "WHATSAPP",
                    "direction": "OUT",
                    "event": "receipt_send_failed_customer",
                    "payload": {
                        "status": "failed",
                        "error_type": type(e).__name__,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                }
                db_session.table("message_log").insert(message_log_data).execute()
            except Exception as log_error:
                logger.error(
                    "Failed to create MessageLog for failed customer receipt",
                    extra={"error": str(log_error)},
                )
            return False

    async def send_receipt_to_merchant(
        self,
        merchant_msisdn: str,
        invoice_id: str,
        customer_msisdn: str,
        amount_kes: float,
        mpesa_receipt: str,
        db_session: Any,
    ) -> bool:
        """
        Send payment receipt to merchant via WhatsApp.

        Formats and sends a receipt confirmation message to the merchant after
        successful payment. Message is kept to 2 lines per CLAUDE.md standards.

        Args:
            merchant_msisdn: Merchant's phone number (MSISDN)
            invoice_id: The invoice ID
            customer_msisdn: Customer's phone number
            amount_kes: Payment amount in KES (float)
            mpesa_receipt: M-PESA receipt number
            db_session: Database session for logging

        Returns:
            True if message sent successfully, False otherwise
        """
        # Format receipt message (keep ≤ 2 lines as per CLAUDE.md)
        message_text = (
            f"✓ Payment received! Receipt: {mpesa_receipt}\n"
            f"Invoice {invoice_id} | {customer_msisdn} paid KES {amount_kes:.2f}"
        )

        try:
            await self.send_message(merchant_msisdn, message_text)

            logger.info(
                "Receipt sent to merchant successfully",
                extra={
                    "invoice_id": invoice_id,
                    "merchant_msisdn": merchant_msisdn,
                    "customer_msisdn": customer_msisdn,
                    "mpesa_receipt": mpesa_receipt,
                },
            )

            # Create MessageLog entry (metadata only - privacy-first)
            message_log_data = {
                "id": str(uuid4()),
                "invoice_id": invoice_id,
                "channel": "WHATSAPP",
                "direction": "OUT",
                "event": "receipt_sent_merchant",
                "payload": {
                    "status": "sent",
                    "timestamp": datetime.utcnow().isoformat(),
                },
            }
            message_log_response = (
                db_session.table("message_log").insert(message_log_data).execute()
            )
            message_log = message_log_response.data[0]

            logger.info(
                "MessageLog created for merchant receipt",
                extra={"invoice_id": invoice_id, "message_log_id": message_log["id"]},
            )

            return True

        except Exception as e:
            logger.error(
                "Failed to send receipt to merchant",
                extra={
                    "invoice_id": invoice_id,
                    "merchant_msisdn": merchant_msisdn,
                    "error": str(e),
                },
                exc_info=True,
            )
            # Create MessageLog entry for failure (metadata only - privacy-first)
            try:
                message_log_data = {
                    "id": str(uuid4()),
                    "invoice_id": invoice_id,
                    "channel": "WHATSAPP",
                    "direction": "OUT",
                    "event": "receipt_send_failed_merchant",
                    "payload": {
                        "status": "failed",
                        "error_type": type(e).__name__,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                }
                db_session.table("message_log").insert(message_log_data).execute()
            except Exception as log_error:
                logger.error(
                    "Failed to create MessageLog for failed merchant receipt",
                    extra={"error": str(log_error)},
                )
            return False

    async def _fallback_to_sms(
        self,
        invoice_id: str,
        customer_msisdn: str,
        customer_name: Optional[str],
        amount_cents: int,
        db_session: Any,
        whatsapp_error: str,
    ) -> bool:
        """
        Fallback to SMS when WhatsApp delivery fails.

        This method is called internally when WhatsApp fails due to network
        or API errors. It attempts to send the invoice via SMS instead.

        Args:
            invoice_id: The invoice ID
            customer_msisdn: Customer's phone number (MSISDN)
            customer_name: Customer's name (optional)
            amount_cents: Invoice amount in cents
            db_session: Database session for logging
            whatsapp_error: The WhatsApp error message

        Returns:
            True if SMS sent successfully, False otherwise
        """
        from .sms import SMSService

        logger.info(
            "SMS fallback triggered",
            extra={
                "invoice_id": invoice_id,
                "customer_msisdn": customer_msisdn,
                "whatsapp_error": whatsapp_error,
            },
        )

        try:
            # Initialize SMS service
            sms_service = SMSService()

            # Send invoice via SMS
            success = await sms_service.send_invoice_to_customer(
                invoice_id=invoice_id,
                customer_msisdn=customer_msisdn,
                customer_name=customer_name,
                amount_cents=amount_cents,
                db_session=db_session,
            )

            if success:
                logger.info(
                    "SMS fallback successful",
                    extra={
                        "invoice_id": invoice_id,
                        "customer_msisdn": customer_msisdn,
                    },
                )
            else:
                logger.error(
                    "SMS fallback failed",
                    extra={
                        "invoice_id": invoice_id,
                        "customer_msisdn": customer_msisdn,
                    },
                )

            return success

        except Exception as e:
            logger.error(
                "SMS fallback encountered exception",
                extra={
                    "invoice_id": invoice_id,
                    "customer_msisdn": customer_msisdn,
                    "error": str(e),
                },
                exc_info=True,
            )

            # Create MessageLog entry for SMS fallback failure (metadata only - privacy-first)
            try:
                message_log_data = {
                    "id": str(uuid4()),
                    "invoice_id": invoice_id,
                    "channel": "SMS",
                    "direction": "OUT",
                    "event": "sms_fallback_failed",
                    "payload": {
                        "status": "failed",
                        "error_type": type(e).__name__,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                }
                db_session.table("message_log").insert(message_log_data).execute()
            except Exception as log_error:
                logger.error(
                    "Failed to create MessageLog for SMS fallback failure",
                    extra={"error": str(log_error)},
                )

            return False
