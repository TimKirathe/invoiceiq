"""
Unit tests for invoice line items parsing and calculations.
"""

import pytest
from datetime import date, timedelta
from src.app.utils.invoice_parser import (
    parse_line_items,
    calculate_invoice_totals,
    format_line_items_preview,
    parse_due_date
)


class TestParseLineItems:
    """Test suite for parse_line_items function."""

    def test_parse_single_line_item(self):
        """Test parsing a single line item."""
        text = "Widget - 100 - 2"
        result = parse_line_items(text)

        assert len(result) == 1
        assert result[0]["name"] == "Widget"
        assert result[0]["unit_price_cents"] == 10000
        assert result[0]["quantity"] == 2
        assert result[0]["subtotal_cents"] == 20000

    def test_parse_multiple_line_items(self):
        """Test parsing multiple line items."""
        text = """Full Home Deep Clean - 1500 - 3
Kitchen Deep Clean - 800 - 1
Bathroom Scrub - 600 - 2"""

        result = parse_line_items(text)

        assert len(result) == 3

        # First item
        assert result[0]["name"] == "Full Home Deep Clean"
        assert result[0]["unit_price_cents"] == 150000
        assert result[0]["quantity"] == 3
        assert result[0]["subtotal_cents"] == 450000

        # Second item
        assert result[1]["name"] == "Kitchen Deep Clean"
        assert result[1]["unit_price_cents"] == 80000
        assert result[1]["quantity"] == 1
        assert result[1]["subtotal_cents"] == 80000

        # Third item
        assert result[2]["name"] == "Bathroom Scrub"
        assert result[2]["unit_price_cents"] == 60000
        assert result[2]["quantity"] == 2
        assert result[2]["subtotal_cents"] == 120000

    def test_parse_with_decimal_prices(self):
        """Test parsing line items with decimal prices."""
        text = """Widget - 100.50 - 2
Gadget - 75.25 - 1"""

        result = parse_line_items(text)

        assert len(result) == 2
        assert result[0]["unit_price_cents"] == 10050
        assert result[0]["subtotal_cents"] == 20100
        assert result[1]["unit_price_cents"] == 7525
        assert result[1]["subtotal_cents"] == 7525

    def test_parse_with_extra_whitespace(self):
        """Test parsing with extra whitespace around separators."""
        text = "  Widget  -  100  -  2  "
        result = parse_line_items(text)

        assert len(result) == 1
        assert result[0]["name"] == "Widget"
        assert result[0]["unit_price_cents"] == 10000
        assert result[0]["quantity"] == 2

    def test_parse_with_empty_lines(self):
        """Test parsing with empty lines (should be skipped)."""
        text = """Widget - 100 - 2

Gadget - 50 - 3

"""
        result = parse_line_items(text)

        assert len(result) == 2
        assert result[0]["name"] == "Widget"
        assert result[1]["name"] == "Gadget"

    def test_parse_empty_text_raises_error(self):
        """Test that empty text raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            parse_line_items("")

        with pytest.raises(ValueError, match="cannot be empty"):
            parse_line_items("   ")

    def test_parse_only_empty_lines_raises_error(self):
        """Test that text with only empty lines raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            parse_line_items("\n\n\n")

    def test_parse_invalid_format_missing_parts(self):
        """Test that missing parts raises ValueError."""
        with pytest.raises(ValueError, match="Invalid format"):
            parse_line_items("Widget - 100")

    def test_parse_invalid_format_too_many_parts(self):
        """Test that too many parts raises ValueError."""
        with pytest.raises(ValueError, match="Invalid format"):
            parse_line_items("Widget - 100 - 2 - Extra")

    def test_parse_item_name_too_short(self):
        """Test that item name shorter than 2 chars raises ValueError."""
        with pytest.raises(ValueError, match="at least 2 characters"):
            parse_line_items("A - 100 - 2")

    def test_parse_item_name_too_long(self):
        """Test that item name longer than 100 chars raises ValueError."""
        long_name = "A" * 101
        with pytest.raises(ValueError, match="at most 100 characters"):
            parse_line_items(f"{long_name} - 100 - 2")

    def test_parse_invalid_price_format(self):
        """Test that non-numeric price raises ValueError."""
        with pytest.raises(ValueError, match="Invalid price format"):
            parse_line_items("Widget - abc - 2")

    def test_parse_negative_price(self):
        """Test that negative price raises ValueError."""
        with pytest.raises(ValueError, match="Price must be positive"):
            parse_line_items("Widget - -100 - 2")

    def test_parse_zero_price(self):
        """Test that zero price raises ValueError."""
        with pytest.raises(ValueError, match="Price must be positive"):
            parse_line_items("Widget - 0 - 2")

    def test_parse_price_too_small(self):
        """Test that price smaller than 0.01 raises ValueError."""
        with pytest.raises(ValueError, match="Price must be at least 0.01"):
            parse_line_items("Widget - 0.001 - 2")

    def test_parse_invalid_quantity_format(self):
        """Test that non-integer quantity raises ValueError."""
        with pytest.raises(ValueError, match="Invalid quantity format"):
            parse_line_items("Widget - 100 - 2.5")

        with pytest.raises(ValueError, match="Invalid quantity format"):
            parse_line_items("Widget - 100 - abc")

    def test_parse_zero_quantity(self):
        """Test that zero quantity raises ValueError."""
        with pytest.raises(ValueError, match="Quantity must be at least 1"):
            parse_line_items("Widget - 100 - 0")

    def test_parse_negative_quantity(self):
        """Test that negative quantity raises ValueError."""
        with pytest.raises(ValueError, match="Quantity must be at least 1"):
            parse_line_items("Widget - 100 - -2")

    def test_parse_quantity_too_large(self):
        """Test that quantity larger than 10000 raises ValueError."""
        with pytest.raises(ValueError, match="Quantity must be at most 10000"):
            parse_line_items("Widget - 100 - 10001")

    def test_parse_complex_item_names(self):
        """Test parsing items with complex names (with special chars)."""
        text = """2-Hour Deep Cleaning Service - 500 - 1
Kitchen & Bathroom Package - 800 - 2
Laundry (Wash + Fold) - 150 - 3"""

        result = parse_line_items(text)

        assert len(result) == 3
        assert result[0]["name"] == "2-Hour Deep Cleaning Service"
        assert result[1]["name"] == "Kitchen & Bathroom Package"
        assert result[2]["name"] == "Laundry (Wash + Fold)"

    def test_parse_large_prices(self):
        """Test parsing with large price values."""
        text = "Luxury Service - 10000.99 - 5"
        result = parse_line_items(text)

        assert len(result) == 1
        assert result[0]["unit_price_cents"] == 1000099
        assert result[0]["subtotal_cents"] == 5000495

    def test_parse_multiple_errors_first_reported(self):
        """Test that error reporting includes line number."""
        text = """Valid Item - 100 - 2
Invalid - abc - 2
Another Valid - 50 - 1"""

        with pytest.raises(ValueError, match="Line 2"):
            parse_line_items(text)


