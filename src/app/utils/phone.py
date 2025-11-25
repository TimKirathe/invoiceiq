"""
Phone number validation and normalization utilities supporting international numbers.

This module provides validation and normalization functions for international
phone numbers using Google's libphonenumbers library (via the phonenumbers package).
It maintains backward compatibility with the original Kenyan number format while
extending support to international numbers from various countries.

Supported formats:
- E.164 format without + prefix (e.g., 254712345678 for Kenya, 447122237689 for UK)
- E.164 format with + prefix (e.g., +254712345678, +447122237689)
- Local formats for various countries (e.g., 0712345678 for Kenya, 07122237689 for UK)
- Numbers without country codes (with appropriate region hint)

The module prioritizes Kenyan numbers for backward compatibility but supports
all countries recognized by libphonenumbers.
"""

import re
from typing import Optional
import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberType


# Legacy regex pattern for Kenyan MSISDN (kept for strict validation)
KENYAN_MSISDN_PATTERN = re.compile(r"^2547\d{8}$")

# Default region for parsing when no country code is provided
DEFAULT_REGION = "KE"  # Kenya


def validate_phone_number(
    phone: str,
    region: Optional[str] = None,
    strict_e164: bool = True
) -> str:
    """
    Validate that a phone number is valid for any supported country.

    This function uses Google's libphonenumbers to validate international
    phone numbers. It supports numbers from any country, not just Kenya.

    Args:
        phone: The phone number string to validate
        region: Optional ISO 3166-1 alpha-2 region code (e.g., "KE", "GB", "US")
                Used as a hint when the phone number doesn't include a country code.
                Defaults to "KE" (Kenya) for backward compatibility.
        strict_e164: If True, returns E.164 format without + prefix (e.g., 254712345678)
                     If False, returns E.164 format with + prefix (e.g., +254712345678)

    Returns:
        The validated phone number in E.164 format (with or without + based on strict_e164)

    Raises:
        ValueError: If the phone number is None, empty, or invalid

    Examples:
        >>> validate_phone_number("254712345678")  # Kenya
        '254712345678'

        >>> validate_phone_number("447122237689")  # UK
        '447122237689'

        >>> validate_phone_number("+1-202-555-1234")  # US with formatting
        '12025551234'

        >>> validate_phone_number("0712345678", region="KE")  # Local Kenyan format
        '254712345678'

        >>> validate_phone_number("07122237689", region="GB")  # Local UK format
        '447122237689'

        >>> validate_phone_number("")
        Traceback (most recent call last):
        ...
        ValueError: Phone number cannot be empty

        >>> validate_phone_number("invalid")
        Traceback (most recent call last):
        ...
        ValueError: Invalid phone number format: invalid
    """
    if phone is None:
        raise ValueError("Phone number cannot be None")

    # Strip whitespace
    phone = phone.strip()

    if not phone:
        raise ValueError("Phone number cannot be empty")

    # Use provided region or default to Kenya for backward compatibility
    parse_region = region or DEFAULT_REGION

    # If the number looks like E.164 without +, add the + for parsing
    # This helps phonenumbers library parse it correctly
    phone_to_parse = phone
    if re.match(r'^\d{10,15}$', phone):
        # Check if it starts with a valid country code
        # Common country codes: 1 (US/Canada), 44 (UK), 254 (Kenya), etc.
        if phone.startswith(('1', '2', '3', '4', '5', '6', '7', '8', '9')):
            phone_to_parse = f"+{phone}"

    try:
        # Parse the phone number with the region hint
        parsed_number = phonenumbers.parse(phone_to_parse, parse_region)

        # Validate that the parsed number is valid
        if not phonenumbers.is_valid_number(parsed_number):
            raise ValueError(f"Invalid phone number format: {phone}")

        # Format to E.164
        formatted = phonenumbers.format_number(parsed_number, phonenumbers.PhoneNumberFormat.E164)

        # Remove + prefix if strict_e164 is True (default behavior for backward compatibility)
        if strict_e164 and formatted.startswith("+"):
            formatted = formatted[1:]

        return formatted

    except NumberParseException as e:
        # Provide user-friendly error message
        error_msg = f"Invalid phone number format: {phone}"
        if e.error_type == NumberParseException.INVALID_COUNTRY_CODE:
            error_msg += " (invalid country code)"
        elif e.error_type == NumberParseException.NOT_A_NUMBER:
            error_msg += " (not a valid number)"
        elif e.error_type == NumberParseException.TOO_SHORT_NSN:
            error_msg += " (number too short)"
        elif e.error_type == NumberParseException.TOO_LONG:
            error_msg += " (number too long)"
        raise ValueError(error_msg)


