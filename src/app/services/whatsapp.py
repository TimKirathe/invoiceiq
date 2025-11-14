"""
WhatsApp Cloud API service for InvoiceIQ.

This module provides the WhatsAppService class for interacting with the WhatsApp
Cloud API, including message parsing, command recognition, state machine management,
and message sending functionality.
"""

import re
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

from ..config import settings
from ..utils.logging import get_logger
from ..utils.phone import validate_msisdn

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
    STATE_COLLECT_PHONE = "COLLECT_PHONE"
    STATE_COLLECT_NAME = "COLLECT_NAME"
    STATE_COLLECT_AMOUNT = "COLLECT_AMOUNT"
    STATE_COLLECT_DESCRIPTION = "COLLECT_DESCRIPTION"
    STATE_READY = "READY"

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
    def set_state(cls, user_id: str, state: str, data: Optional[Dict[str, Any]] = None) -> None:
        """
        Set the state for a user.

        Args:
            user_id: The user's phone number (MSISDN)
            state: The new state
            data: Optional data to store with the state
        """
        if data is None:
            data = {}
        cls.states[user_id] = {"state": state, "data": data}
        logger.info(
            "State updated",
            extra={
                "user_id": user_id,
                "new_state": state,
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


class WhatsAppService:
    """
    Service for interacting with WhatsApp Cloud API.

    Provides methods for parsing incoming messages, recognizing commands,
    managing conversation state, and sending messages.
    """

    def __init__(self) -> None:
        """Initialize the WhatsApp service with configuration from settings."""
        self.waba_token = settings.waba_token
        self.waba_phone_id = settings.waba_phone_id
        self.base_url = "https://graph.facebook.com/v21.0"
        self.state_manager = ConversationStateManager

        logger.info(
            "WhatsAppService initialized",
            extra={
                "base_url": self.base_url,
                "phone_id": self.waba_phone_id[:5] + "..." if self.waba_phone_id else None,
            },
        )

    def parse_incoming_message(self, payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """
        Parse an incoming WhatsApp webhook payload to extract message details.

        Args:
            payload: The webhook payload from WhatsApp Cloud API

        Returns:
            Dictionary with 'text', 'from', and 'type' keys, or None if parsing fails
        """
        try:
            # Navigate the webhook structure: payload['entry'][0]['changes'][0]['value']['messages'][0]
            entry = payload.get("entry", [])
            if not entry:
                logger.warning("No entry field in webhook payload")
                return None

            changes = entry[0].get("changes", [])
            if not changes:
                logger.warning("No changes field in webhook entry")
                return None

            value = changes[0].get("value", {})
            messages = value.get("messages", [])
            if not messages:
                logger.debug("No messages field in webhook value (might be a status update)")
                return None

            message = messages[0]
            message_type = message.get("type")
            sender = message.get("from")

            if not sender:
                logger.warning("No sender in message")
                return None

            # Validate MSISDN format
            try:
                validate_msisdn(sender)
            except ValueError as e:
                logger.warning(
                    "Invalid sender MSISDN format",
                    extra={"sender": sender, "error": str(e)},
                )
                return None

            # Extract text based on message type
            text = None
            if message_type == "text":
                text_obj = message.get("text", {})
                text = text_obj.get("body")
            elif message_type == "interactive":
                # Handle button clicks
                interactive = message.get("interactive", {})
                button_reply = interactive.get("button_reply", {})
                text = button_reply.get("id") or button_reply.get("title")
            else:
                logger.info(
                    "Unsupported message type",
                    extra={"type": message_type, "sender": sender},
                )
                return None

            if not text:
                logger.warning("No text content in message")
                return None

            result = {"text": text, "from": sender, "type": message_type}
            logger.info(
                "Message parsed successfully",
                extra={"sender": sender, "type": message_type, "text_length": len(text)},
            )
            return result

        except (KeyError, IndexError, TypeError) as e:
            logger.error(
                "Failed to parse webhook payload",
                extra={"error": str(e), "payload_keys": list(payload.keys())},
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
            return {"command": "remind", "params": {"invoice_id": match.group(1).strip()}}

        # Cancel command: cancel <invoice_id>
        cancel_pattern = r"^cancel\s+(.+)$"
        match = re.match(cancel_pattern, text)
        if match:
            return {"command": "cancel", "params": {"invoice_id": match.group(1).strip()}}

        # One-line invoice command: invoice <phone_or_name> <amount> <desc...>
        # This regex matches: invoice followed by either a phone number or name, then amount, then description
        invoice_pattern = r"^invoice\s+(\S+(?:\s+\S+)*?)\s+(\d+)\s+(.{3,})$"
        match = re.match(invoice_pattern, message_text.strip(), re.IGNORECASE)
        if match:
            phone_or_name = match.group(1).strip()
            amount_str = match.group(2).strip()
            description = match.group(3).strip()

            # Check if it's a phone number (starts with 254 and is numeric)
            if re.match(r"^2547\d{8}$", phone_or_name):
                # It's a phone number
                return {
                    "command": "invoice",
                    "params": {
                        "phone": phone_or_name,
                        "amount": int(amount_str),
                        "description": description,
                    },
                }
            else:
                # It's a name
                return {
                    "command": "invoice",
                    "params": {
                        "name": phone_or_name,
                        "amount": int(amount_str),
                        "description": description,
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

        # STATE: IDLE - Start guided flow
        if current_state == self.state_manager.STATE_IDLE:
            self.state_manager.set_state(user_id, self.state_manager.STATE_COLLECT_PHONE)
            return {
                "response": "Let's create an invoice!\n\nPlease send the customer's phone number (format: 2547XXXXXXXX):",
                "action": "started",
            }

        # STATE: COLLECT_PHONE - Validate and store phone
        elif current_state == self.state_manager.STATE_COLLECT_PHONE:
            try:
                validated_phone = validate_msisdn(text)
                self.state_manager.update_data(user_id, "phone", validated_phone)
                self.state_manager.set_state(
                    user_id, self.state_manager.STATE_COLLECT_NAME, data
                )
                return {
                    "response": "Great! Now, what is the customer's name? (or send '-' to skip)",
                    "action": "phone_collected",
                }
            except ValueError:
                return {
                    "response": "Invalid phone number. Please use format 2547XXXXXXXX",
                    "action": "validation_error",
                }

        # STATE: COLLECT_NAME - Store name (or skip)
        elif current_state == self.state_manager.STATE_COLLECT_NAME:
            if text == "-":
                self.state_manager.update_data(user_id, "name", None)
            else:
                # Validate name length
                if len(text) < 2 or len(text) > 60:
                    return {
                        "response": "Name must be between 2 and 60 characters. Please try again:",
                        "action": "validation_error",
                    }
                self.state_manager.update_data(user_id, "name", text)

            self.state_manager.set_state(
                user_id, self.state_manager.STATE_COLLECT_AMOUNT, data
            )
            return {
                "response": "Got it! What is the invoice amount in KES? (whole numbers only)",
                "action": "name_collected",
            }

        # STATE: COLLECT_AMOUNT - Validate and store amount
        elif current_state == self.state_manager.STATE_COLLECT_AMOUNT:
            try:
                amount = int(text)
                if amount < 1:
                    raise ValueError("Amount must be at least 1 KES")

                # Store amount in cents
                self.state_manager.update_data(user_id, "amount_cents", amount * 100)
                self.state_manager.set_state(
                    user_id, self.state_manager.STATE_COLLECT_DESCRIPTION, data
                )
                return {
                    "response": "Perfect! Finally, what is this invoice for? (3-120 characters)",
                    "action": "amount_collected",
                }
            except ValueError:
                return {
                    "response": "Please enter a valid amount (minimum 1 KES)",
                    "action": "validation_error",
                }

        # STATE: COLLECT_DESCRIPTION - Validate and store description
        elif current_state == self.state_manager.STATE_COLLECT_DESCRIPTION:
            if len(text) < 3 or len(text) > 120:
                return {
                    "response": "Description must be between 3 and 120 characters. Please try again:",
                    "action": "validation_error",
                }

            self.state_manager.update_data(user_id, "description", text)
            self.state_manager.set_state(user_id, self.state_manager.STATE_READY, data)

            # Show preview
            phone = data.get("phone")
            name = data.get("name") or "Not provided"
            amount = data.get("amount_cents", 0) // 100
            description = text

            preview = (
                f"Ready to send!\n\n"
                f"Customer: {name}\n"
                f"Phone: {phone}\n"
                f"Amount: KES {amount}\n"
                f"Description: {description}\n\n"
                f"Send 'confirm' to proceed or 'cancel' to start over."
            )
            return {"response": preview, "action": "ready"}

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
        logger.error("Unknown state", extra={"state": current_state, "user_id": user_id})
        self.state_manager.clear_state(user_id)
        return {
            "response": "An error occurred. Please start again by sending 'invoice'.",
            "action": "error",
        }

    async def send_message(self, to: str, message: str) -> Optional[Dict[str, Any]]:
        """
        Send a text message to a WhatsApp user.

        Args:
            to: Recipient's phone number (MSISDN)
            message: Text message to send

        Returns:
            Response data from WhatsApp API, or None on failure
        """
        url = f"{self.base_url}/{self.waba_phone_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.waba_token}",
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
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, headers=headers, timeout=30.0)
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
            raise Exception(f"WhatsApp API error: {e.response.status_code} - {e.response.text}")

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

    async def send_invoice_to_customer(
        self,
        invoice_id: str,
        customer_msisdn: str,
        customer_name: Optional[str],
        amount_cents: int,
        description: str,
        db_session: Any,
    ) -> bool:
        """
        Send an invoice to a customer via WhatsApp with interactive payment button.

        Formats and sends the invoice message with a "Pay with M-PESA" button.
        Creates a MessageLog entry for the outbound message.

        Args:
            invoice_id: The invoice ID
            customer_msisdn: Customer's phone number (MSISDN)
            customer_name: Customer's name (optional)
            amount_cents: Invoice amount in cents
            description: Invoice description
            db_session: Database session for logging

        Returns:
            True if message sent successfully, False otherwise
        """
        # Import here to avoid circular dependency
        from ..models import MessageLog

        # Convert amount from cents to KES
        amount_kes = amount_cents / 100

        # Format invoice message (keep ≤ 2 lines as per CLAUDE.md)
        message_text = f"Invoice {invoice_id}\nAmount: KES {amount_kes:.2f} | {description}"

        # Prepare WhatsApp interactive button payload
        url = f"{self.base_url}/{self.waba_phone_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.waba_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": customer_msisdn,
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
                                "id": f"pay_{invoice_id}",
                                "title": "Pay with M-PESA"
                            }
                        }
                    ]
                }
            }
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, headers=headers, timeout=30.0)
                response.raise_for_status()
                response_data = response.json()

                logger.info(
                    "Invoice sent to customer successfully",
                    extra={
                        "invoice_id": invoice_id,
                        "customer_msisdn": customer_msisdn,
                        "amount_kes": amount_kes,
                        "message_id": response_data.get("messages", [{}])[0].get("id"),
                    },
                )

                # Create MessageLog entry (metadata only - privacy-first)
                message_log = MessageLog(
                    invoice_id=invoice_id,
                    channel="WHATSAPP",
                    direction="OUT",
                    event="invoice_sent",
                    payload={
                        "message_id": response_data.get("messages", [{}])[0].get("id"),
                        "status": "sent",
                        "status_code": response.status_code,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )
                db_session.add(message_log)
                await db_session.commit()

                logger.info(
                    "MessageLog created for invoice send",
                    extra={"invoice_id": invoice_id, "message_log_id": message_log.id},
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
                message_log = MessageLog(
                    invoice_id=invoice_id,
                    channel="WHATSAPP",
                    direction="OUT",
                    event="invoice_send_failed",
                    payload={
                        "status": "failed",
                        "status_code": e.response.status_code,
                        "error_type": "http_error",
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )
                db_session.add(message_log)
                await db_session.commit()
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
                message_log = MessageLog(
                    invoice_id=invoice_id,
                    channel="WHATSAPP",
                    direction="OUT",
                    event="invoice_send_failed",
                    payload={
                        "status": "failed",
                        "error_type": "network_error",
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )
                db_session.add(message_log)
                await db_session.commit()
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
                description=description,
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
                message_log = MessageLog(
                    invoice_id=invoice_id,
                    channel="WHATSAPP",
                    direction="OUT",
                    event="invoice_send_failed",
                    payload={
                        "status": "failed",
                        "error_type": "unexpected_error",
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )
                db_session.add(message_log)
                await db_session.commit()
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
                description=description,
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
        # Import here to avoid circular dependency
        from ..models import MessageLog

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
            message_log = MessageLog(
                invoice_id=invoice_id,
                channel="WHATSAPP",
                direction="OUT",
                event="receipt_sent_customer",
                payload={
                    "status": "sent",
                    "timestamp": datetime.utcnow().isoformat(),
                },
            )
            db_session.add(message_log)
            await db_session.commit()

            logger.info(
                "MessageLog created for customer receipt",
                extra={"invoice_id": invoice_id, "message_log_id": message_log.id},
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
                message_log = MessageLog(
                    invoice_id=invoice_id,
                    channel="WHATSAPP",
                    direction="OUT",
                    event="receipt_send_failed_customer",
                    payload={
                        "status": "failed",
                        "error_type": type(e).__name__,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )
                db_session.add(message_log)
                await db_session.commit()
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
        # Import here to avoid circular dependency
        from ..models import MessageLog

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
            message_log = MessageLog(
                invoice_id=invoice_id,
                channel="WHATSAPP",
                direction="OUT",
                event="receipt_sent_merchant",
                payload={
                    "status": "sent",
                    "timestamp": datetime.utcnow().isoformat(),
                },
            )
            db_session.add(message_log)
            await db_session.commit()

            logger.info(
                "MessageLog created for merchant receipt",
                extra={"invoice_id": invoice_id, "message_log_id": message_log.id},
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
                message_log = MessageLog(
                    invoice_id=invoice_id,
                    channel="WHATSAPP",
                    direction="OUT",
                    event="receipt_send_failed_merchant",
                    payload={
                        "status": "failed",
                        "error_type": type(e).__name__,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )
                db_session.add(message_log)
                await db_session.commit()
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
        description: str,
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
            description: Invoice description
            db_session: Database session for logging
            whatsapp_error: The WhatsApp error message

        Returns:
            True if SMS sent successfully, False otherwise
        """
        # Import here to avoid circular dependency
        from ..models import MessageLog
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
                description=description,
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
                message_log = MessageLog(
                    invoice_id=invoice_id,
                    channel="SMS",
                    direction="OUT",
                    event="sms_fallback_failed",
                    payload={
                        "status": "failed",
                        "error_type": type(e).__name__,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )
                db_session.add(message_log)
                await db_session.commit()
            except Exception as log_error:
                logger.error(
                    "Failed to create MessageLog for SMS fallback failure",
                    extra={"error": str(log_error)},
                )

            return False