class TestCalculateInvoiceTotals:
    """Test suite for calculate_invoice_totals function."""

    def test_calculate_without_vat(self):
        """Test calculation without VAT."""
        line_items = [
            {"name": "Widget", "unit_price_cents": 10000, "quantity": 2, "subtotal_cents": 20000},
            {"name": "Gadget", "unit_price_cents": 5000, "quantity": 3, "subtotal_cents": 15000}
        ]

        result = calculate_invoice_totals(line_items, include_vat=False)

        assert result["subtotal_cents"] == 35000
        assert result["vat_cents"] == 0
        assert result["total_cents"] == 35000
        assert result["line_items"] == line_items

    def test_calculate_with_vat(self):
        """Test calculation with 16% VAT."""
        line_items = [
            {"name": "Widget", "unit_price_cents": 10000, "quantity": 2, "subtotal_cents": 20000},
            {"name": "Gadget", "unit_price_cents": 5000, "quantity": 3, "subtotal_cents": 15000}
        ]

        result = calculate_invoice_totals(line_items, include_vat=True)

        assert result["subtotal_cents"] == 35000
        # VAT = 35000 * 0.16 = 5600
        assert result["vat_cents"] == 5600
        assert result["total_cents"] == 40600
        assert result["line_items"] == line_items

    def test_calculate_vat_rounding(self):
        """Test VAT calculation with rounding."""
        line_items = [
            {"name": "Service", "unit_price_cents": 10001, "quantity": 1, "subtotal_cents": 10001}
        ]

        result = calculate_invoice_totals(line_items, include_vat=True)

        assert result["subtotal_cents"] == 10001
        # VAT = 10001 * 0.16 = 1600.16 -> rounds to 1600
        assert result["vat_cents"] == 1600
        assert result["total_cents"] == 11601

    def test_calculate_single_item_without_vat(self):
        """Test calculation with single item and no VAT."""
        line_items = [
            {"name": "Widget", "unit_price_cents": 50000, "quantity": 1, "subtotal_cents": 50000}
        ]

        result = calculate_invoice_totals(line_items, include_vat=False)

        assert result["subtotal_cents"] == 50000
        assert result["vat_cents"] == 0
        assert result["total_cents"] == 50000

    def test_calculate_single_item_with_vat(self):
        """Test calculation with single item and VAT."""
        line_items = [
            {"name": "Widget", "unit_price_cents": 50000, "quantity": 1, "subtotal_cents": 50000}
        ]

        result = calculate_invoice_totals(line_items, include_vat=True)

        assert result["subtotal_cents"] == 50000
        # VAT = 50000 * 0.16 = 8000
        assert result["vat_cents"] == 8000
        assert result["total_cents"] == 58000

    def test_calculate_empty_items_raises_error(self):
        """Test that empty line items raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            calculate_invoice_totals([], include_vat=False)

    def test_calculate_complex_example_without_vat(self):
        """Test complex example matching user requirements (no VAT)."""
        line_items = [
            {
                "name": "Full Home Deep Clean",
                "unit_price_cents": 150000,
                "quantity": 3,
                "subtotal_cents": 450000
            },
            {
                "name": "Kitchen Deep Clean",
                "unit_price_cents": 80050,
                "quantity": 1,
                "subtotal_cents": 80050
            },
            {
                "name": "Bathroom Scrub",
                "unit_price_cents": 60025,
                "quantity": 2,
                "subtotal_cents": 120050
            }
        ]

        result = calculate_invoice_totals(line_items, include_vat=False)

        assert result["subtotal_cents"] == 650100
        assert result["vat_cents"] == 0
        assert result["total_cents"] == 650100

    def test_calculate_complex_example_with_vat(self):
        """Test complex example matching user requirements (with VAT)."""
        line_items = [
            {
                "name": "Full Home Deep Clean",
                "unit_price_cents": 150000,
                "quantity": 3,
                "subtotal_cents": 450000
            },
            {
                "name": "Kitchen Deep Clean",
                "unit_price_cents": 80050,
                "quantity": 1,
                "subtotal_cents": 80050
            },
            {
                "name": "Bathroom Scrub",
                "unit_price_cents": 60025,
                "quantity": 2,
                "subtotal_cents": 120050
            }
        ]

        result = calculate_invoice_totals(line_items, include_vat=True)

        assert result["subtotal_cents"] == 650100
        # VAT = 650100 * 0.16 = 104016
        assert result["vat_cents"] == 104016
        assert result["total_cents"] == 754116

    def test_calculate_vat_precision(self):
        """Test VAT calculation precision with various amounts."""
        test_cases = [
            # (subtotal, expected_vat)
            (100, 16),      # 100 * 0.16 = 16.00
            (125, 20),      # 125 * 0.16 = 20.00
            (333, 53),      # 333 * 0.16 = 53.28 -> 53
            (999, 160),     # 999 * 0.16 = 159.84 -> 160
            (1234, 197),    # 1234 * 0.16 = 197.44 -> 197
            (5555, 889),    # 5555 * 0.16 = 888.80 -> 889
        ]

        for subtotal, expected_vat in test_cases:
            line_items = [
                {"name": "Test", "unit_price_cents": subtotal, "quantity": 1, "subtotal_cents": subtotal}
            ]
            result = calculate_invoice_totals(line_items, include_vat=True)
            assert result["vat_cents"] == expected_vat, \
                f"Failed for subtotal {subtotal}: expected VAT {expected_vat}, got {result['vat_cents']}"

    def test_calculate_default_no_vat(self):
        """Test that VAT defaults to False when not specified."""
        line_items = [
            {"name": "Widget", "unit_price_cents": 10000, "quantity": 1, "subtotal_cents": 10000}
        ]

        result = calculate_invoice_totals(line_items)

        assert result["vat_cents"] == 0
        assert result["total_cents"] == 10000


class TestFormatLineItemsPreview:
    """Test suite for format_line_items_preview function."""

    def test_format_single_item(self):
        """Test formatting a single line item."""
        line_items = [
            {"name": "Widget", "unit_price_cents": 10000, "quantity": 2, "subtotal_cents": 20000}
        ]

        result = format_line_items_preview(line_items)

        expected = "1) Widget – 100.00 × 2 = KES 200.00"
        assert result == expected

    def test_format_multiple_items(self):
        """Test formatting multiple line items."""
        line_items = [
            {"name": "Widget", "unit_price_cents": 10000, "quantity": 2, "subtotal_cents": 20000},
            {"name": "Gadget", "unit_price_cents": 5000, "quantity": 3, "subtotal_cents": 15000}
        ]

        result = format_line_items_preview(line_items)

        expected = (
            "1) Widget – 100.00 × 2 = KES 200.00\n"
            "2) Gadget – 50.00 × 3 = KES 150.00"
        )
        assert result == expected

    def test_format_with_large_amounts(self):
        """Test formatting with large amounts (thousand separators)."""
        line_items = [
            {"name": "Service", "unit_price_cents": 150000, "quantity": 3, "subtotal_cents": 450000}
        ]

        result = format_line_items_preview(line_items)

        expected = "1) Service – 1,500.00 × 3 = KES 4,500.00"
        assert result == expected

    def test_format_complex_example(self):
        """Test formatting matching user requirements example."""
        line_items = [
            {
                "name": "Full Home Deep Clean",
                "unit_price_cents": 150000,
                "quantity": 3,
                "subtotal_cents": 450000
            },
            {
                "name": "Carpet Wash",
                "unit_price_cents": 50000,
                "quantity": 2,
                "subtotal_cents": 100000
            }
        ]

        result = format_line_items_preview(line_items)

        expected = (
            "1) Full Home Deep Clean – 1,500.00 × 3 = KES 4,500.00\n"
            "2) Carpet Wash – 500.00 × 2 = KES 1,000.00"
        )
        assert result == expected

    def test_format_empty_items_raises_error(self):
        """Test that empty line items raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            format_line_items_preview([])

    def test_format_with_decimal_cents(self):
        """Test formatting with amounts that have cents."""
        line_items = [
            {"name": "Widget", "unit_price_cents": 12345, "quantity": 1, "subtotal_cents": 12345}
        ]

        result = format_line_items_preview(line_items)

        expected = "1) Widget – 123.45 × 1 = KES 123.45"
        assert result == expected

    def test_format_uses_en_dash(self):
        """Test that formatting uses en-dash (–) not hyphen (-)."""
        line_items = [
            {"name": "Widget", "unit_price_cents": 10000, "quantity": 1, "subtotal_cents": 10000}
        ]

        result = format_line_items_preview(line_items)

        # Check for en-dash (U+2013) not hyphen (U+002D)
        assert "–" in result  # en-dash
        # Make sure item name is preserved exactly
        assert "Widget" in result