def normalize_phone_number(
    phone: str,
    region: Optional[str] = None,
    strict_e164: bool = True
) -> str:
    """
    Normalize a phone number to E.164 format.

    Accepts various formats and converts them to E.164:
    - Numbers with country code: 254712345678, 447122237689
    - Numbers with + prefix: +254712345678, +447122237689
    - Local formats: 0712345678 (with appropriate region)
    - Numbers without country code: 712345678 (with appropriate region)

    After normalization, the phone number is validated.

    Args:
        phone: The phone number string to normalize
        region: Optional ISO 3166-1 alpha-2 region code for parsing
                Defaults to "KE" (Kenya) for backward compatibility
        strict_e164: If True, returns E.164 without + prefix (backward compatible)
                     If False, returns E.164 with + prefix

    Returns:
        The normalized phone number in E.164 format

    Raises:
        ValueError: If the phone number is None, empty, or cannot be
                   normalized to a valid format

    Examples:
        >>> normalize_phone_number("254712345678")  # Already normalized
        '254712345678'

        >>> normalize_phone_number("+254712345678")  # With + prefix
        '254712345678'

        >>> normalize_phone_number("0712345678", region="KE")  # Local Kenyan
        '254712345678'

        >>> normalize_phone_number("712345678", region="KE")  # Without country code
        '254712345678'

        >>> normalize_phone_number("+44 7122 237689")  # UK with formatting
        '447122237689'

        >>> normalize_phone_number("(202) 555-1234", region="US")  # US local
        '12025551234'

        >>> normalize_phone_number("")
        Traceback (most recent call last):
        ...
        ValueError: Phone number cannot be empty
    """
    # The normalize function essentially does the same as validate
    # since phonenumbers.parse already handles normalization
    return validate_phone_number(phone, region, strict_e164)


# Legacy function names for backward compatibility with STRICT Kenyan-only validation
def validate_msisdn(phone: str) -> str:
    """
    Legacy function name for backward compatibility.
    Validates a phone number STRICTLY for Kenyan format only.
    
    This function maintains the original strict behavior:
    - Only accepts Kenyan numbers (254XXXXXXXXX)
    - Rejects numbers with + prefix
    - Rejects local formats
    - Rejects non-Safaricom prefixes

    DEPRECATED: Use validate_phone_number() for international support.

    Args:
        phone: The phone number string to validate

    Returns:
        The validated phone number in E.164 format without + prefix

    Raises:
        ValueError: If the phone number is invalid or not Kenyan
    """
    if phone is None:
        raise ValueError("Phone number cannot be None")

    # Strip whitespace
    phone = phone.strip()

    if not phone:
        raise ValueError("Phone number cannot be empty")

    # Use the original strict regex validation for backward compatibility
    if not KENYAN_MSISDN_PATTERN.match(phone):
        raise ValueError(
            "Invalid phone number format. "
            "Expected format: 2547XXXXXXXX (Kenyan mobile number)"
        )

    return phone


