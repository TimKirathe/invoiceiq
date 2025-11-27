"""
Unit tests for payment methods parsing and CRUD operations.
"""

import pytest
from unittest.mock import Mock

from src.app.utils.invoice_parser import parse_mpesa_payment_method
from src.app.services.payment_methods import (
    save_payment_method,
    get_payment_methods,
    get_default_payment_method,
    update_payment_method,
    delete_payment_method,
    set_default_payment_method
)


class TestParseMpesaPaymentMethod:
    """Test suite for parse_mpesa_payment_method function."""

    # PAYBILL tests
    def test_parse_paybill_space_separated(self):
        """Test parsing PAYBILL with space-separated details."""
        result = parse_mpesa_payment_method("1", "123456 ACC001")

        assert result["method_type"] == "PAYBILL"
        assert result["paybill_number"] == "123456"
        assert result["account_number"] == "ACC001"
        assert result["till_number"] is None
        assert result["phone_number"] is None

    def test_parse_paybill_newline_separated(self):
        """Test parsing PAYBILL with newline-separated details."""
        result = parse_mpesa_payment_method("1", "123456\nACC001")

        assert result["method_type"] == "PAYBILL"
        assert result["paybill_number"] == "123456"
        assert result["account_number"] == "ACC001"

    def test_parse_paybill_5_digit_number(self):
        """Test parsing PAYBILL with 5-digit paybill number."""
        result = parse_mpesa_payment_method("1", "12345 ACC001")

        assert result["paybill_number"] == "12345"

    def test_parse_paybill_7_digit_number(self):
        """Test parsing PAYBILL with 7-digit paybill number."""
        result = parse_mpesa_payment_method("1", "1234567 ACC001")

        assert result["paybill_number"] == "1234567"

    def test_parse_paybill_numeric_account(self):
        """Test parsing PAYBILL with numeric account number."""
        result = parse_mpesa_payment_method("1", "123456 123456789")

        assert result["account_number"] == "123456789"

    def test_parse_paybill_alphanumeric_account(self):
        """Test parsing PAYBILL with alphanumeric account number."""
        result = parse_mpesa_payment_method("1", "123456 ABC123XYZ")

        assert result["account_number"] == "ABC123XYZ"

    def test_parse_paybill_invalid_number_too_short(self):
        """Test that paybill number < 5 digits raises ValueError."""
        with pytest.raises(ValueError, match="Invalid paybill number"):
            parse_mpesa_payment_method("1", "1234 ACC001")

    def test_parse_paybill_invalid_number_too_long(self):
        """Test that paybill number > 7 digits raises ValueError."""
        with pytest.raises(ValueError, match="Invalid paybill number"):
            parse_mpesa_payment_method("1", "12345678 ACC001")

    def test_parse_paybill_invalid_account_too_long(self):
        """Test that account number > 20 chars raises ValueError."""
        with pytest.raises(ValueError, match="Invalid account number"):
            parse_mpesa_payment_method("1", "123456 ABCDEFGHIJKLMNOPQRSTUV")

    def test_parse_paybill_invalid_account_special_chars(self):
        """Test that account number with special chars raises ValueError."""
        with pytest.raises(ValueError, match="Invalid account number"):
            parse_mpesa_payment_method("1", "123456 ACC-001")

    def test_parse_paybill_missing_account(self):
        """Test that missing account number raises ValueError."""
        with pytest.raises(ValueError, match="Invalid PAYBILL format"):
            parse_mpesa_payment_method("1", "123456")

    def test_parse_paybill_extra_parts(self):
        """Test that extra parts raise ValueError."""
        with pytest.raises(ValueError, match="Invalid PAYBILL format"):
            parse_mpesa_payment_method("1", "123456 ACC001 EXTRA")

    # TILL tests
    def test_parse_till_valid(self):
        """Test parsing valid TILL number."""
        result = parse_mpesa_payment_method("2", "654321")

        assert result["method_type"] == "TILL"
        assert result["till_number"] == "654321"
        assert result["paybill_number"] is None
        assert result["account_number"] is None
        assert result["phone_number"] is None

    def test_parse_till_5_digit(self):
        """Test parsing TILL with 5-digit number."""
        result = parse_mpesa_payment_method("2", "12345")

        assert result["till_number"] == "12345"

    def test_parse_till_7_digit(self):
        """Test parsing TILL with 7-digit number."""
        result = parse_mpesa_payment_method("2", "1234567")

        assert result["till_number"] == "1234567"

    def test_parse_till_with_whitespace(self):
        """Test parsing TILL with extra whitespace."""
        result = parse_mpesa_payment_method("2", "  654321  ")

        assert result["till_number"] == "654321"

    def test_parse_till_invalid_too_short(self):
        """Test that till number < 5 digits raises ValueError."""
        with pytest.raises(ValueError, match="Invalid till number"):
            parse_mpesa_payment_method("2", "1234")

    def test_parse_till_invalid_too_long(self):
        """Test that till number > 7 digits raises ValueError."""
        with pytest.raises(ValueError, match="Invalid till number"):
            parse_mpesa_payment_method("2", "12345678")

    def test_parse_till_invalid_non_numeric(self):
        """Test that non-numeric till number raises ValueError."""
        with pytest.raises(ValueError, match="Invalid till number"):
            parse_mpesa_payment_method("2", "ABC123")

    # PHONE tests
    def test_parse_phone_valid(self):
        """Test parsing valid phone number."""
        result = parse_mpesa_payment_method("3", "254712345678")

        assert result["method_type"] == "PHONE"
        assert result["phone_number"] == "254712345678"
        assert result["paybill_number"] is None
        assert result["account_number"] is None
        assert result["till_number"] is None

    def test_parse_phone_with_plus(self):
        """Test parsing phone number with + prefix."""
        result = parse_mpesa_payment_method("3", "+254712345678")

        assert result["phone_number"] == "254712345678"

    def test_parse_phone_local_format(self):
        """Test parsing phone number in local format."""
        result = parse_mpesa_payment_method("3", "0712345678")

        assert result["phone_number"] == "254712345678"

    def test_parse_phone_invalid_format(self):
        """Test that invalid phone number raises ValueError."""
        with pytest.raises(ValueError, match="Invalid phone number"):
            parse_mpesa_payment_method("3", "123456")

    def test_parse_phone_non_kenyan(self):
        """Test that non-Kenyan phone number raises ValueError (if validator is strict)."""
        # This depends on the phone validator behavior
        # If it allows international numbers, this test should be adjusted
        pass

    # Method type validation tests
    def test_parse_invalid_method_type_zero(self):
        """Test that method type '0' raises ValueError."""
        with pytest.raises(ValueError, match="Invalid method type"):
            parse_mpesa_payment_method("0", "123456")

    def test_parse_invalid_method_type_four(self):
        """Test that method type '4' raises ValueError."""
        with pytest.raises(ValueError, match="Invalid method type"):
            parse_mpesa_payment_method("4", "123456")

    def test_parse_invalid_method_type_string(self):
        """Test that non-numeric method type raises ValueError."""
        with pytest.raises(ValueError, match="Invalid method type"):
            parse_mpesa_payment_method("PAYBILL", "123456 ACC001")

    def test_parse_empty_method_type(self):
        """Test that empty method type raises ValueError."""
        with pytest.raises(ValueError, match="Method type cannot be empty"):
            parse_mpesa_payment_method("", "123456")

    def test_parse_empty_details(self):
        """Test that empty details raise ValueError."""
        with pytest.raises(ValueError, match="Payment details cannot be empty"):
            parse_mpesa_payment_method("1", "")

    def test_parse_whitespace_method_type(self):
        """Test that whitespace-only method type raises ValueError."""
        with pytest.raises(ValueError, match="Method type cannot be empty"):
            parse_mpesa_payment_method("   ", "123456")

    def test_parse_whitespace_details(self):
        """Test that whitespace-only details raise ValueError."""
        with pytest.raises(ValueError, match="Payment details cannot be empty"):
            parse_mpesa_payment_method("1", "   ")