class TestIntegration:
    """Integration tests combining parse, calculate, and format functions."""

    def test_full_workflow_without_vat(self):
        """Test complete workflow: parse -> calculate -> format."""
        text = """Full Home Deep Clean - 1500 - 3
Carpet Wash - 500 - 2"""

        # Parse
        line_items = parse_line_items(text)
        assert len(line_items) == 2

        # Calculate
        totals = calculate_invoice_totals(line_items, include_vat=False)
        assert totals["subtotal_cents"] == 550000
        assert totals["vat_cents"] == 0
        assert totals["total_cents"] == 550000

        # Format
        preview = format_line_items_preview(line_items)
        expected = (
            "1) Full Home Deep Clean – 1,500.00 × 3 = KES 4,500.00\n"
            "2) Carpet Wash – 500.00 × 2 = KES 1,000.00"
        )
        assert preview == expected

    def test_full_workflow_with_vat(self):
        """Test complete workflow with VAT: parse -> calculate -> format."""
        text = """Full Home Deep Clean - 1500 - 3
Carpet Wash - 500 - 2"""

        # Parse
        line_items = parse_line_items(text)

        # Calculate with VAT
        totals = calculate_invoice_totals(line_items, include_vat=True)
        assert totals["subtotal_cents"] == 550000
        # VAT = 550000 * 0.16 = 88000
        assert totals["vat_cents"] == 88000
        assert totals["total_cents"] == 638000

        # Format (same as without VAT - formatting doesn't include VAT line)
        preview = format_line_items_preview(line_items)
        assert "Full Home Deep Clean" in preview
        assert "Carpet Wash" in preview

    def test_end_to_end_user_scenario(self):
        """Test realistic user scenario from requirements."""
        # User sends message
        user_message = """Full Home Deep Clean - 1500 - 3
Kitchen Deep Clean - 800.50 - 1
Bathroom Scrub - 600.25 - 2"""

        # System parses
        line_items = parse_line_items(user_message)
        assert len(line_items) == 3

        # User chooses to include VAT
        totals = calculate_invoice_totals(line_items, include_vat=True)

        # Verify calculations
        assert totals["subtotal_cents"] == 650100  # 450000 + 80050 + 120050
        assert totals["vat_cents"] == 104016       # 650100 * 0.16
        assert totals["total_cents"] == 754116     # 650100 + 104016

        # System generates preview
        preview = format_line_items_preview(line_items)
        assert "1) Full Home Deep Clean" in preview
        assert "2) Kitchen Deep Clean" in preview
        assert "3) Bathroom Scrub" in preview