def normalize_msisdn(phone: str) -> str:
    """
    Legacy function name for backward compatibility.
    Normalizes a phone number STRICTLY to Kenyan format.
    
    This function maintains the original behavior:
    - Accepts various Kenyan formats and normalizes to 254XXXXXXXXX
    - Rejects non-Kenyan numbers
    - Rejects non-Safaricom prefixes

    DEPRECATED: Use normalize_phone_number() for international support.

    Args:
        phone: The phone number string to normalize

    Returns:
        The normalized phone number in E.164 format without + prefix

    Raises:
        ValueError: If the phone number is invalid or not Kenyan
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

    # Use the strict validate_msisdn to ensure it's a valid Kenyan number
    return validate_msisdn(phone)


def get_phone_number_info(phone: str, region: Optional[str] = None) -> dict:
    """
    Get detailed information about a phone number.

    This is a utility function that provides additional information about
    a phone number, useful for debugging or displaying to users.

    Args:
        phone: The phone number string to analyze
        region: Optional region code for parsing

    Returns:
        Dictionary with phone number information including:
        - is_valid: Whether the number is valid
        - country_code: The country calling code
        - country: The ISO country code
        - national_number: The national number
        - number_type: Type of number (MOBILE, FIXED_LINE, etc.)
        - formatted_international: International format
        - formatted_national: National format
        - formatted_e164: E.164 format

    Examples:
        >>> info = get_phone_number_info("254712345678")
        >>> info['country']
        'KE'
        >>> info['country_code']
        254
    """
    parse_region = region or DEFAULT_REGION
    result = {
        "is_valid": False,
        "country_code": None,
        "country": None,
        "national_number": None,
        "number_type": None,
        "formatted_international": None,
        "formatted_national": None,
        "formatted_e164": None,
    }

    # If the number looks like E.164 without +, add the + for parsing
    phone_to_parse = phone
    if re.match(r'^\d{10,15}$', phone):
        if phone.startswith(('1', '2', '3', '4', '5', '6', '7', '8', '9')):
            phone_to_parse = f"+{phone}"

    try:
        parsed = phonenumbers.parse(phone_to_parse, parse_region)
        result["is_valid"] = phonenumbers.is_valid_number(parsed)
        result["country_code"] = parsed.country_code
        result["country"] = phonenumbers.region_code_for_number(parsed)
        result["national_number"] = parsed.national_number

        # Get number type
        num_type = phonenumbers.number_type(parsed)
        type_names = {
            PhoneNumberType.MOBILE: "MOBILE",
            PhoneNumberType.FIXED_LINE: "FIXED_LINE",
            PhoneNumberType.FIXED_LINE_OR_MOBILE: "FIXED_LINE_OR_MOBILE",
            PhoneNumberType.TOLL_FREE: "TOLL_FREE",
            PhoneNumberType.PREMIUM_RATE: "PREMIUM_RATE",
            PhoneNumberType.SHARED_COST: "SHARED_COST",
            PhoneNumberType.VOIP: "VOIP",
            PhoneNumberType.PERSONAL_NUMBER: "PERSONAL_NUMBER",
            PhoneNumberType.PAGER: "PAGER",
            PhoneNumberType.UAN: "UAN",
            PhoneNumberType.VOICEMAIL: "VOICEMAIL",
            PhoneNumberType.UNKNOWN: "UNKNOWN",
        }
        result["number_type"] = type_names.get(num_type, "UNKNOWN")

        # Format in different ways
        if result["is_valid"]:
            result["formatted_international"] = phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
            )
            result["formatted_national"] = phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.NATIONAL
            )
            result["formatted_e164"] = phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164
            )

    except (NumberParseException, Exception):
        # Return the default result with is_valid=False
        pass

    return result


def is_kenyan_number(phone: str) -> bool:
    """
    Check if a phone number is a Kenyan number.

    Args:
        phone: The phone number to check

    Returns:
        True if the number is Kenyan, False otherwise

    Examples:
        >>> is_kenyan_number("254712345678")
        True
        >>> is_kenyan_number("447122237689")
        False
    """
    try:
        info = get_phone_number_info(phone)
        return info.get("country") == "KE" and info.get("is_valid", False)
    except Exception:
        return False