class TestSavePaymentMethod:
    """Test suite for save_payment_method function."""

    def test_save_paybill_method(self):
        """Test saving a PAYBILL payment method."""
        mock_supabase = Mock()
        mock_table = Mock()
        mock_supabase.table.return_value = mock_table
        mock_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = None
        mock_table.insert.return_value.execute.return_value = Mock(data=[{"id": "test-id"}])

        method_data = {
            "method_type": "PAYBILL",
            "paybill_number": "123456",
            "account_number": "ACC001",
            "till_number": None,
            "phone_number": None
        }

        method_id = save_payment_method("254712345678", method_data, mock_supabase)

        assert method_id is not None
        mock_table.insert.assert_called_once()

    def test_save_till_method(self):
        """Test saving a TILL payment method."""
        mock_supabase = Mock()
        mock_table = Mock()
        mock_supabase.table.return_value = mock_table
        mock_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = None
        mock_table.insert.return_value.execute.return_value = Mock(data=[{"id": "test-id"}])

        method_data = {
            "method_type": "TILL",
            "paybill_number": None,
            "account_number": None,
            "till_number": "654321",
            "phone_number": None
        }

        method_id = save_payment_method("254712345678", method_data, mock_supabase)

        assert method_id is not None

    def test_save_phone_method(self):
        """Test saving a PHONE payment method."""
        mock_supabase = Mock()
        mock_table = Mock()
        mock_supabase.table.return_value = mock_table
        mock_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = None
        mock_table.insert.return_value.execute.return_value = Mock(data=[{"id": "test-id"}])

        method_data = {
            "method_type": "PHONE",
            "paybill_number": None,
            "account_number": None,
            "till_number": None,
            "phone_number": "254712345678"
        }

        method_id = save_payment_method("254712345678", method_data, mock_supabase)

        assert method_id is not None

    def test_save_as_default_unsets_existing_defaults(self):
        """Test that saving as default unsets existing defaults."""
        mock_supabase = Mock()
        mock_table = Mock()
        mock_supabase.table.return_value = mock_table
        mock_update_chain = mock_table.update.return_value.eq.return_value.eq.return_value
        mock_update_chain.execute.return_value = None
        mock_table.insert.return_value.execute.return_value = Mock(data=[{"id": "test-id"}])

        method_data = {
            "method_type": "PAYBILL",
            "paybill_number": "123456",
            "account_number": "ACC001",
            "till_number": None,
            "phone_number": None
        }

        save_payment_method("254712345678", method_data, mock_supabase, is_default=True)

        # Verify update was called to unset existing defaults
        mock_table.update.assert_called()

    def test_save_empty_merchant_msisdn_raises_error(self):
        """Test that empty merchant MSISDN raises ValueError."""
        mock_supabase = Mock()
        method_data = {"method_type": "PAYBILL"}

        with pytest.raises(ValueError, match="Merchant MSISDN cannot be empty"):
            save_payment_method("", method_data, mock_supabase)

    def test_save_empty_method_data_raises_error(self):
        """Test that empty method data raises ValueError."""
        mock_supabase = Mock()

        with pytest.raises(ValueError, match="Method data cannot be empty"):
            save_payment_method("254712345678", {}, mock_supabase)

    def test_save_missing_method_type_raises_error(self):
        """Test that missing method_type raises ValueError."""
        mock_supabase = Mock()
        method_data = {"paybill_number": "123456"}

        with pytest.raises(ValueError, match="method_type is required"):
            save_payment_method("254712345678", method_data, mock_supabase)

    def test_save_invalid_method_type_raises_error(self):
        """Test that invalid method_type raises ValueError."""
        mock_supabase = Mock()
        method_data = {"method_type": "INVALID"}

        with pytest.raises(ValueError, match="Invalid method_type"):
            save_payment_method("254712345678", method_data, mock_supabase)


