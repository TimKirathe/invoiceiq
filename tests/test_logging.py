"""
Tests for privacy-compliant logging utilities.

Tests verify that:
1. PII is filtered from logs
2. Correlation IDs are propagated correctly
3. Log format is valid JSON
4. Sensitive fields are redacted
"""

import json
import logging

from src.app.utils.logging import (
    JSONFormatter,
    get_logger,
    log_api_call,
    log_event,
    setup_logging,
)


class TestJSONFormatter:
    """Test the JSON formatter for structured logging."""

    def test_json_formatter_basic(self):
        """Test that JSONFormatter produces valid JSON output."""
        # Set up formatter
        formatter = JSONFormatter()

        # Create a log record
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        # Format the record
        formatted = formatter.format(record)

        # Verify it's valid JSON
        log_data = json.loads(formatted)

        # Verify required fields
        assert "timestamp" in log_data
        assert "level" in log_data
        assert log_data["level"] == "INFO"
        assert "logger" in log_data
        assert log_data["logger"] == "test"
        assert "message" in log_data
        assert log_data["message"] == "Test message"

    def test_json_formatter_with_correlation_id(self):
        """Test that correlation_id is included in JSON output."""
        formatter = JSONFormatter()

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        record.correlation_id = "test-correlation-123"

        formatted = formatter.format(record)
        log_data = json.loads(formatted)

        assert "correlation_id" in log_data
        assert log_data["correlation_id"] == "test-correlation-123"

    def test_json_formatter_with_extra_fields(self):
        """Test that extra fields are included in JSON output."""
        formatter = JSONFormatter()

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        # Add custom fields
        record.invoice_id = "INV-123"
        record.status = "PENDING"

        formatted = formatter.format(record)
        log_data = json.loads(formatted)

        assert "invoice_id" in log_data
        assert log_data["invoice_id"] == "INV-123"
        assert "status" in log_data
        assert log_data["status"] == "PENDING"

    def test_json_formatter_with_exception(self):
        """Test that exceptions are formatted properly."""
        formatter = JSONFormatter()

        try:
            raise ValueError("Test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()
            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname="test.py",
                lineno=1,
                msg="Error occurred",
                args=(),
                exc_info=exc_info,
            )

        formatted = formatter.format(record)
        log_data = json.loads(formatted)

        assert "exception" in log_data
        assert "ValueError: Test error" in log_data["exception"]


class TestLogEvent:
    """Test the log_event function for PII filtering."""

    def test_log_event_filters_pii_fields(self, caplog):
        """Test that PII fields are filtered from logs."""
        caplog.set_level(logging.INFO)

        # Call log_event with PII fields
        log_event(
            "Test event",
            level="INFO",
            invoice_id="INV-123",
            phone="254712345678",  # Should be filtered
            customer_name="John Doe",  # Should be filtered
            msisdn="254798765432",  # Should be filtered
            status="PENDING",
        )

        # Check that event was logged
        assert len(caplog.records) == 1
        record = caplog.records[0]

        # Verify PII fields were filtered
        assert not hasattr(record, "phone")
        assert not hasattr(record, "customer_name")
        assert not hasattr(record, "msisdn")

        # Verify non-PII fields were kept
        assert hasattr(record, "invoice_id")
        assert record.invoice_id == "INV-123"
        assert hasattr(record, "status")
        assert record.status == "PENDING"

    def test_log_event_filters_sensitive_field_names(self, caplog):
        """Test that fields with sensitive names are filtered."""
        caplog.set_level(logging.INFO)

        log_event(
            "Test event",
            level="INFO",
            invoice_id="INV-123",
            api_token="secret-token",  # Should be filtered
            password="secret-pass",  # Should be filtered
            api_secret="secret-key",  # Should be filtered
            database_password="db-pass",  # Should be filtered
        )

        # Check that event was logged
        assert len(caplog.records) == 1
        record = caplog.records[0]

        # Verify sensitive fields were filtered
        assert not hasattr(record, "api_token")
        assert not hasattr(record, "password")
        assert not hasattr(record, "api_secret")
        assert not hasattr(record, "database_password")

        # Verify non-sensitive fields were kept
        assert hasattr(record, "invoice_id")
        assert record.invoice_id == "INV-123"

    def test_log_event_with_correlation_id(self, caplog):
        """Test that correlation_id is added to logs."""
        caplog.set_level(logging.INFO)

        log_event(
            "Test event",
            level="INFO",
            correlation_id="abc-123",
            invoice_id="INV-123",
        )

        assert len(caplog.records) == 1
        record = caplog.records[0]

        assert hasattr(record, "correlation_id")
        assert record.correlation_id == "abc-123"

    def test_log_event_respects_log_level(self, caplog):
        """Test that log_event respects the specified log level."""
        caplog.set_level(logging.DEBUG)

        # Log at different levels
        log_event("Debug message", level="DEBUG", test_field="debug")
        log_event("Info message", level="INFO", test_field="info")
        log_event("Warning message", level="WARNING", test_field="warning")
        log_event("Error message", level="ERROR", test_field="error")

        # Verify all levels were logged
        assert len(caplog.records) == 4
        assert caplog.records[0].levelname == "DEBUG"
        assert caplog.records[1].levelname == "INFO"
        assert caplog.records[2].levelname == "WARNING"
        assert caplog.records[3].levelname == "ERROR"