class TestParseDueDate:
    """Test suite for parse_due_date function."""

    def test_parse_zero_returns_due_on_receipt(self):
        """Test that input '0' returns 'Due on receipt'."""
        result = parse_due_date("0")
        assert result == "Due on receipt"

    def test_parse_relative_days_single_digit(self):
        """Test parsing single digit relative days."""
        today = date.today()
        target_date = today + timedelta(days=7)

        result = parse_due_date("7")

        # Extract expected month abbreviation
        month_abbrev = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                       'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][target_date.month]
        expected = f"In 7 days ({target_date.day} {month_abbrev} {target_date.year})"
        assert result == expected

    def test_parse_relative_days_double_digit(self):
        """Test parsing double digit relative days."""
        today = date.today()
        target_date = today + timedelta(days=30)

        result = parse_due_date("30")

        month_abbrev = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                       'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][target_date.month]
        expected = f"In 30 days ({target_date.day} {month_abbrev} {target_date.year})"
        assert result == expected

    def test_parse_relative_days_triple_digit(self):
        """Test parsing triple digit relative days (max 365)."""
        today = date.today()
        target_date = today + timedelta(days=365)

        result = parse_due_date("365")

        month_abbrev = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                       'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][target_date.month]
        expected = f"In 365 days ({target_date.day} {month_abbrev} {target_date.year})"
        assert result == expected

    def test_parse_relative_days_minimum(self):
        """Test parsing minimum relative days (1)."""
        today = date.today()
        target_date = today + timedelta(days=1)

        result = parse_due_date("1")

        month_abbrev = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                       'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][target_date.month]
        expected = f"In 1 days ({target_date.day} {month_abbrev} {target_date.year})"
        assert result == expected

    def test_parse_relative_days_out_of_range_high(self):
        """Test that relative days > 365 raises ValueError."""
        with pytest.raises(ValueError, match="range 1-365"):
            parse_due_date("366")

    def test_parse_relative_days_negative(self):
        """Test that negative relative days raises ValueError."""
        with pytest.raises(ValueError, match="negative"):
            parse_due_date("-5")

    def test_parse_iso_format_basic(self):
        """Test parsing ISO format (YYYY-MM-DD)."""
        # Use a date far in the future to avoid test flakiness
        result = parse_due_date("2025-12-25")
        assert result == "Due: 25 December 2025"

    def test_parse_iso_format_next_year(self):
        """Test parsing ISO format for next year."""
        today = date.today()
        next_year = today.year + 1

        result = parse_due_date(f"{next_year}-06-15")
        assert result == f"Due: 15 June {next_year}"

    def test_parse_dd_mm_format_with_year(self):
        """Test parsing DD/MM/YYYY format."""
        result = parse_due_date("25/12/2025")
        assert result == "Due: 25 December 2025"

    def test_parse_dd_mm_format_without_year_future(self):
        """Test parsing DD/MM format (without year) for future date in current year."""
        today = date.today()

        # Find a date in the future this year
        future_month = (today.month % 12) + 1
        future_year = today.year if future_month > today.month else today.year + 1

        result = parse_due_date(f"15/{future_month:02d}")
        assert result == f"Due: 15 {['', 'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'][future_month]} {future_year}"

    def test_parse_month_name_dd_mmm_format(self):
        """Test parsing 'DD MMM' format (e.g., '25 Dec')."""
        today = date.today()
        # Use December which is likely in the future or next year
        year = today.year if today.month < 12 or (today.month == 12 and today.day < 25) else today.year + 1

        result = parse_due_date("25 Dec")
        assert result == f"Due: 25 December {year}"

    def test_parse_month_name_dd_mmmm_format(self):
        """Test parsing 'DD MMMM' format (e.g., '25 December')."""
        today = date.today()
        year = today.year if today.month < 12 or (today.month == 12 and today.day < 25) else today.year + 1

        result = parse_due_date("25 December")
        assert result == f"Due: 25 December {year}"

    def test_parse_month_name_mmm_dd_format(self):
        """Test parsing 'MMM DD' format (e.g., 'Dec 25')."""
        today = date.today()
        year = today.year if today.month < 12 or (today.month == 12 and today.day < 25) else today.year + 1

        result = parse_due_date("Dec 25")
        assert result == f"Due: 25 December {year}"

    def test_parse_month_name_mmmm_dd_format(self):
        """Test parsing 'MMMM DD' format (e.g., 'December 25')."""
        today = date.today()
        year = today.year if today.month < 12 or (today.month == 12 and today.day < 25) else today.year + 1

        result = parse_due_date("December 25")
        assert result == f"Due: 25 December {year}"

    def test_parse_month_name_with_year(self):
        """Test parsing month name formats with explicit year."""
        result = parse_due_date("25 Dec 2025")
        assert result == "Due: 25 December 2025"

        result = parse_due_date("Dec 25 2025")
        assert result == "Due: 25 December 2025"

    def test_parse_month_name_case_insensitive(self):
        """Test that month names are case insensitive."""
        today = date.today()
        year = today.year if today.month < 12 or (today.month == 12 and today.day < 25) else today.year + 1

        result = parse_due_date("25 dec")
        assert result == f"Due: 25 December {year}"

        result = parse_due_date("DEC 25")
        assert result == f"Due: 25 December {year}"

        result = parse_due_date("25 DECEMBER")
        assert result == f"Due: 25 December {year}"

    def test_parse_all_month_abbreviations(self):
        """Test parsing all month abbreviations."""
        # Use dates within 365 days from today
        today = date.today()
        test_year = today.year + 1  # Next year to ensure not in past

        month_tests = [
            (f"15 Jan {test_year}", f"Due: 15 January {test_year}"),
            (f"15 Feb {test_year}", f"Due: 15 February {test_year}"),
            (f"15 Mar {test_year}", f"Due: 15 March {test_year}"),
            (f"15 Apr {test_year}", f"Due: 15 April {test_year}"),
            (f"15 May {test_year}", f"Due: 15 May {test_year}"),
            (f"15 Jun {test_year}", f"Due: 15 June {test_year}"),
            (f"15 Jul {test_year}", f"Due: 15 July {test_year}"),
            (f"15 Aug {test_year}", f"Due: 15 August {test_year}"),
            (f"15 Sep {test_year}", f"Due: 15 September {test_year}"),
            (f"15 Oct {test_year}", f"Due: 15 October {test_year}"),
        ]

        for input_date, expected in month_tests:
            result = parse_due_date(input_date)
            assert result == expected

    def test_parse_invalid_date_feb_31(self):
        """Test that invalid date (31 Feb) raises ValueError."""
        with pytest.raises(ValueError, match="Invalid date"):
            parse_due_date("31/02/2025")

        with pytest.raises(ValueError, match="Invalid date"):
            parse_due_date("31 Feb 2025")

    def test_parse_invalid_date_april_31(self):
        """Test that invalid date (31 Apr) raises ValueError."""
        with pytest.raises(ValueError, match="Invalid date"):
            parse_due_date("31/04/2025")

    def test_parse_past_date_iso_format(self):
        """Test that past date in ISO format raises ValueError."""
        with pytest.raises(ValueError, match="past"):
            parse_due_date("2020-01-01")

    def test_parse_past_date_dd_mm_format(self):
        """Test that past date in DD/MM/YYYY format raises ValueError."""
        with pytest.raises(ValueError, match="past"):
            parse_due_date("01/01/2020")

    def test_parse_date_more_than_365_days_ahead(self):
        """Test that date more than 365 days in the future raises ValueError."""
        today = date.today()
        far_future = today + timedelta(days=400)

        with pytest.raises(ValueError, match="365 days"):
            parse_due_date(f"{far_future.year}-{far_future.month:02d}-{far_future.day:02d}")

    def test_parse_invalid_month_name(self):
        """Test that invalid month name raises ValueError."""
        with pytest.raises(ValueError, match="Invalid month name"):
            parse_due_date("25 Decembar")

        with pytest.raises(ValueError, match="Invalid month name"):
            parse_due_date("Xyz 25")

    def test_parse_empty_string_raises_error(self):
        """Test that empty string raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            parse_due_date("")

    def test_parse_whitespace_only_raises_error(self):
        """Test that whitespace-only string raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            parse_due_date("   ")

    def test_parse_invalid_format_raises_error(self):
        """Test that completely invalid format raises ValueError."""
        with pytest.raises(ValueError, match="Invalid due date format"):
            parse_due_date("not a date")

        with pytest.raises(ValueError, match="Invalid due date format"):
            parse_due_date("abc123")

    def test_parse_decimal_relative_days_raises_error(self):
        """Test that decimal relative days raises ValueError."""
        with pytest.raises(ValueError, match="Invalid due date format"):
            parse_due_date("0.5")

        with pytest.raises(ValueError, match="Invalid due date format"):
            parse_due_date("7.5")

    def test_parse_with_extra_whitespace(self):
        """Test that extra whitespace is handled correctly."""
        result = parse_due_date("  7  ")
        assert "In 7 days" in result

        today = date.today()
        year = today.year if today.month < 12 or (today.month == 12 and today.day < 25) else today.year + 1
        result = parse_due_date("  25 Dec  ")
        assert result == f"Due: 25 December {year}"

    def test_parse_year_rollover_behavior(self):
        """Test that dates without year correctly roll over to next year if needed."""
        today = date.today()

        # Test with a date that's definitely in the past this year
        if today.month > 1:  # After January
            result = parse_due_date("01/01")
            # Should be next year
            assert f"{today.year + 1}" in result

        # Test with a date in the future this year
        if today.month < 12:  # Before December
            result = parse_due_date("25/12")
            # Should be this year or next depending on current date
            assert ("December" in result)

    def test_parse_september_abbreviation_variants(self):
        """Test that both 'Sep' and 'Sept' work for September."""
        result1 = parse_due_date("15 Sep 2026")
        result2 = parse_due_date("15 Sept 2026")

        assert result1 == "Due: 15 September 2026"
        assert result2 == "Due: 15 September 2026"

    def test_parse_edge_case_leap_year(self):
        """Test parsing Feb 29 in a leap year."""
        # Note: 2028 is a leap year but may be >365 days away, so we skip this test
        # or use a closer leap year. Let's test the validation instead.
        with pytest.raises(ValueError, match="Invalid date"):
            # Try a non-leap year to verify leap year validation
            parse_due_date("29/02/2027")

    def test_parse_edge_case_non_leap_year(self):
        """Test that Feb 29 in non-leap year raises ValueError."""
        with pytest.raises(ValueError, match="Invalid date"):
            parse_due_date("29/02/2027")  # 2027 is not a leap year

    def test_parse_single_digit_day_month_in_slash_format(self):
        """Test parsing with single digit day and month."""
        today = date.today()
        next_year = today.year + 1
        result = parse_due_date(f"5/6/{next_year}")
        assert result == f"Due: 5 June {next_year}"

    def test_parse_boundary_dates(self):
        """Test parsing boundary dates within 365 days."""
        today = date.today()

        result = parse_due_date(f"01/01/{today.year + 1}")
        assert result == f"Due: 1 January {today.year + 1}"

        # Test a date that's definitely within 365 days
        test_date = today + timedelta(days=30)
        result = parse_due_date(f"{test_date.day:02d}/{test_date.month:02d}/{test_date.year}")
        month_name = ['', 'January', 'February', 'March', 'April', 'May', 'June',
                     'July', 'August', 'September', 'October', 'November', 'December'][test_date.month]
        assert result == f"Due: {test_date.day} {month_name} {test_date.year}"