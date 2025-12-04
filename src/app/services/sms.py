"""
SMS service for InvoiceIQ using Africa's Talking API.

This module provides the SMSService class for sending SMS messages via
Africa's Talking, with fallback support when WhatsApp delivery fails.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from ..config import settings
from ..utils.logging import get_logger
from ..utils.phone import validate_msisdn

# Set up logger
logger = get_logger(__name__)


class SMSService:
    """
    Service for interacting with Africa's Talking SMS API.

    Provides methods for sending SMS messages, formatting invoice messages,
    and parsing inbound SMS commands.
    """

    def __init__(self) -> None:
        """Initialize the SMS service with configuration from settings."""
        self.api_key = settings.sms_api_key
        self.username = settings.sms_username
        self.sender_id = settings.sms_sender_id
        self.use_sandbox = settings.sms_use_sandbox

        # Set base URL based on sandbox mode
        if self.use_sandbox:
            self.base_url = "https://api.sandbox.africastalking.com/version1/messaging"
        else:
            self.base_url = "https://api.africastalking.com/version1/messaging"

        logger.info(
            "SMSService initialized",
            extra={
                "base_url": self.base_url,
                "username": self.username,
                "use_sandbox": self.use_sandbox,
                "has_sender_id": self.sender_id is not None,
            },
        )

    @retry(
        retry=retry_if_exception_type((httpx.RequestError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        before_sleep=before_sleep_log(logger, logging.INFO),
        reraise=True,
    )
    async def send_sms(
        self,
        to: str,
        message: str,
        from_: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send an SMS message via Africa's Talking API with retry logic.

        Retries on network errors with exponential backoff:
        - 3 attempts total
        - Wait times: 1s, 2s, 4s
        - Retries only on network/timeout errors, not API errors

        Args:
            to: Recipient's phone number (MSISDN in E.164 format)
            message: Text message to send (max 160 chars for single SMS)
            from_: Optional sender ID/shortcode (uses config default if not provided)

        Returns:
            Dictionary with status and response data from API

        Raises:
            Exception: If SMS sending fails due to network or API errors after retries
        """
        # Validate phone number
        try:
            validated_phone = validate_msisdn(to)
            # Add + prefix for Africa's Talking API (E.164 format with +)
            phone_with_plus = f"+{validated_phone}"
        except ValueError as e:
            logger.error(
                "Invalid phone number for SMS",
                extra={"phone": to, "error": str(e)},
            )
            raise ValueError(f"Invalid phone number: {str(e)}")

        # Use provided sender ID or fall back to configured one
        sender_id = from_ or self.sender_id

        # Prepare request payload (form-urlencoded)
        payload = {
            "username": self.username,
            "to": phone_with_plus,
            "message": message,
        }

        # Add sender ID if available
        if sender_id:
            payload["from"] = sender_id

        headers = {
            "apiKey": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        logger.info(
            "Sending SMS",
            extra={
                "to": to,
                "message_length": len(message),
                "sender_id": sender_id,
            },
        )

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.base_url,
                    data=payload,
                    headers=headers,
                    timeout=10.0,
                )
                response.raise_for_status()
                response_data = response.json()

                logger.info(
                    "SMS sent successfully",
                    extra={
                        "to": to,
                        "message_length": len(message),
                        "response": response_data,
                    },
                )

                return {
                    "status": "success",
                    "response": response_data,
                    "recipient": to,
                    "message": message,
                }

        except httpx.HTTPStatusError as e:
            logger.error(
                "Africa's Talking API returned error status",
                extra={
                    "status_code": e.response.status_code,
                    "response": e.response.text,
                    "to": to,
                },
                exc_info=True,
            )
            raise Exception(
                f"SMS API error: {e.response.status_code} - {e.response.text}"
            )

        except httpx.TimeoutException as e:
            logger.error(
                "SMS request timed out",
                extra={"error": str(e), "to": to},
                exc_info=True,
            )
            raise Exception(f"SMS request timed out: {str(e)}")

        except httpx.RequestError as e:
            logger.error(
                "Failed to send SMS (network error)",
                extra={"error": str(e), "to": to},
                exc_info=True,
            )
            raise Exception(f"Failed to send SMS: {str(e)}")

        except Exception as e:
            logger.error(
                "Unexpected error sending SMS",
                extra={"error": str(e), "to": to},
                exc_info=True,
            )
            raise Exception(f"SMS API error: {str(e)}")

    def format_invoice_sms(self, invoice: Dict[str, Any]) -> str:
        """
        Format an invoice as an SMS message (≤2 lines).

        The message includes invoice ID, amount, and payment instruction.
        Keeps message concise to fit within 2 lines as per CLAUDE.md standards.

        Args:
            invoice: The invoice dictionary with 'id' and 'amount_cents' keys

        Returns:
            Formatted SMS message string

        Example:
            "Invoice #INV-123 for KES 500. Reply PAY to complete payment. -InvoiceIQ"
        """
        # Convert amount from cents to KES
        amount_kes = invoice["amount_cents"] / 100

        # Format message (≤2 lines)
        message = (
            f"Invoice #{invoice['id']} for KES {amount_kes:.2f}. "
            f"Reply PAY to complete payment. -InvoiceIQ"
        )

        return message

    async def send_invoice_to_customer(
        self,
        invoice_id: str,
        customer_msisdn: str,
        customer_name: Optional[str],
        amount_cents: int,
        db_session: Any,
    ) -> bool:
        """
        Send an invoice to a customer via SMS.

        Formats and sends the invoice message as SMS. Creates a MessageLog
        entry for the outbound message.

        Args:
            invoice_id: The invoice ID
            customer_msisdn: Customer's phone number (MSISDN)
            customer_name: Customer's name (optional)
            amount_cents: Invoice amount in cents
            db_session: Supabase client for logging

        Returns:
            True if message sent successfully, False otherwise
        """
        # Convert amount from cents to KES
        amount_kes = amount_cents / 100

        # Format SMS message with invoice link
        invoice_link = f"{settings.api_base_url}/invoices/{invoice_id}"
        message = (
            f"Invoice #{invoice_id} for KES {amount_kes:.2f}. "
            f"View: {invoice_link} -InvoiceIQ"
        )

        try:
            # Send SMS
            await self.send_sms(to=customer_msisdn, message=message)

            logger.info(
                "Invoice sent to customer via SMS",
                extra={
                    "invoice_id": invoice_id,
                    "customer_msisdn": customer_msisdn,
                    "amount_kes": amount_kes,
                },
            )

            # Create MessageLog entry (metadata only - privacy-first)
            message_log_data = {
                "id": str(uuid4()),
                "invoice_id": invoice_id,
                "channel": "SMS",
                "direction": "OUT",
                "event": "invoice_sent",
                "payload": {
                    "status": "sent",
                    "timestamp": datetime.utcnow().isoformat(),
                },
            }
            message_log_response = db_session.table("message_log").insert(message_log_data).execute()
            message_log = message_log_response.data[0]

            logger.info(
                "MessageLog created for SMS invoice send",
                extra={"invoice_id": invoice_id, "message_log_id": message_log["id"]},
            )

            return True

        except Exception as e:
            logger.error(
                "Failed to send invoice via SMS",
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
                    "channel": "SMS",
                    "direction": "OUT",
                    "event": "invoice_send_failed",
                    "payload": {
                        "status": "failed",
                        "error_type": type(e).__name__,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                }
                db_session.table("message_log").insert(message_log_data).execute()
            except Exception as log_error:
                logger.error(
                    "Failed to create MessageLog for failed SMS send",
                    extra={"error": str(log_error)},
                )

            return False

    def parse_sms_command(self, message_text: str) -> Dict[str, Any]:
        """
        Parse an inbound SMS message to recognize commands.

        Supports all merchant commands (invoice, remind, cancel, help)
        and payment commands (PAY).

        Args:
            message_text: The SMS message text to parse

        Returns:
            Dictionary with 'command' and 'params' keys
        """
        import re

        text = message_text.strip()
        text_lower = text.lower()

        # Payment command: "PAY"
        if text_lower == "pay":
            return {"command": "pay", "params": {}}

        # Help command
        if text_lower == "help":
            return {"command": "help", "params": {}}

        # Start guided flow
        if text_lower == "invoice" or text_lower == "new invoice":
            return {"command": "start_guided", "params": {}}

        # Remind command: remind <invoice_id>
        remind_pattern = r"^remind\s+(.+)$"
        match = re.match(remind_pattern, text_lower)
        if match:
            return {"command": "remind", "params": {"invoice_id": match.group(1).strip()}}

        # Cancel command: cancel <invoice_id>
        cancel_pattern = r"^cancel\s+(.+)$"
        match = re.match(cancel_pattern, text_lower)
        if match:
            return {"command": "cancel", "params": {"invoice_id": match.group(1).strip()}}

        # Unknown command
        logger.info(
            "Unknown SMS command received",
            extra={"sms_text": message_text},
        )
        return {"command": "unknown", "params": {}}

    def parse_africas_talking_callback(
        self, payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Parse an inbound SMS callback from Africa's Talking.

        Africa's Talking callbacks typically include:
        - from: Sender's phone number
        - to: Recipient's phone number (our shortcode/number)
        - text: Message text
        - date: Timestamp
        - id: Message ID
        - linkId: Link ID for session tracking (optional)

        Args:
            payload: The callback payload from Africa's Talking

        Returns:
            Dictionary with parsed data, or None if parsing fails
        """
        try:
            result = {
                "from": payload.get("from"),
                "to": payload.get("to"),
                "text": payload.get("text"),
                "date": payload.get("date"),
                "message_id": payload.get("id"),
                "link_id": payload.get("linkId"),
            }

            logger.info(
                "Africa's Talking callback parsed",
                extra={
                    "from": result["from"],
                    "message_id": result["message_id"],
                },
            )

            return result

        except (KeyError, TypeError) as e:
            logger.error(
                "Failed to parse Africa's Talking callback",
                extra={"error": str(e), "payload": payload},
                exc_info=True,
            )
            return None

    def parse_delivery_receipt(
        self, payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Parse a delivery receipt callback from Africa's Talking.

        Delivery receipts typically include:
        - id: Message ID
        - status: Delivery status (Success, Failed, etc.)
        - phoneNumber: Recipient's phone number
        - retryCount: Number of retry attempts (optional)

        Args:
            payload: The delivery receipt payload from Africa's Talking

        Returns:
            Dictionary with parsed data, or None if parsing fails
        """
        try:
            result = {
                "message_id": payload.get("id"),
                "status": payload.get("status"),
                "phone_number": payload.get("phoneNumber"),
                "retry_count": payload.get("retryCount", 0),
            }

            logger.info(
                "Delivery receipt parsed",
                extra={
                    "message_id": result["message_id"],
                    "status": result["status"],
                    "phone_number": result["phone_number"],
                },
            )

            return result

        except (KeyError, TypeError) as e:
            logger.error(
                "Failed to parse delivery receipt",
                extra={"error": str(e), "payload": payload},
                exc_info=True,
            )
            return None