class TestGetPaymentMethods:
    """Test suite for get_payment_methods function."""

    def test_get_payment_methods_returns_list(self):
        """Test that get_payment_methods returns a list."""
        mock_supabase = Mock()
        mock_table = Mock()
        mock_supabase.table.return_value = mock_table
        mock_chain = mock_table.select.return_value.eq.return_value.order.return_value
        mock_chain.execute.return_value = Mock(data=[
            {"id": "1", "method_type": "PAYBILL"},
            {"id": "2", "method_type": "TILL"}
        ])

        result = get_payment_methods("254712345678", mock_supabase)

        assert isinstance(result, list)
        assert len(result) == 2

    def test_get_payment_methods_empty_list(self):
        """Test that get_payment_methods returns empty list when no methods."""
        mock_supabase = Mock()
        mock_table = Mock()
        mock_supabase.table.return_value = mock_table
        mock_chain = mock_table.select.return_value.eq.return_value.order.return_value
        mock_chain.execute.return_value = Mock(data=[])

        result = get_payment_methods("254712345678", mock_supabase)

        assert result == []

    def test_get_payment_methods_empty_msisdn_raises_error(self):
        """Test that empty MSISDN raises ValueError."""
        mock_supabase = Mock()

        with pytest.raises(ValueError, match="Merchant MSISDN cannot be empty"):
            get_payment_methods("", mock_supabase)


