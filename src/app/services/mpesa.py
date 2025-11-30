"""
M-PESA Daraja API service for STK Push integration.

This module provides MPesaService class for interacting with the M-PESA
Daraja API, including OAuth token generation and STK Push initiation.
"""

import base64
import logging
import time
from datetime import datetime
from typing import Any, Dict
from xml.sax.saxutils import escape as xml_escape

import httpx
import pybreaker
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from ..config import settings
from ..utils.logging import get_logger

logger = get_logger(__name__)


# Custom circuit breaker listener for logging state changes
class MPesaCircuitBreakerListener(pybreaker.CircuitBreakerListener):
    """Custom listener to log M-PESA circuit breaker state changes."""

    def state_change(self, cb, old_state, new_state):
        """Log when circuit breaker state changes."""
        logger.warning(
            f"M-PESA circuit breaker state changed from {old_state.name} to {new_state.name}",
            extra={
                "old_state": old_state.name,
                "new_state": new_state.name,
                "fail_counter": cb.fail_counter,
            },
        )


# Circuit breaker for M-PESA API
# Opens after 5 failures, stays open for 60 seconds before attempting recovery
mpesa_circuit_breaker = pybreaker.CircuitBreaker(
    fail_max=5,
    reset_timeout=60,
    listeners=[MPesaCircuitBreakerListener()],
)


