"""
Unit tests for phone number validation and normalization utilities.

This test module validates the behavior of MSISDN validation and normalization
functions in src/app/utils/phone.py, ensuring compliance with Kenyan mobile
number formats (E.164 format: 254XXXXXXXXX).
"""

import pytest

from src.app.utils.phone import validate_msisdn, normalize_msisdn


class TestValidateMSISDN:
    """Test suite for validate_msisdn function."""

    def test_validate_msisdn_valid_format(self):
        """Test validation with a correctly formatted MSISDN."""
        result = validate_msisdn("254712345678")
        assert result == "254712345678"

    @pytest.mark.parametrize(
        "valid_phone",
        [
            "254712345678",  # Safaricom
            "254722345678",  # Safaricom
            "254732345678",  # Safaricom
            "254742345678",  # Safaricom
            "254752345678",  # Safaricom
            "254768345678",  # Safaricom
            "254769345678",  # Safaricom
            "254790345678",  # Safaricom
            "254791345678",  # Safaricom
            "254792345678",  # Safaricom
        ],
    )
    def test_validate_msisdn_various_valid_safaricom_numbers(self, valid_phone):
        """Test validation accepts various Safaricom number prefixes."""
        result = validate_msisdn(valid_phone)
        assert result == valid_phone

    def test_validate_msisdn_with_leading_whitespace(self):
        """Test validation strips leading whitespace."""
        result = validate_msisdn(" 254712345678")
        assert result == "254712345678"

    def test_validate_msisdn_with_trailing_whitespace(self):
        """Test validation strips trailing whitespace."""
        result = validate_msisdn("254712345678 ")
        assert result == "254712345678"

    def test_validate_msisdn_with_surrounding_whitespace(self):
        """Test validation strips both leading and trailing whitespace."""
        result = validate_msisdn("  254712345678  ")
        assert result == "254712345678"

    def test_validate_msisdn_rejects_plus_prefix(self):
        """Test validation rejects numbers with + prefix."""
        with pytest.raises(ValueError) as exc_info:
            validate_msisdn("+254712345678")
        assert "Invalid phone number format" in str(exc_info.value)
        assert "2547XXXXXXXX" in str(exc_info.value)

    def test_validate_msisdn_rejects_local_format(self):
        """Test validation rejects local format (0XXXXXXXXX)."""
        with pytest.raises(ValueError) as exc_info:
            validate_msisdn("0712345678")
        assert "Invalid phone number format" in str(exc_info.value)

    def test_validate_msisdn_rejects_too_short(self):
        """Test validation rejects numbers with fewer than 12 digits."""
        with pytest.raises(ValueError) as exc_info:
            validate_msisdn("25471234567")  # 11 digits instead of 12
        assert "Invalid phone number format" in str(exc_info.value)

    def test_validate_msisdn_rejects_too_long(self):
        """Test validation rejects numbers with more than 12 digits."""
        with pytest.raises(ValueError) as exc_info:
            validate_msisdn("2547123456789")  # 13 digits instead of 12
        assert "Invalid phone number format" in str(exc_info.value)

    def test_validate_msisdn_rejects_wrong_country_code(self):
        """Test validation rejects numbers with incorrect country code."""
        with pytest.raises(ValueError) as exc_info:
            validate_msisdn("255712345678")  # Tanzania country code
        assert "Invalid phone number format" in str(exc_info.value)

    @pytest.mark.parametrize(
        "non_safaricom_phone",
        [
            "254112345678",  # Telkom (011)
            "254112345678",  # Airtel (01X)
            "254812345678",  # Not allocated
            "254912345678",  # Not allocated
        ],
    )
    def test_validate_msisdn_rejects_non_safaricom_numbers(self, non_safaricom_phone):
        """Test validation rejects non-Safaricom numbers (not starting with 2547)."""
        with pytest.raises(ValueError) as exc_info:
            validate_msisdn(non_safaricom_phone)
        assert "Invalid phone number format" in str(exc_info.value)

    def test_validate_msisdn_rejects_empty_string(self):
        """Test validation rejects empty strings."""
        with pytest.raises(ValueError) as exc_info:
            validate_msisdn("")
        assert "Phone number cannot be empty" in str(exc_info.value)

    def test_validate_msisdn_rejects_whitespace_only(self):
        """Test validation rejects strings containing only whitespace."""
        with pytest.raises(ValueError) as exc_info:
            validate_msisdn("   ")
        assert "Phone number cannot be empty" in str(exc_info.value)

    def test_validate_msisdn_rejects_none(self):
        """Test validation rejects None values."""
        with pytest.raises(ValueError) as exc_info:
            validate_msisdn(None)
        assert "Phone number cannot be None" in str(exc_info.value)

    @pytest.mark.parametrize(
        "invalid_phone",
        [
            "abc712345678",  # Letters instead of country code
            "254abc345678",  # Letters in number
            "254-712-345678",  # Hyphens
            "254 712 345678",  # Spaces
            "254.712.345678",  # Dots
            "254(712)345678",  # Parentheses
            "254#712345678",  # Special characters
        ],
    )
    def test_validate_msisdn_rejects_non_numeric_characters(self, invalid_phone):
        """Test validation rejects numbers with letters or special characters."""
        with pytest.raises(ValueError) as exc_info:
            validate_msisdn(invalid_phone)
        assert "Invalid phone number format" in str(exc_info.value)


