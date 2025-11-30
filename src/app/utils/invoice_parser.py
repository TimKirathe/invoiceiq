"""
Invoice line items parsing and calculation utilities.

This module provides functions to parse line items from user input,
calculate subtotals with optional VAT, and format previews for display.
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Optional
from datetime import date, timedelta
import re

from .phone import validate_phone_number


def parse_line_items(text: str) -> List[Dict]:
    """
    Parse line items from multi-line text input.

    Expected format:
        Item Name - Unit Price - Quantity

    Example:
        Full Home Deep Clean - 1500 - 3
        Kitchen Deep Clean - 800 - 1
        Bathroom Scrub - 600 - 2

    Args:
        text: Multi-line string with line items (one per line)

    Returns:
        List of dictionaries, each containing:
        - name (str): Item name
        - unit_price_cents (int): Unit price in cents
        - quantity (int): Quantity
        - subtotal_cents (int): Calculated subtotal in cents (unit_price × quantity)

    Raises:
        ValueError: If text is empty, or if any line has invalid format or values

    Examples:
        >>> items = parse_line_items("Widget - 100 - 2\\nGadget - 50 - 3")
        >>> len(items)
        2
        >>> items[0]['name']
        'Widget'
        >>> items[0]['unit_price_cents']
        10000
        >>> items[0]['quantity']
        2
        >>> items[0]['subtotal_cents']
        20000
    """
    if not text or not text.strip():
        raise ValueError("Line items text cannot be empty")

    lines = text.strip().split('\n')
    parsed_items = []

    for line_num, line in enumerate(lines, start=1):
        # Skip empty lines (after stripping whitespace)
        line = line.strip()
        if not line:
            continue

        # Split by ' - ' (with spaces around dash)
        parts = [part.strip() for part in line.split(' - ')]

        if len(parts) != 3:
            raise ValueError(
                f"Line {line_num}: Invalid format. "
                f"Expected 'Item - Price - Quantity', got: {line}"
            )

        name, price_str, quantity_str = parts

        # Validate item name
        if not name or len(name) < 2:
            raise ValueError(
                f"Line {line_num}: Item name must be at least 2 characters"
            )

        if len(name) > 100:
            raise ValueError(
                f"Line {line_num}: Item name must be at most 100 characters"
            )

        # Parse and validate unit price
        try:
            # Use Decimal for precise monetary calculations
            unit_price_decimal = Decimal(price_str)

            # Price must be positive
            if unit_price_decimal <= 0:
                raise ValueError(
                    f"Line {line_num}: Price must be positive, got: {price_str}"
                )

            # Convert to cents (multiply by 100)
            unit_price_cents = int(unit_price_decimal * 100)

            # Minimum 1 cent
            if unit_price_cents < 1:
                raise ValueError(
                    f"Line {line_num}: Price must be at least 0.01, got: {price_str}"
                )

        except (ValueError, ArithmeticError) as e:
            if "Price must be" in str(e):
                raise
            raise ValueError(
                f"Line {line_num}: Invalid price format. "
                f"Expected decimal number, got: {price_str}"
            )

        # Parse and validate quantity
        try:
            quantity = int(quantity_str)

            if quantity < 1:
                raise ValueError(
                    f"Line {line_num}: Quantity must be at least 1, got: {quantity_str}"
                )

            if quantity > 10000:
                raise ValueError(
                    f"Line {line_num}: Quantity must be at most 10000, got: {quantity_str}"
                )

        except ValueError as e:
            if "Quantity must be" in str(e):
                raise
            raise ValueError(
                f"Line {line_num}: Invalid quantity format. "
                f"Expected integer, got: {quantity_str}"
            )

        # Calculate subtotal
        subtotal_cents = unit_price_cents * quantity

        parsed_items.append({
            "name": name,
            "unit_price_cents": unit_price_cents,
            "quantity": quantity,
            "subtotal_cents": subtotal_cents
        })

    if not parsed_items:
        raise ValueError("No valid line items found (all lines were empty)")

    return parsed_items


def calculate_invoice_totals(line_items: List[Dict], include_vat: bool = False) -> Dict:
    """
    Calculate subtotal, VAT (if requested), and total from line items.

    VAT is calculated as 16% ADDED ON TOP of the subtotal (VAT Exclusive).
    Formula: VAT = subtotal × 0.16
    Total = Subtotal + VAT

    Args:
        line_items: List of parsed line items (from parse_line_items)
        include_vat: If True, calculate 16% VAT on top of subtotal

    Returns:
        Dictionary with:
        - subtotal_cents (int): Sum of all line item subtotals
        - vat_cents (int): VAT amount (0 if include_vat=False)
        - total_cents (int): Final total (subtotal + VAT)
        - line_items (List[Dict]): Original line items (unchanged)

    Raises:
        ValueError: If line_items is empty

    Examples:
        >>> items = [
        ...     {"name": "Widget", "unit_price_cents": 10000, "quantity": 2, "subtotal_cents": 20000},
        ...     {"name": "Gadget", "unit_price_cents": 5000, "quantity": 3, "subtotal_cents": 15000}
        ... ]
        >>> result = calculate_invoice_totals(items, include_vat=False)
        >>> result['subtotal_cents']
        35000
        >>> result['vat_cents']
        0
        >>> result['total_cents']
        35000

        >>> result = calculate_invoice_totals(items, include_vat=True)
        >>> result['subtotal_cents']
        35000
        >>> result['vat_cents']
        5600
        >>> result['total_cents']
        40600
    """
    if not line_items:
        raise ValueError("Line items cannot be empty")

    # Calculate subtotal
    subtotal_cents = sum(item["subtotal_cents"] for item in line_items)

    # Calculate VAT if requested (16% of subtotal)
    if include_vat:
        # Use Decimal for precise VAT calculation
        subtotal_decimal = Decimal(subtotal_cents)
        vat_decimal = subtotal_decimal * Decimal("0.16")
        # Round to nearest cent using ROUND_HALF_UP (banker's rounding)
        vat_cents = int(vat_decimal.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    else:
        vat_cents = 0

    # Calculate total
    total_cents = subtotal_cents + vat_cents

    return {
        "subtotal_cents": subtotal_cents,
        "vat_cents": vat_cents,
        "total_cents": total_cents,
        "line_items": line_items
    }


def format_line_items_preview(line_items: List[Dict]) -> str:
    """
    Format line items for display in invoice preview.

    Format:
        1) Item Name – Price × Quantity = KES Subtotal
        2) Item Name – Price × Quantity = KES Subtotal
        ...

    Example:
        1) Full Home Deep Clean – 1,500.00 × 3 = KES 4,500.00
        2) Kitchen Deep Clean – 800.00 × 1 = KES 800.00

    Args:
        line_items: List of parsed line items

    Returns:
        Formatted string with numbered line items

    Raises:
        ValueError: If line_items is empty

    Examples:
        >>> items = [
        ...     {"name": "Widget", "unit_price_cents": 10000, "quantity": 2, "subtotal_cents": 20000},
        ...     {"name": "Gadget", "unit_price_cents": 5000, "quantity": 3, "subtotal_cents": 15000}
        ... ]
        >>> print(format_line_items_preview(items))
        1) Widget – 100.00 × 2 = KES 200.00
        2) Gadget – 50.00 × 3 = KES 150.00
    """
    if not line_items:
        raise ValueError("Line items cannot be empty")

    lines = []
    for idx, item in enumerate(line_items, start=1):
        # Convert cents to KES (divide by 100)
        unit_price_kes = item["unit_price_cents"] / 100
        subtotal_kes = item["subtotal_cents"] / 100

        # Format with thousand separators and 2 decimal places
        # Using en-dash (–) instead of hyphen (-)
        line = (
            f"{idx}) {item['name']} – "
            f"{unit_price_kes:,.2f} × {item['quantity']} = "
            f"KES {subtotal_kes:,.2f}"
        )
        lines.append(line)

    return '\n'.join(lines)


def parse_due_date(message: str) -> str:
    """
    Parse due date from merchant input and return formatted string.

    Supports multiple input formats:
    - "0" → "Due on receipt"
    - Relative days (1-365): "7" → "In 7 days (3 Dec 2024)"
    - DD/MM or DD/MM/YYYY: "30/11" → "Due: 30 November 2024"
    - Month names: "25 Dec", "Dec 25" → "Due: 25 December 2024"
    - ISO: "2024-12-25" → "Due: 25 December 2024"

    Args:
        message: Due date in various formats

    Returns:
        Formatted string (format depends on input type)

    Raises:
        ValueError: If date is invalid, in the past, or > 365 days ahead
    """
    if not message or not message.strip():
        raise ValueError("Due date cannot be empty")

    message = message.strip()
    today = date.today()

    # Month names mapping (full and abbreviated, case insensitive)
    MONTH_NAMES = {
        'january': 1, 'jan': 1,
        'february': 2, 'feb': 2,
        'march': 3, 'mar': 3,
        'april': 4, 'apr': 4,
        'may': 5,
        'june': 6, 'jun': 6,
        'july': 7, 'jul': 7,
        'august': 8, 'aug': 8,
        'september': 9, 'sep': 9, 'sept': 9,
        'october': 10, 'oct': 10,
        'november': 11, 'nov': 11,
        'december': 12, 'dec': 12
    }

    # Full month names for output
    FULL_MONTH_NAMES = [
        '', 'January', 'February', 'March', 'April', 'May', 'June',
        'July', 'August', 'September', 'October', 'November', 'December'
    ]

    # Abbreviated month names for output
    ABBREV_MONTH_NAMES = [
        '', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
    ]

    # Special case: "0" means due on receipt
    if message == "0":
        return "Due on receipt"

    # Try to parse as relative days (1-365)
    try:
        days = int(message)

        if days < 0:
            raise ValueError("Relative days cannot be negative")

        if days < 1 or days > 365:
            raise ValueError("Relative days must be in range 1-365")

        # Calculate target date
        target_date = today + timedelta(days=days)

        # Format: "In N days (DD MMM YYYY)"
        day = target_date.day
        month_abbrev = ABBREV_MONTH_NAMES[target_date.month]
        year = target_date.year

        return f"In {days} days ({day} {month_abbrev} {year})"

    except ValueError as e:
        # If it's our custom error, re-raise it
        if "Relative days" in str(e):
            raise
        # Otherwise, it's not a valid integer, continue to other formats
        pass

    # Try ISO format: YYYY-MM-DD
    iso_match = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', message)
    if iso_match:
        year = int(iso_match.group(1))
        month = int(iso_match.group(2))
        day = int(iso_match.group(3))

        try:
            target_date = date(year, month, day)
        except ValueError:
            raise ValueError(f"Invalid date: {message}")

        # Validate not in past
        if target_date < today:
            raise ValueError(f"Date cannot be in the past: {message}")

        # Validate not more than 365 days ahead
        days_ahead = (target_date - today).days
        if days_ahead > 365:
            raise ValueError(f"Date cannot be more than 365 days in the future: {message}")

        # Format: "Due: DD MMMM YYYY"
        month_full = FULL_MONTH_NAMES[target_date.month]
        return f"Due: {target_date.day} {month_full} {target_date.year}"

    # Try DD/MM or DD/MM/YYYY format
    slash_match = re.match(r'^(\d{1,2})/(\d{1,2})(?:/(\d{4}))?$', message)
    if slash_match:
        day = int(slash_match.group(1))
        month = int(slash_match.group(2))
        year_str = slash_match.group(3)

        if year_str:
            year = int(year_str)
        else:
            # No year provided - use current year if date is in future, else next year
            year = today.year
            try:
                test_date = date(year, month, day)
                if test_date < today:
                    year += 1
            except ValueError:
                # Invalid date, will be caught below
                pass

        try:
            target_date = date(year, month, day)
        except ValueError:
            raise ValueError(f"Invalid date: {message}")

        # Validate not in past
        if target_date < today:
            raise ValueError(f"Date cannot be in the past: {message}")

        # Validate not more than 365 days ahead
        days_ahead = (target_date - today).days
        if days_ahead > 365:
            raise ValueError(f"Date cannot be more than 365 days in the future: {message}")

        # Format: "Due: DD MMMM YYYY"
        month_full = FULL_MONTH_NAMES[target_date.month]
        return f"Due: {target_date.day} {month_full} {target_date.year}"

    # Try month name formats
    # Pattern 1: DD MMM[MMM] [YYYY] (e.g., "25 Dec", "25 December", "25 Dec 2024")
    month_day_match = re.match(
        r'^(\d{1,2})\s+([a-zA-Z]+)(?:\s+(\d{4}))?$',
        message,
        re.IGNORECASE
    )

    # Pattern 2: MMM[MMM] DD [YYYY] (e.g., "Dec 25", "December 25", "Dec 25 2024")
    day_month_match = re.match(
        r'^([a-zA-Z]+)\s+(\d{1,2})(?:\s+(\d{4}))?$',
        message,
        re.IGNORECASE
    )

    if month_day_match:
        day = int(month_day_match.group(1))
        month_str = month_day_match.group(2).lower()
        year_str = month_day_match.group(3)

        if month_str not in MONTH_NAMES:
            raise ValueError(f"Invalid month name: {month_day_match.group(2)}")

        month = MONTH_NAMES[month_str]

        if year_str:
            year = int(year_str)
        else:
            # No year provided - use current year if date is in future, else next year
            year = today.year
            try:
                test_date = date(year, month, day)
                if test_date < today:
                    year += 1
            except ValueError:
                # Invalid date, will be caught below
                pass

        try:
            target_date = date(year, month, day)
        except ValueError:
            raise ValueError(f"Invalid date: {message}")

        # Validate not in past
        if target_date < today:
            raise ValueError(f"Date cannot be in the past: {message}")

        # Validate not more than 365 days ahead
        days_ahead = (target_date - today).days
        if days_ahead > 365:
            raise ValueError(f"Date cannot be more than 365 days in the future: {message}")

        # Format: "Due: DD MMMM YYYY"
        month_full = FULL_MONTH_NAMES[target_date.month]
        return f"Due: {target_date.day} {month_full} {target_date.year}"

    elif day_month_match:
        month_str = day_month_match.group(1).lower()
        day = int(day_month_match.group(2))
        year_str = day_month_match.group(3)

        if month_str not in MONTH_NAMES:
            raise ValueError(f"Invalid month name: {day_month_match.group(1)}")

        month = MONTH_NAMES[month_str]

        if year_str:
            year = int(year_str)
        else:
            # No year provided - use current year if date is in future, else next year
            year = today.year
            try:
                test_date = date(year, month, day)
                if test_date < today:
                    year += 1
            except ValueError:
                # Invalid date, will be caught below
                pass

        try:
            target_date = date(year, month, day)
        except ValueError:
            raise ValueError(f"Invalid date: {message}")

        # Validate not in past
        if target_date < today:
            raise ValueError(f"Date cannot be in the past: {message}")

        # Validate not more than 365 days ahead
        days_ahead = (target_date - today).days
        if days_ahead > 365:
            raise ValueError(f"Date cannot be more than 365 days in the future: {message}")

        # Format: "Due: DD MMMM YYYY"
        month_full = FULL_MONTH_NAMES[target_date.month]
        return f"Due: {target_date.day} {month_full} {target_date.year}"

    # If we get here, no format matched
    raise ValueError(
        f"Invalid due date format: {message}. "
        "Supported formats: 0, 1-365 (days), DD/MM[/YYYY], "
        "DD MMM[MMM] [YYYY], MMM[MMM] DD [YYYY], YYYY-MM-DD"
    )


def parse_mpesa_payment_method(method_type: str, details: str) -> Dict:
    """
    Parse M-PESA payment method details.

    Args:
        method_type: Payment method type ("1" for PAYBILL, "2" for TILL, "3" for PHONE)
        details: Payment details string (format varies by method type)

    Returns:
        Dict with payment method details:
        {
            "method_type": "PAYBILL" | "TILL" | "PHONE",
            "paybill_number": str | None,
            "account_number": str | None,
            "till_number": str | None,
            "phone_number": str | None
        }

    Raises:
        ValueError: If method type is invalid or details are malformed

    Examples:
        >>> result = parse_mpesa_payment_method("1", "123456 ACC001")
        >>> result["method_type"]
        'PAYBILL'
        >>> result["paybill_number"]
        '123456'
        >>> result["account_number"]
        'ACC001'

        >>> result = parse_mpesa_payment_method("2", "654321")
        >>> result["method_type"]
        'TILL'
        >>> result["till_number"]
        '654321'

        >>> result = parse_mpesa_payment_method("3", "254712345678")
        >>> result["method_type"]
        'PHONE'
        >>> result["phone_number"]
        '254712345678'
    """
    if not method_type or not method_type.strip():
        raise ValueError("Method type cannot be empty")

    if not details or not details.strip():
        raise ValueError("Payment details cannot be empty")

    method_type = method_type.strip()
    details = details.strip()

    # Validate and convert method_type
    method_type_map = {
        "1": "PAYBILL",
        "2": "TILL",
        "3": "PHONE"
    }

    if method_type not in method_type_map:
        raise ValueError(
            f"Invalid method type: {method_type}. "
            "Expected '1' (PAYBILL), '2' (TILL), or '3' (PHONE)"
        )

    parsed_method_type = method_type_map[method_type]

    # Initialize all fields to None
    result: Dict[str, Optional[str]] = {
        "method_type": parsed_method_type,
        "paybill_number": None,
        "account_number": None,
        "till_number": None,
        "phone_number": None
    }

    # Parse details based on method type
    if parsed_method_type == "PAYBILL":
        # Expected format: "paybill_number account_number" (space or newline separated)
        # Split by whitespace (space or newline)
        parts = details.split()

        if len(parts) != 2:
            raise ValueError(
                "Invalid PAYBILL format. "
                "Expected 'paybill_number account_number' (space or newline separated)"
            )

        paybill_number, account_number = parts

        # Validate paybill number (5-7 digits)
        if not re.match(r'^\d{5,7}$', paybill_number):
            raise ValueError(
                f"Invalid paybill number: {paybill_number}. "
                "Must be 5-7 digits"
            )

        # Validate account number (1-100 alphanumeric characters)
        if not re.match(r'^[a-zA-Z0-9\-]{1,100}$', account_number):
            raise ValueError(
                f"Invalid account number: {account_number}. "
                "Must be 1-100 alphanumeric characters"
            )

        result["paybill_number"] = paybill_number
        result["account_number"] = account_number

    elif parsed_method_type == "TILL":
        # Expected format: "till_number"
        till_number = details.strip()

        # Validate till number (5-7 digits)
        if not re.match(r'^\d{5,7}$', till_number):
            raise ValueError(
                f"Invalid till number: {till_number}. "
                "Must be 5-7 digits"
            )

        result["till_number"] = till_number

    elif parsed_method_type == "PHONE":
        # Expected format: phone number
        phone_number = details.strip()

        # Validate phone number using existing phone validation
        try:
            validated_phone = validate_phone_number(phone_number)
            result["phone_number"] = validated_phone
        except ValueError as e:
            raise ValueError(f"Invalid phone number: {e}")

    return result


def format_line_items_for_template(line_items: List[Dict]) -> str:
    """
    Format line items for WhatsApp template.

    Shows all items if ≤40 chars, otherwise first item + count.

    Format when all items fit:
        Item1 – KES X.XX (xQ); Item2 – KES Y.YY (xQ)

    Format when too long:
        Item1 – KES X.XX (xQ) +N more

    Args:
        line_items: List of parsed line items

    Returns:
        Formatted string for WhatsApp template (≤40 chars if possible)

    Raises:
        ValueError: If line_items is empty

    Examples:
        >>> items = [
        ...     {"name": "Widget", "unit_price_cents": 10000, "quantity": 2, "subtotal_cents": 20000},
        ...     {"name": "Gadget", "unit_price_cents": 5000, "quantity": 3, "subtotal_cents": 15000}
        ... ]
        >>> format_line_items_for_template(items)
        'Widget – KES 100.00 (x2); Gadget – KES 50.00 (x3)'
    """
    if not line_items:
        raise ValueError("Line items cannot be empty")

    # Try formatting all items
    all_items_formatted = "; ".join([
        f"{item['name']} – KES {item['unit_price_cents']/100:,.2f} (x{item['quantity']})"
        for item in line_items
    ])

    # If short enough, return all items
    if len(all_items_formatted) <= 40:
        return all_items_formatted

    # Otherwise, return first item + count
    first_item = line_items[0]
    unit_price_kes = first_item["unit_price_cents"] / 100
    remaining = len(line_items) - 1

    if remaining == 0:
        return f"{first_item['name']} – KES {unit_price_kes:,.2f} (x{first_item['quantity']})"
    else:
        return f"{first_item['name']} – KES {unit_price_kes:,.2f} (x{first_item['quantity']}) +{remaining} more"


def format_mpesa_details(
    method_type: str,
    paybill_number: Optional[str] = None,
    account_number: Optional[str] = None,
    till_number: Optional[str] = None,
    phone_number: Optional[str] = None
) -> str:
    """
    Format M-PESA payment details for WhatsApp template.

    Formats:
    - PAYBILL: "Paybill: XXXXX, Acc: YYYYY"
    - TILL: "Till: XXXXX"
    - PHONE: "Phone: 254XXXXXXXXX"

    Args:
        method_type: Payment method type ("PAYBILL", "TILL", or "PHONE")
        paybill_number: Paybill number (required if method_type is PAYBILL)
        account_number: Account number (required if method_type is PAYBILL)
        till_number: Till number (required if method_type is TILL)
        phone_number: Phone number (required if method_type is PHONE)

    Returns:
        Formatted M-PESA details string

    Raises:
        ValueError: If method_type is invalid or required fields are missing

    Examples:
        >>> format_mpesa_details("PAYBILL", paybill_number="123456", account_number="ACC001")
        'Paybill: 123456, Acc: ACC001'

        >>> format_mpesa_details("TILL", till_number="654321")
        'Till: 654321'

        >>> format_mpesa_details("PHONE", phone_number="254712345678")
        'Phone: 254712345678'
    """
    if not method_type:
        raise ValueError("Method type cannot be empty")

    method_type = method_type.upper()

    if method_type == "PAYBILL":
        if not paybill_number:
            raise ValueError("Paybill number is required for PAYBILL method")
        if not account_number:
            raise ValueError("Account number is required for PAYBILL method")
        return f"Paybill: {paybill_number}, Acc: {account_number}"

    elif method_type == "TILL":
        if not till_number:
            raise ValueError("Till number is required for TILL method")
        return f"Till: {till_number}"

    elif method_type == "PHONE":
        if not phone_number:
            raise ValueError("Phone number is required for PHONE method")
        return f"Phone: {phone_number}"

    else:
        raise ValueError(
            f"Invalid method type: {method_type}. "
            "Expected 'PAYBILL', 'TILL', or 'PHONE'"
        )
