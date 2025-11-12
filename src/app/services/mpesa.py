"""
M-PESA Daraja API service for STK Push integration.

This module provides MPesaService class for interacting with the M-PESA
Daraja API, including OAuth token generation and STK Push initiation.
"""

import base64
import time
from datetime import datetime
from typing import Any, Dict

import httpx

from ..config import settings
from ..utils.logging import get_logger

logger = get_logger(__name__)


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

    async def get_access_token(self) -> str:
        """
        Get OAuth access token with caching.

        Checks if cached token exists and is not expired. If cached and valid,
        returns cached token. Otherwise, generates new token from M-PESA API
        and caches it with expiration timestamp.

        Returns:
            M-PESA OAuth access token

        Raises:
            httpx.HTTPError: If token generation fails
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
            async with httpx.AsyncClient(timeout=30.0) as client:
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

    async def initiate_stk_push(
        self,
        phone_number: str,
        amount: int,
        account_reference: str,
        transaction_desc: str,
    ) -> Dict[str, Any]:
        """
        Initiate M-PESA STK Push request.

        Sends STK Push request to customer's phone to prompt for M-PESA payment.

        Args:
            phone_number: Customer MSISDN (254XXXXXXXXX format)
            amount: Amount in whole KES (no decimals)
            account_reference: Invoice ID or reference
            transaction_desc: Transaction description

        Returns:
            M-PESA API response data

        Raises:
            httpx.HTTPError: If STK Push request fails
            ValueError: If response is invalid
        """
        logger.info(
            "Initiating STK Push",
            extra={
                "phone_number": phone_number,
                "amount": amount,
                "account_reference": account_reference,
            },
        )

        # Get access token
        access_token = await self.get_access_token()

        # Generate timestamp and password
        timestamp = self.generate_timestamp()
        password = self.generate_password(self.shortcode, self.passkey, timestamp)

        # Construct STK Push request payload
        payload = {
            "BusinessShortCode": self.shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": amount,
            "PartyA": phone_number,
            "PartyB": self.shortcode,
            "PhoneNumber": phone_number,
            "CallBackURL": self.callback_url,
            "AccountReference": account_reference,
            "TransactionDesc": transaction_desc,
        }

        # Make STK Push request
        stk_url = f"{self.base_url}/mpesa/stkpush/v1/processrequest"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(stk_url, json=payload, headers=headers)
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