class TestNormalizeMSISDN:
    """Test suite for normalize_msisdn function."""

    def test_normalize_msisdn_already_normalized(self):
        """Test normalization returns unchanged value when already normalized."""
        result = normalize_msisdn("254712345678")
        assert result == "254712345678"

    def test_normalize_msisdn_with_plus_prefix(self):
        """Test normalization removes + prefix."""
        result = normalize_msisdn("+254712345678")
        assert result == "254712345678"

    def test_normalize_msisdn_from_local_format(self):
        """Test normalization converts local format (0XXXXXXXXX) to E.164."""
        result = normalize_msisdn("0712345678")
        assert result == "254712345678"

    def test_normalize_msisdn_without_country_code(self):
        """Test normalization adds country code to 9-digit numbers starting with 7."""
        result = normalize_msisdn("712345678")
        assert result == "254712345678"

    @pytest.mark.parametrize(
        "input_phone,expected",
        [
            ("254712345678", "254712345678"),  # Already normalized
            ("+254712345678", "254712345678"),  # With + prefix
            ("0712345678", "254712345678"),  # Local format
            ("712345678", "254712345678"),  # Without country code
            ("254722345678", "254722345678"),  # Different Safaricom prefix
            ("+254732345678", "254732345678"),  # Different prefix with +
            ("0742345678", "254742345678"),  # Different prefix local format
        ],
    )
    def test_normalize_msisdn_various_formats(self, input_phone, expected):
        """Test normalization handles various input formats correctly."""
        result = normalize_msisdn(input_phone)
        assert result == expected

    def test_normalize_msisdn_with_leading_whitespace(self):
        """Test normalization strips leading whitespace before processing."""
        result = normalize_msisdn(" 254712345678")
        assert result == "254712345678"

    def test_normalize_msisdn_with_trailing_whitespace(self):
        """Test normalization strips trailing whitespace before processing."""
        result = normalize_msisdn("254712345678 ")
        assert result == "254712345678"

    def test_normalize_msisdn_with_whitespace_and_plus(self):
        """Test normalization handles whitespace with + prefix."""
        result = normalize_msisdn("  +254712345678  ")
        assert result == "254712345678"

    def test_normalize_msisdn_with_whitespace_local_format(self):
        """Test normalization handles whitespace with local format."""
        result = normalize_msisdn("  0712345678  ")
        assert result == "254712345678"

    def test_normalize_msisdn_rejects_empty_string(self):
        """Test normalization rejects empty strings."""
        with pytest.raises(ValueError) as exc_info:
            normalize_msisdn("")
        assert "Phone number cannot be empty" in str(exc_info.value)

    def test_normalize_msisdn_rejects_whitespace_only(self):
        """Test normalization rejects strings containing only whitespace."""
        with pytest.raises(ValueError) as exc_info:
            normalize_msisdn("   ")
        assert "Phone number cannot be empty" in str(exc_info.value)

    def test_normalize_msisdn_rejects_none(self):
        """Test normalization rejects None values."""
        with pytest.raises(ValueError) as exc_info:
            normalize_msisdn(None)
        assert "Phone number cannot be None" in str(exc_info.value)

    def test_normalize_msisdn_rejects_invalid_local_format(self):
        """Test normalization rejects invalid local format (non-7XX)."""
        with pytest.raises(ValueError) as exc_info:
            normalize_msisdn("0112345678")  # Not a Safaricom number
        assert "Invalid phone number format" in str(exc_info.value)

    def test_normalize_msisdn_rejects_invalid_short_format(self):
        """Test normalization rejects 9-digit numbers not starting with 7."""
        with pytest.raises(ValueError) as exc_info:
            normalize_msisdn("112345678")  # 9 digits but doesn't start with 7
        assert "Invalid phone number format" in str(exc_info.value)

    @pytest.mark.parametrize(
        "invalid_phone",
        [
            "+255712345678",  # Wrong country code with +
            "0812345678",  # Invalid local format (not 7XX)
            "812345678",  # 9 digits not starting with 7
            "25412345678",  # Wrong country code, correct length
            "12345678",  # Too short
            "254712345",  # Too short after country code
        ],
    )
    def test_normalize_msisdn_rejects_various_invalid_formats(self, invalid_phone):
        """Test normalization rejects various invalid formats that can't be normalized."""
        with pytest.raises(ValueError) as exc_info:
            normalize_msisdn(invalid_phone)
        assert "Invalid phone number format" in str(exc_info.value)

    @pytest.mark.parametrize(
        "invalid_phone",
        [
            "abcd712345678",  # Letters
            "254abc345678",  # Letters mixed with numbers
            "+254-712-345678",  # Hyphens (note: + is stripped, but hyphens remain)
            "0712 345 678",  # Spaces within number
        ],
    )
    def test_normalize_msisdn_rejects_non_numeric_formats(self, invalid_phone):
        """Test normalization rejects formats with non-numeric characters."""
        with pytest.raises(ValueError) as exc_info:
            normalize_msisdn(invalid_phone)
        # All of these will fail validation after normalization attempts
        assert "Invalid phone number format" in str(exc_info.value)

    def test_normalize_msisdn_validates_result(self):
        """Test that normalized result is validated before returning."""
        # This tests that normalize_msisdn calls validate_msisdn internally
        # by ensuring that even after normalization, invalid numbers are rejected
        with pytest.raises(ValueError) as exc_info:
            normalize_msisdn("0112345678")  # Will normalize to 254112345678 (invalid)
        assert "Invalid phone number format" in str(exc_info.value)