class MPesaService:
    """
    Service for M-PESA Daraja API integration.

    Handles OAuth token generation with caching, password generation,
    and STK Push request initiation.
    """

    # Token cache: stores access_token and expiration timestamp
    _token_cache: Dict[str, Any] = {}

    # M-PESA API base URLs
    SANDBOX_BASE_URL = "https://sandbox.safaricom.co.ke"
    PRODUCTION_BASE_URL = "https://api.safaricom.co.ke"

    def __init__(self, environment: str = "sandbox") -> None:
        """
        Initialize MPesaService.

        Args:
            environment: API environment ("sandbox" or "production")
        """
        self.environment = environment.lower()
        self.base_url = (
            self.PRODUCTION_BASE_URL
            if self.environment == "production"
            else self.SANDBOX_BASE_URL
        )
        self.consumer_key = settings.mpesa_consumer_key
        self.consumer_secret = settings.mpesa_consumer_secret
        self.shortcode = settings.mpesa_shortcode
        self.passkey = settings.mpesa_passkey
        self.callback_url = settings.mpesa_callback_url

        logger.info(
            f"MPesaService initialized for {self.environment} environment",
            extra={"environment": self.environment, "base_url": self.base_url},
        )

    @retry(
        retry=retry_if_exception_type((httpx.RequestError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        before_sleep=before_sleep_log(logger, logging.INFO),
        reraise=True,
    )
    async def get_access_token(self) -> str:
        """
        Get OAuth access token with caching and retry logic.

        Checks if cached token exists and is not expired. If cached and valid,
        returns cached token. Otherwise, generates new token from M-PESA API
        and caches it with expiration timestamp.

        Retries on network errors with exponential backoff:
        - 3 attempts total
        - Wait times: 1s, 2s, 4s
        - Retries only on network/timeout errors, not API errors

        Returns:
            M-PESA OAuth access token

        Raises:
            httpx.HTTPError: If token generation fails after retries
            ValueError: If response is invalid
        """
        # Check if cached token exists and is not expired
        current_time = time.time()
        cached_token = self._token_cache.get("access_token")
        cached_expiry = self._token_cache.get("expires_at", 0)

        if cached_token and current_time < cached_expiry:
            logger.debug(
                "Using cached M-PESA access token",
                extra={
                    "expires_in": int(cached_expiry - current_time),
                },
            )
            return cached_token

        # Generate new token
        logger.info("Generating new M-PESA access token")

        # Create Basic Auth header
        credentials = f"{self.consumer_key}:{self.consumer_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        auth_header = f"Basic {encoded_credentials}"

        # Make OAuth request
        oauth_url = f"{self.base_url}/oauth/v1/generate?grant_type=client_credentials"
        headers = {"Authorization": auth_header}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(oauth_url, headers=headers)
                response.raise_for_status()

                data = response.json()
                access_token = data.get("access_token")
                expires_in = int(data.get("expires_in", 3600))

                if not access_token:
                    raise ValueError("No access_token in response")

                # Cache token with 60-second buffer before expiration
                expiry_timestamp = current_time + expires_in - 60

                self._token_cache["access_token"] = access_token
                self._token_cache["expires_at"] = expiry_timestamp

                logger.info(
                    "M-PESA access token generated successfully",
                    extra={"expires_in": expires_in},
                )

                return access_token

        except httpx.HTTPError as e:
            logger.error(
                "Failed to generate M-PESA access token",
                extra={"error": str(e)},
                exc_info=True,
            )
            raise
        except (ValueError, KeyError) as e:
            logger.error(
                "Invalid response from M-PESA OAuth API",
                extra={"error": str(e)},
                exc_info=True,
            )
            raise ValueError(f"Invalid OAuth response: {e}")

    def generate_password(self, shortcode: str, passkey: str, timestamp: str) -> str:
        """
        Generate password for STK Push request.

        Password is base64 encoded string of: shortcode + passkey + timestamp

        Args:
            shortcode: Business shortcode
            passkey: M-PESA passkey
            timestamp: Timestamp in YYYYMMDDHHmmss format

        Returns:
            Base64 encoded password
        """
        raw_password = f"{shortcode}{passkey}{timestamp}"
        encoded_password = base64.b64encode(raw_password.encode()).decode()

        logger.debug(
            "Generated STK Push password",
            extra={"shortcode": shortcode, "timestamp": timestamp},
        )

        return encoded_password

    def generate_timestamp(self) -> str:
        """
        Generate timestamp for STK Push request.

        Returns:
            Timestamp in YYYYMMDDHHmmss format (e.g., "20250112153045")
        """
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

        logger.debug("Generated timestamp", extra={"timestamp": timestamp})

        return timestamp

    def _sanitize_xml_text(self, text: str) -> str:
        """
        Sanitize text for safe inclusion in M-PESA API requests.

        M-PESA Daraja API processes requests through XML, so special XML characters
        must be escaped to prevent parsing errors. This method uses Python's standard
        xml.sax.saxutils.escape() to escape XML special characters.

        Escapes the following characters:
        - & → &amp;
        - < → &lt;
        - > → &gt;

        Args:
            text: Text to sanitize

        Returns:
            Sanitized text with XML special characters escaped
        """
        if not text:
            return text

        # Use Python's standard library XML escaping
        sanitized = xml_escape(text)

        logger.debug(
            "Sanitized XML text",
            extra={"original": text, "sanitized": sanitized},
        )

        return sanitized

    @retry(
        retry=retry_if_exception_type((httpx.RequestError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        before_sleep=before_sleep_log(logger, logging.INFO),
        reraise=True,
    )
    async def initiate_stk_push(
        self,
        phone_number: str,
        amount: int,
        account_reference: str,
        transaction_desc: str,
        payment_method: str | None = None,
    ) -> Dict[str, Any]:
        """
        Initiate M-PESA STK Push request with retry logic and circuit breaker.

        Sends STK Push request to customer's phone to prompt for M-PESA payment.

        Retries on network errors with exponential backoff:
        - 3 attempts total
        - Wait times: 1s, 2s, 4s
        - Retries only on network/timeout errors, not API errors

        Circuit breaker protection:
        - Opens after 5 consecutive failures
        - Stays open for 60 seconds before attempting recovery

        Args:
            phone_number: Customer MSISDN (254XXXXXXXXX format)
            amount: Amount in whole KES (no decimals)
            account_reference: Invoice ID or reference
            transaction_desc: Transaction description
            payment_method: Payment method type ("PAYBILL" or "TILL").
                          Falls back to settings.mpesa_payment_type if not provided.

        Returns:
            M-PESA API response data

        Raises:
            httpx.HTTPError: If STK Push request fails after retries
            ValueError: If response is invalid
            pybreaker.CircuitBreakerError: If circuit breaker is open
        """
        logger.info(
            "Initiating STK Push",
            extra={
                "phone_number": phone_number,
                "amount": amount,
                "account_reference": account_reference,
                "payment_method": payment_method,
            },
        )

        # Wrap the actual API call with circuit breaker
        return await self._initiate_stk_push_with_circuit_breaker(
            phone_number, amount, account_reference, transaction_desc, payment_method
        )

    async def _initiate_stk_push_with_circuit_breaker(
        self,
        phone_number: str,
        amount: int,
        account_reference: str,
        transaction_desc: str,
        payment_method: str | None = None,
    ) -> Dict[str, Any]:
        """
        Internal method to initiate STK Push with circuit breaker protection.

        This method is wrapped by the circuit breaker to prevent cascading failures.
        """
        # Get access token
        access_token = await self.get_access_token()

        # Generate timestamp and password
        timestamp = self.generate_timestamp()
        password = self.generate_password(self.shortcode, self.passkey, timestamp)

        # Determine transaction type based on payment method
        # Default to config setting if not explicitly provided
        method = payment_method or settings.mpesa_payment_type.upper()
        transaction_type = (
            "CustomerBuyGoodsOnline" if method == "TILL"
            else "CustomerPayBillOnline"
        )

        # Sanitize text fields to prevent XML parsing errors in Daraja API
        # M-PESA processes these fields through XML, so special characters
        # like &, <, > must be escaped to prevent parsing failures
        sanitized_account_ref = self._sanitize_xml_text(account_reference)
        sanitized_transaction_desc = self._sanitize_xml_text(transaction_desc)

        # Construct STK Push request payload
        # CRITICAL: M-PESA API expects numeric fields as integers, not strings
        payload = {
            "BusinessShortCode": int(self.shortcode),
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": transaction_type,
            "Amount": amount,
            "PartyA": int(phone_number),
            "PartyB": int(self.shortcode),
            "PhoneNumber": int(phone_number),
            "CallBackURL": self.callback_url,
            "AccountReference": sanitized_account_ref,
            "TransactionDesc": sanitized_transaction_desc,
        }

        # Make STK Push request with circuit breaker
        stk_url = f"{self.base_url}/mpesa/stkpush/v1/processrequest"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        # DEBUG: Log complete request details before making API call
        logger.info(
            "DEBUG: STK Push request details",
            extra={
                "url": stk_url,
                "method": "POST",
                "headers": {
                    "Authorization": f"Bearer {access_token[:10]}...{access_token[-10:] if len(access_token) > 20 else '***'}",
                    "Content-Type": "application/json",
                },
                "payload": {
                    "BusinessShortCode": payload["BusinessShortCode"],
                    "Password": f"{payload['Password'][:10]}...{payload['Password'][-10:] if len(payload['Password']) > 20 else '***'}",
                    "Timestamp": payload["Timestamp"],
                    "TransactionType": payload["TransactionType"],
                    "Amount": payload["Amount"],
                    "PartyA": payload["PartyA"],
                    "PartyB": payload["PartyB"],
                    "PhoneNumber": payload["PhoneNumber"],
                    "CallBackURL": payload["CallBackURL"],
                    "AccountReference": payload["AccountReference"],
                    "TransactionDesc": payload["TransactionDesc"],
                },
                "payload_types": {
                    "BusinessShortCode": type(payload["BusinessShortCode"]).__name__,
                    "Password": type(payload["Password"]).__name__,
                    "Timestamp": type(payload["Timestamp"]).__name__,
                    "TransactionType": type(payload["TransactionType"]).__name__,
                    "Amount": type(payload["Amount"]).__name__,
                    "PartyA": type(payload["PartyA"]).__name__,
                    "PartyB": type(payload["PartyB"]).__name__,
                    "PhoneNumber": type(payload["PhoneNumber"]).__name__,
                    "CallBackURL": type(payload["CallBackURL"]).__name__,
                    "AccountReference": type(payload["AccountReference"]).__name__,
                    "TransactionDesc": type(payload["TransactionDesc"]).__name__,
                },
                "payment_method": method,
                "environment": self.environment,
            },
        )

        try:
            # Wrap the HTTP call with circuit breaker
            @mpesa_circuit_breaker
            async def make_stk_request() -> Dict[str, Any]:
                async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                    response = await client.post(stk_url, json=payload, headers=headers)

                    # DEBUG: Log raw response before processing
                    logger.info(
                        "DEBUG: STK Push raw response received",
                        extra={
                            "status_code": response.status_code,
                            "response_headers": dict(response.headers),
                            "response_text": response.text[:500] if len(response.text) > 500 else response.text,
                        },
                    )

                    response.raise_for_status()
                    data = response.json()

                    logger.info(
                        "STK Push initiated successfully",
                        extra={
                            "phone_number": phone_number,
                            "amount": amount,
                            "response": data,
                        },
                    )

                    return data

            return await make_stk_request()

        except pybreaker.CircuitBreakerError as e:
            logger.error(
                "Circuit breaker is OPEN - M-PESA API is unavailable",
                extra={
                    "error": str(e),
                    "phone_number": phone_number,
                    "amount": amount,
                },
                exc_info=True,
            )
            raise

        except httpx.TimeoutException:
            logger.error(
                "STK Push request timed out",
                extra={
                    "phone_number": phone_number,
                    "amount": amount,
                    "timeout": 30.0,
                },
                exc_info=True,
            )
            raise Exception("Payment service timed out. Please try again.")

        except httpx.HTTPStatusError as e:
            logger.error(
                "M-PESA API returned error",
                extra={
                    "status_code": e.response.status_code,
                    "response": e.response.text,
                },
                exc_info=True,
            )
            raise Exception(f"Payment service error: {e.response.status_code}")

        except httpx.HTTPError as e:
            logger.error(
                "Failed to initiate STK Push",
                extra={
                    "error": str(e),
                    "phone_number": phone_number,
                    "amount": amount,
                },
                exc_info=True,
            )
            raise
        except (ValueError, KeyError) as e:
            logger.error(
                "Invalid response from M-PESA STK Push API",
                extra={"error": str(e)},
                exc_info=True,
            )
            raise ValueError(f"Invalid STK Push response: {e}")