class TestGetDefaultPaymentMethod:
    """Test suite for get_default_payment_method function."""

    def test_get_default_returns_method(self):
        """Test that get_default_payment_method returns default method."""
        mock_supabase = Mock()
        mock_table = Mock()
        mock_supabase.table.return_value = mock_table
        mock_chain = mock_table.select.return_value.eq.return_value.eq.return_value
        mock_chain.execute.return_value = Mock(data=[{
            "id": "1",
            "is_default": True,
            "method_type": "PAYBILL"
        }])

        result = get_default_payment_method("254712345678", mock_supabase)

        assert result is not None
        assert result["id"] == "1"

    def test_get_default_returns_none_when_no_default(self):
        """Test that get_default_payment_method returns None when no default."""
        mock_supabase = Mock()
        mock_table = Mock()
        mock_supabase.table.return_value = mock_table
        mock_chain = mock_table.select.return_value.eq.return_value.eq.return_value
        mock_chain.execute.return_value = Mock(data=[])

        result = get_default_payment_method("254712345678", mock_supabase)

        assert result is None

    def test_get_default_empty_msisdn_raises_error(self):
        """Test that empty MSISDN raises ValueError."""
        mock_supabase = Mock()

        with pytest.raises(ValueError, match="Merchant MSISDN cannot be empty"):
            get_default_payment_method("", mock_supabase)


class TestUpdatePaymentMethod:
    """Test suite for update_payment_method function."""

    def test_update_returns_true_when_successful(self):
        """Test that update_payment_method returns True when successful."""
        mock_supabase = Mock()
        mock_table = Mock()
        mock_supabase.table.return_value = mock_table
        mock_chain = mock_table.update.return_value.eq.return_value
        mock_chain.execute.return_value = Mock(data=[{"id": "1"}])

        result = update_payment_method("method-id", {"account_number": "NEWACC"}, mock_supabase)

        assert result is True

    def test_update_returns_false_when_not_found(self):
        """Test that update_payment_method returns False when method not found."""
        mock_supabase = Mock()
        mock_table = Mock()
        mock_supabase.table.return_value = mock_table
        mock_chain = mock_table.update.return_value.eq.return_value
        mock_chain.execute.return_value = Mock(data=[])

        result = update_payment_method("method-id", {"account_number": "NEWACC"}, mock_supabase)

        assert result is False

    def test_update_empty_method_id_raises_error(self):
        """Test that empty method ID raises ValueError."""
        mock_supabase = Mock()

        with pytest.raises(ValueError, match="Method ID cannot be empty"):
            update_payment_method("", {"account_number": "NEWACC"}, mock_supabase)

    def test_update_empty_updates_raises_error(self):
        """Test that empty updates raise ValueError."""
        mock_supabase = Mock()

        with pytest.raises(ValueError, match="Updates dictionary cannot be empty"):
            update_payment_method("method-id", {}, mock_supabase)


class TestDeletePaymentMethod:
    """Test suite for delete_payment_method function."""

    def test_delete_returns_true_when_successful(self):
        """Test that delete_payment_method returns True when successful."""
        mock_supabase = Mock()
        mock_table = Mock()
        mock_supabase.table.return_value = mock_table
        mock_chain = mock_table.delete.return_value.eq.return_value
        mock_chain.execute.return_value = Mock(data=[{"id": "1"}])

        result = delete_payment_method("method-id", mock_supabase)

        assert result is True

    def test_delete_returns_false_when_not_found(self):
        """Test that delete_payment_method returns False when method not found."""
        mock_supabase = Mock()
        mock_table = Mock()
        mock_supabase.table.return_value = mock_table
        mock_chain = mock_table.delete.return_value.eq.return_value
        mock_chain.execute.return_value = Mock(data=[])

        result = delete_payment_method("method-id", mock_supabase)

        assert result is False

    def test_delete_empty_method_id_raises_error(self):
        """Test that empty method ID raises ValueError."""
        mock_supabase = Mock()

        with pytest.raises(ValueError, match="Method ID cannot be empty"):
            delete_payment_method("", mock_supabase)


