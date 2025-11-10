"""
MSISDN (phone number) validation utilities for Kenyan phone numbers.

This module provides validation and normalization functions for Kenyan
mobile numbers in the E.164 format (254XXXXXXXXX).
"""

import re


# Regex pattern for valid Kenyan MSISDN (E.164 format without +)
MSISDN_PATTERN = re.compile(r"^2547\d{8}$")


def validate_msisdn(phone: str) -> str:
    """
    Validate that a phone number matches the Kenyan MSISDN format.

    The expected format is: 254XXXXXXXXX (E.164 format without the + prefix)
    Only Safaricom numbers (254-7XX-XXXXXX) are currently supported.

    Args:
        phone: The phone number string to validate

    Returns:
        The validated phone number string

    Raises:
        ValueError: If the phone number is None, empty, or doesn't match
                   the expected format

    Examples:
        >>> validate_msisdn("254712345678")
        '254712345678'

        >>> validate_msisdn("254112345678")  # Invalid - not a 7XX number
        Traceback (most recent call last):
        ...
        ValueError: Invalid phone number format. Expected format: 2547XXXXXXXX (Kenyan mobile number)

        >>> validate_msisdn("")
        Traceback (most recent call last):
        ...
        ValueError: Phone number cannot be empty

        >>> validate_msisdn(None)  # doctest: +SKIP
        Traceback (most recent call last):
        ...
        ValueError: Phone number cannot be None
    """
    if phone is None:
        raise ValueError("Phone number cannot be None")

    # Strip whitespace
    phone = phone.strip()

    if not phone:
        raise ValueError("Phone number cannot be empty")

    if not MSISDN_PATTERN.match(phone):
        raise ValueError(
            "Invalid phone number format. "
            "Expected format: 2547XXXXXXXX (Kenyan mobile number)"
        )

    return phone


def normalize_msisdn(phone: str) -> str:
    """
    Normalize a phone number to the standard MSISDN format.

    Accepts various formats and converts them to 254XXXXXXXXX:
    - 254712345678 (already normalized)
    - +254712345678 (with + prefix)
    - 0712345678 (local format)
    - 712345678 (without country code or leading 0)

    After normalization, the phone number is validated.

    Args:
        phone: The phone number string to normalize

    Returns:
        The normalized phone number in 254XXXXXXXXX format

    Raises:
        ValueError: If the phone number is None, empty, or cannot be
                   normalized to a valid format

    Examples:
        >>> normalize_msisdn("254712345678")
        '254712345678'

        >>> normalize_msisdn("+254712345678")
        '254712345678'

        >>> normalize_msisdn("0712345678")
        '254712345678'

        >>> normalize_msisdn("712345678")
        '254712345678'

        >>> normalize_msisdn("0112345678")  # Invalid - not a 7XX number
        Traceback (most recent call last):
        ...
        ValueError: Invalid phone number format. Expected format: 2547XXXXXXXX (Kenyan mobile number)

        >>> normalize_msisdn("  +254712345678  ")  # With whitespace
        '254712345678'

        >>> normalize_msisdn("")
        Traceback (most recent call last):
        ...
        ValueError: Phone number cannot be empty
    """
    if phone is None:
        raise ValueError("Phone number cannot be None")

    # Strip whitespace
    phone = phone.strip()

    if not phone:
        raise ValueError("Phone number cannot be empty")

    # Remove + prefix if present
    if phone.startswith("+"):
        phone = phone[1:]

    # Convert local format (0XXXXXXXXX) to international (254XXXXXXXXX)
    if phone.startswith("0") and len(phone) == 10:
        phone = "254" + phone[1:]

    # Add country code if missing (assuming 9 digits starting with 7)
    if not phone.startswith("254") and len(phone) == 9 and phone.startswith("7"):
        phone = "254" + phone

    # Validate the normalized phone number
    return validate_msisdn(phone)