class TestLogApiCall:
    """Test the log_api_call function."""

    def test_log_api_call_basic(self, caplog):
        """Test basic API call logging."""
        caplog.set_level(logging.INFO)

        log_api_call(
            service="whatsapp",
            endpoint="/messages",
            method="POST",
            status_code=200,
            duration_ms=245.5,
        )

        assert len(caplog.records) == 1
        record = caplog.records[0]

        assert hasattr(record, "service")
        assert record.service == "whatsapp"
        assert hasattr(record, "endpoint")
        assert record.endpoint == "/messages"
        assert hasattr(record, "method")
        assert record.method == "POST"
        assert hasattr(record, "status_code")
        assert record.status_code == 200
        assert hasattr(record, "duration_ms")
        assert record.duration_ms == 245.5

    def test_log_api_call_with_correlation_id(self, caplog):
        """Test API call logging with correlation ID."""
        caplog.set_level(logging.INFO)

        log_api_call(
            service="mpesa",
            endpoint="/oauth/v1/generate",
            method="GET",
            status_code=200,
            duration_ms=123.4,
            correlation_id="xyz-789",
        )

        assert len(caplog.records) == 1
        record = caplog.records[0]

        assert hasattr(record, "correlation_id")
        assert record.correlation_id == "xyz-789"

    def test_log_api_call_with_error(self, caplog):
        """Test API call logging with error type."""
        caplog.set_level(logging.INFO)

        log_api_call(
            service="sms",
            endpoint="/send",
            method="POST",
            status_code=500,
            duration_ms=1000.0,
            error_type="HTTPError",
        )

        assert len(caplog.records) == 1
        record = caplog.records[0]

        assert hasattr(record, "error_type")
        assert record.error_type == "HTTPError"
        assert record.status_code == 500


class TestSetupLogging:
    """Test the setup_logging function."""

    def test_setup_logging_configures_root_logger(self):
        """Test that setup_logging configures the root logger."""
        setup_logging(level="DEBUG")

        root_logger = logging.getLogger()

        # Verify log level set correctly
        assert root_logger.level == logging.DEBUG

        # Verify handler is configured
        assert len(root_logger.handlers) > 0

        # Verify formatter is JSONFormatter
        handler = root_logger.handlers[0]
        assert isinstance(handler.formatter, JSONFormatter)

    def test_get_logger_returns_logger(self):
        """Test that get_logger returns a logger instance."""
        logger = get_logger("test")

        assert isinstance(logger, logging.Logger)
        assert logger.name == "test"


class TestPIIFiltering:
    """Integration tests for PII filtering across logging functions."""

    def test_no_pii_in_logs(self, caplog):
        """Integration test: verify no PII appears in any logs."""
        caplog.set_level(logging.DEBUG)

        # Simulate various logging scenarios with PII
        log_event(
            "Invoice created",
            invoice_id="INV-123",
            phone="254712345678",  # PII
            customer_name="John Doe",  # PII
            amount=1000,
            email="john@example.com",  # PII
            status="PENDING",
        )

        log_event(
            "Payment initiated",
            payment_id="PAY-456",
            msisdn="254798765432",  # PII
            body="Payment for invoice",  # PII
        )

        # Verify no PII fields in any record (excluding built-in 'message' attribute)
        pii_fields = {"phone", "customer_name", "email", "msisdn", "body", "message_text", "sms_text"}

        for record in caplog.records:
            for field in pii_fields:
                assert not hasattr(record, field), f"PII field '{field}' found in log record"

        # Verify non-PII fields are present
        assert any(hasattr(r, "invoice_id") for r in caplog.records)
        assert any(hasattr(r, "payment_id") for r in caplog.records)
        assert any(hasattr(r, "status") for r in caplog.records)