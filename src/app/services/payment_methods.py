"""
Payment methods service for InvoiceIQ.

Provides CRUD operations for managing merchant payment methods.
Each merchant can have multiple payment methods (PAYBILL, TILL, PHONE)
with one designated as the default.
"""

import uuid
from typing import List, Dict, Optional
from datetime import datetime

from supabase import Client

from ..utils.logging import get_logger

logger = get_logger(__name__)


def save_payment_method(
    merchant_msisdn: str,
    method_data: Dict,
    supabase: Client,
    is_default: bool = False
) -> str:
    """
    Save a new payment method for a merchant.

    Args:
        merchant_msisdn: Merchant's phone number
        method_data: Dictionary with payment method details (from parse_mpesa_payment_method)
        supabase: Supabase client instance
        is_default: Whether this should be the default payment method

    Returns:
        The ID of the created payment method

    Raises:
        ValueError: If method_data is invalid
        Exception: If database operation fails

    Example:
        >>> method_data = {
        ...     "method_type": "PAYBILL",
        ...     "paybill_number": "123456",
        ...     "account_number": "ACC001",
        ...     "till_number": None,
        ...     "phone_number": None
        ... }
        >>> method_id = save_payment_method("254712345678", method_data, supabase)
    """
    if not merchant_msisdn or not merchant_msisdn.strip():
        raise ValueError("Merchant MSISDN cannot be empty")

    if not method_data:
        raise ValueError("Method data cannot be empty")

    # Validate required fields
    if "method_type" not in method_data:
        raise ValueError("method_type is required in method_data")

    method_type = method_data["method_type"]
    if method_type not in ("PAYBILL", "TILL", "PHONE"):
        raise ValueError(f"Invalid method_type: {method_type}")

    # Generate unique ID
    method_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    # If this is set as default, unset any existing defaults for this merchant
    if is_default:
        try:
            supabase.table("merchant_payment_methods").update({
                "is_default": False,
                "updated_at": now
            }).eq("merchant_msisdn", merchant_msisdn).eq("is_default", True).execute()
        except Exception as e:
            logger.error(
                "Failed to unset existing default payment methods",
                extra={
                    "merchant_msisdn": merchant_msisdn,
                    "error": str(e)
                },
                exc_info=True
            )
            raise

    # Prepare insert data
    insert_data = {
        "id": method_id,
        "merchant_msisdn": merchant_msisdn,
        "method_type": method_type,
        "paybill_number": method_data.get("paybill_number"),
        "account_number": method_data.get("account_number"),
        "till_number": method_data.get("till_number"),
        "phone_number": method_data.get("phone_number"),
        "is_default": is_default,
        "created_at": now,
        "updated_at": now
    }

    try:
        supabase.table("merchant_payment_methods").insert(insert_data).execute()
        logger.info(
            "Payment method saved successfully",
            extra={
                "method_id": method_id,
                "merchant_msisdn": merchant_msisdn,
                "method_type": method_type,
                "is_default": is_default
            }
        )
        return method_id
    except Exception as e:
        logger.error(
            "Failed to save payment method",
            extra={
                "merchant_msisdn": merchant_msisdn,
                "method_type": method_type,
                "error": str(e)
            },
            exc_info=True
        )
        raise


def get_payment_methods(merchant_msisdn: str, supabase: Client) -> List[Dict]:
    """
    Get all payment methods for a merchant.

    Args:
        merchant_msisdn: Merchant's phone number
        supabase: Supabase client instance

    Returns:
        List of payment method dictionaries, sorted by created_at (newest first)

    Raises:
        ValueError: If merchant_msisdn is empty
        Exception: If database operation fails

    Example:
        >>> methods = get_payment_methods("254712345678", supabase)
        >>> len(methods)
        2
    """
    if not merchant_msisdn or not merchant_msisdn.strip():
        raise ValueError("Merchant MSISDN cannot be empty")

    try:
        response = (
            supabase.table("merchant_payment_methods")
            .select("*")
            .eq("merchant_msisdn", merchant_msisdn)
            .order("created_at", desc=True)
            .execute()
        )
        logger.debug(
            "Retrieved payment methods",
            extra={
                "merchant_msisdn": merchant_msisdn,
                "count": len(response.data)
            }
        )
        return response.data
    except Exception as e:
        logger.error(
            "Failed to retrieve payment methods",
            extra={
                "merchant_msisdn": merchant_msisdn,
                "error": str(e)
            },
            exc_info=True
        )
        raise


def get_default_payment_method(
    merchant_msisdn: str,
    supabase: Client
) -> Optional[Dict]:
    """
    Get the default payment method for a merchant.

    Args:
        merchant_msisdn: Merchant's phone number
        supabase: Supabase client instance

    Returns:
        Payment method dictionary if default exists, None otherwise

    Raises:
        ValueError: If merchant_msisdn is empty
        Exception: If database operation fails

    Example:
        >>> default = get_default_payment_method("254712345678", supabase)
        >>> default["method_type"]
        'PAYBILL'
    """
    if not merchant_msisdn or not merchant_msisdn.strip():
        raise ValueError("Merchant MSISDN cannot be empty")

    try:
        response = (
            supabase.table("merchant_payment_methods")
            .select("*")
            .eq("merchant_msisdn", merchant_msisdn)
            .eq("is_default", True)
            .execute()
        )

        default_method = response.data[0] if response.data else None

        if default_method:
            logger.debug(
                "Retrieved default payment method",
                extra={
                    "merchant_msisdn": merchant_msisdn,
                    "method_id": default_method["id"],
                    "method_type": default_method["method_type"]
                }
            )
        else:
            logger.debug(
                "No default payment method found",
                extra={"merchant_msisdn": merchant_msisdn}
            )

        return default_method
    except Exception as e:
        logger.error(
            "Failed to retrieve default payment method",
            extra={
                "merchant_msisdn": merchant_msisdn,
                "error": str(e)
            },
            exc_info=True
        )
        raise