class TestEdgeCases:
    """Test suite for edge cases and boundary conditions."""

    def test_validate_msisdn_minimum_valid_safaricom_number(self):
        """Test validation with lowest possible valid Safaricom number."""
        result = validate_msisdn("254700000000")
        assert result == "254700000000"

    def test_validate_msisdn_maximum_valid_safaricom_number(self):
        """Test validation with highest possible valid Safaricom number."""
        result = validate_msisdn("254799999999")
        assert result == "254799999999"

    def test_normalize_msisdn_multiple_plus_signs(self):
        """Test normalization with multiple + signs (only first is removed)."""
        with pytest.raises(ValueError):
            # After removing first +, "254+712345678" is invalid
            normalize_msisdn("++254712345678")

    def test_normalize_msisdn_local_format_wrong_length(self):
        """Test normalization doesn't convert local format if length != 10."""
        with pytest.raises(ValueError):
            # "071234567" is 9 chars starting with 0, won't trigger local conversion
            normalize_msisdn("071234567")

    def test_validate_msisdn_boundary_just_below_valid_length(self):
        """Test validation rejects number with 11 digits."""
        with pytest.raises(ValueError):
            validate_msisdn("25471234567")  # 11 digits

    def test_validate_msisdn_boundary_just_above_valid_length(self):
        """Test validation rejects number with 13 digits."""
        with pytest.raises(ValueError):
            validate_msisdn("2547123456789")  # 13 digits

    @pytest.mark.parametrize(
        "whitespace_phone",
        [
            "\t254712345678\t",  # Tabs
            "\n254712345678\n",  # Newlines
            " \t\n254712345678 \t\n",  # Mixed whitespace
        ],
    )
    def test_validate_msisdn_various_whitespace_types(self, whitespace_phone):
        """Test validation handles various types of whitespace."""
        result = validate_msisdn(whitespace_phone)
        assert result == "254712345678"

    @pytest.mark.parametrize(
        "whitespace_phone",
        [
            "\t+254712345678\t",  # Tabs with +
            "\n0712345678\n",  # Newlines with local format
            " \t712345678 ",  # Mixed whitespace without country code
        ],
    )
    def test_normalize_msisdn_various_whitespace_types(self, whitespace_phone):
        """Test normalization handles various types of whitespace."""
        result = normalize_msisdn(whitespace_phone)
        assert result == "254712345678"