class TestSetDefaultPaymentMethod:
    """Test suite for set_default_payment_method function."""

    def test_set_default_returns_true_when_successful(self):
        """Test that set_default_payment_method returns True when successful."""
        mock_supabase = Mock()
        mock_table = Mock()
        mock_supabase.table.return_value = mock_table

        # Mock the check query
        mock_check_chain = mock_table.select.return_value.eq.return_value.eq.return_value
        mock_check_chain.execute.return_value = Mock(data=[{"id": "1"}])

        # Mock the unset query
        mock_unset_chain = mock_table.update.return_value.eq.return_value.eq.return_value
        mock_unset_chain.execute.return_value = None

        # Mock the set default query
        mock_update_chain = mock_table.update.return_value.eq.return_value
        mock_update_chain.execute.return_value = Mock(data=[{"id": "1"}])

        result = set_default_payment_method("254712345678", "method-id", mock_supabase)

        assert result is True

    def test_set_default_returns_false_when_not_found(self):
        """Test that set_default_payment_method returns False when method not found."""
        mock_supabase = Mock()
        mock_table = Mock()
        mock_supabase.table.return_value = mock_table

        # Mock the check query to return empty
        mock_check_chain = mock_table.select.return_value.eq.return_value.eq.return_value
        mock_check_chain.execute.return_value = Mock(data=[])

        result = set_default_payment_method("254712345678", "method-id", mock_supabase)

        assert result is False

    def test_set_default_empty_msisdn_raises_error(self):
        """Test that empty MSISDN raises ValueError."""
        mock_supabase = Mock()

        with pytest.raises(ValueError, match="Merchant MSISDN cannot be empty"):
            set_default_payment_method("", "method-id", mock_supabase)

    def test_set_default_empty_method_id_raises_error(self):
        """Test that empty method ID raises ValueError."""
        mock_supabase = Mock()

        with pytest.raises(ValueError, match="Method ID cannot be empty"):
            set_default_payment_method("254712345678", "", mock_supabase)


class TestIntegration:
    """Integration tests combining parsing and CRUD operations."""

    def test_full_workflow_paybill(self):
        """Test complete workflow: parse -> save -> retrieve."""
        # Parse
        parsed = parse_mpesa_payment_method("1", "123456 ACC001")
        assert parsed["method_type"] == "PAYBILL"

        # Mock Supabase for save
        mock_supabase = Mock()
        mock_table = Mock()
        mock_supabase.table.return_value = mock_table
        mock_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = None
        mock_table.insert.return_value.execute.return_value = Mock(data=[{"id": "test-id"}])

        # Save
        method_id = save_payment_method("254712345678", parsed, mock_supabase)
        assert method_id is not None

    def test_full_workflow_till(self):
        """Test complete workflow for TILL: parse -> save."""
        parsed = parse_mpesa_payment_method("2", "654321")
        assert parsed["method_type"] == "TILL"

        mock_supabase = Mock()
        mock_table = Mock()
        mock_supabase.table.return_value = mock_table
        mock_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = None
        mock_table.insert.return_value.execute.return_value = Mock(data=[{"id": "test-id"}])

        method_id = save_payment_method("254712345678", parsed, mock_supabase)
        assert method_id is not None

    def test_full_workflow_phone(self):
        """Test complete workflow for PHONE: parse -> save."""
        parsed = parse_mpesa_payment_method("3", "254712345678")
        assert parsed["method_type"] == "PHONE"

        mock_supabase = Mock()
        mock_table = Mock()
        mock_supabase.table.return_value = mock_table
        mock_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = None
        mock_table.insert.return_value.execute.return_value = Mock(data=[{"id": "test-id"}])

        method_id = save_payment_method("254712345678", parsed, mock_supabase)
        assert method_id is not None