def update_payment_method(
    method_id: str,
    updates: Dict,
    supabase: Client
) -> bool:
    """
    Update an existing payment method.

    Args:
        method_id: Payment method ID
        updates: Dictionary with fields to update
        supabase: Supabase client instance

    Returns:
        True if update was successful, False if method not found

    Raises:
        ValueError: If method_id or updates are invalid
        Exception: If database operation fails

    Example:
        >>> success = update_payment_method(
        ...     "method-id-123",
        ...     {"account_number": "NEWACC"},
        ...     supabase
        ... )
    """
    if not method_id or not method_id.strip():
        raise ValueError("Method ID cannot be empty")

    if not updates:
        raise ValueError("Updates dictionary cannot be empty")

    # Add updated_at timestamp
    updates["updated_at"] = datetime.utcnow().isoformat()

    try:
        response = (
            supabase.table("merchant_payment_methods")
            .update(updates)
            .eq("id", method_id)
            .execute()
        )

        if response.data:
            logger.info(
                "Payment method updated successfully",
                extra={
                    "method_id": method_id,
                    "updated_fields": list(updates.keys())
                }
            )
            return True
        else:
            logger.warning(
                "Payment method not found for update",
                extra={"method_id": method_id}
            )
            return False
    except Exception as e:
        logger.error(
            "Failed to update payment method",
            extra={
                "method_id": method_id,
                "error": str(e)
            },
            exc_info=True
        )
        raise


def delete_payment_method(method_id: str, supabase: Client) -> bool:
    """
    Delete a payment method.

    Args:
        method_id: Payment method ID
        supabase: Supabase client instance

    Returns:
        True if deletion was successful, False if method not found

    Raises:
        ValueError: If method_id is empty
        Exception: If database operation fails

    Example:
        >>> success = delete_payment_method("method-id-123", supabase)
    """
    if not method_id or not method_id.strip():
        raise ValueError("Method ID cannot be empty")

    try:
        response = (
            supabase.table("merchant_payment_methods")
            .delete()
            .eq("id", method_id)
            .execute()
        )

        if response.data:
            logger.info(
                "Payment method deleted successfully",
                extra={"method_id": method_id}
            )
            return True
        else:
            logger.warning(
                "Payment method not found for deletion",
                extra={"method_id": method_id}
            )
            return False
    except Exception as e:
        logger.error(
            "Failed to delete payment method",
            extra={
                "method_id": method_id,
                "error": str(e)
            },
            exc_info=True
        )
        raise


def set_default_payment_method(
    merchant_msisdn: str,
    method_id: str,
    supabase: Client
) -> bool:
    """
    Set a payment method as the default for a merchant.

    This will unset any existing default and set the specified method as default.

    Args:
        merchant_msisdn: Merchant's phone number
        method_id: Payment method ID to set as default
        supabase: Supabase client instance

    Returns:
        True if successful, False if method not found

    Raises:
        ValueError: If merchant_msisdn or method_id are empty
        Exception: If database operation fails

    Example:
        >>> success = set_default_payment_method(
        ...     "254712345678",
        ...     "method-id-123",
        ...     supabase
        ... )
    """
    if not merchant_msisdn or not merchant_msisdn.strip():
        raise ValueError("Merchant MSISDN cannot be empty")

    if not method_id or not method_id.strip():
        raise ValueError("Method ID cannot be empty")

    now = datetime.utcnow().isoformat()

    try:
        # First, verify the method exists and belongs to this merchant
        check_response = (
            supabase.table("merchant_payment_methods")
            .select("id")
            .eq("id", method_id)
            .eq("merchant_msisdn", merchant_msisdn)
            .execute()
        )

        if not check_response.data:
            logger.warning(
                "Payment method not found or doesn't belong to merchant",
                extra={
                    "method_id": method_id,
                    "merchant_msisdn": merchant_msisdn
                }
            )
            return False

        # Unset existing defaults for this merchant
        supabase.table("merchant_payment_methods").update({
            "is_default": False,
            "updated_at": now
        }).eq("merchant_msisdn", merchant_msisdn).eq("is_default", True).execute()

        # Set new default
        response = (
            supabase.table("merchant_payment_methods")
            .update({
                "is_default": True,
                "updated_at": now
            })
            .eq("id", method_id)
            .execute()
        )

        if response.data:
            logger.info(
                "Default payment method updated successfully",
                extra={
                    "merchant_msisdn": merchant_msisdn,
                    "method_id": method_id
                }
            )
            return True
        else:
            logger.warning(
                "Failed to set default payment method",
                extra={
                    "merchant_msisdn": merchant_msisdn,
                    "method_id": method_id
                }
            )
            return False
    except Exception as e:
        logger.error(
            "Failed to set default payment method",
            extra={
                "merchant_msisdn": merchant_msisdn,
                "method_id": method_id,
                "error": str(e)
            },
            exc_info=True
        )
        raise