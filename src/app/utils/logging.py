"""
Structured JSON logging utility for the InvoiceIQ application.

This module provides structured logging capabilities with JSON output format,
making logs easily parseable and searchable in production environments.
"""

import json
import logging
import sys
from typing import Any


class JSONFormatter(logging.Formatter):
    """
    Custom JSON formatter for structured logging.

    Formats log records as JSON objects with timestamp, level, logger name,
    message, and any additional fields passed via the 'extra' parameter.
    """

    def format(self, record: logging.LogRecord) -> str:
        """
        Format a log record as a JSON string.

        Args:
            record: The log record to format

        Returns:
            JSON-formatted string representation of the log record
        """
        log_data = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add extra fields if present (e.g., correlation_id)
        if hasattr(record, "correlation_id"):
            log_data["correlation_id"] = record.correlation_id

        # Add any other custom attributes from extra dict
        for key, value in record.__dict__.items():
            if key not in [
                "name",
                "msg",
                "args",
                "created",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "message",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "thread",
                "threadName",
                "exc_info",
                "exc_text",
                "stack_info",
                "correlation_id",
            ]:
                log_data[key] = value

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


def setup_logging(level: str = "INFO") -> None:
    """
    Configure application logging with JSON formatting.

    Sets up the root logger with JSON formatter and console handler.

    Args:
        level: The logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
               Defaults to INFO.

    Example:
        >>> setup_logging("DEBUG")
        >>> logger = get_logger(__name__)
        >>> logger.info("Application started")
    """
    # Convert string level to logging constant
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Create console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(numeric_level)

    # Set JSON formatter
    formatter = JSONFormatter()
    handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    root_logger.handlers.clear()  # Clear any existing handlers
    root_logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the specified name.

    Args:
        name: The name for the logger, typically __name__ of the calling module

    Returns:
        A configured logger instance

    Example:
        >>> logger = get_logger(__name__)
        >>> logger.info("Processing invoice", extra={"invoice_id": "INV-123"})
    """
    return logging.getLogger(name)


def log_api_call(
    service: str,
    endpoint: str,
    method: str,
    status_code: int,
    duration_ms: float,
    correlation_id: str | None = None,
    error_type: str | None = None,
) -> None:
    """
    Log API calls with metadata only (no request/response bodies with PII).

    Args:
        service: API service name (e.g., "whatsapp", "mpesa")
        endpoint: API endpoint path
        method: HTTP method (GET, POST, etc.)
        status_code: HTTP response status code
        duration_ms: Request duration in milliseconds
        correlation_id: Request correlation ID for tracing
        error_type: Exception type if error occurred

    Example:
        >>> log_api_call(
        ...     service="mpesa",
        ...     endpoint="/oauth/v1/generate",
        ...     method="GET",
        ...     status_code=200,
        ...     duration_ms=245.5,
        ...     correlation_id="abc-123"
        ... )
    """
    logger = get_logger(__name__)
    extra_data = {
        "service": service,
        "endpoint": endpoint,
        "method": method,
        "status_code": status_code,
        "duration_ms": duration_ms,
    }

    if correlation_id:
        extra_data["correlation_id"] = correlation_id

    if error_type:
        extra_data["error_type"] = error_type

    logger.info(
        f"API call to {service}",
        extra=extra_data,
    )


def log_event(
    event: str,
    level: str = "INFO",
    correlation_id: str | None = None,
    **metadata: Any,
) -> None:
    """
    Log an event with privacy-compliant metadata.

    Automatically filters out common PII fields (phone, msisdn, customer_name, etc.)

    Args:
        event: Event description
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        correlation_id: Request correlation ID
        **metadata: Additional metadata (PII fields will be filtered)

    Example:
        >>> log_event(
        ...     "Invoice created",
        ...     level="INFO",
        ...     correlation_id="abc-123",
        ...     invoice_id="INV-001",
        ...     status="PENDING"
        ... )
    """
    # PII fields to filter
    pii_fields = {
        "phone",
        "msisdn",
        "phone_number",
        "customer_phone",
        "merchant_phone",
        "customer_name",
        "name",
        "full_name",
        "message",
        "message_text",
        "body",
        "email",
        "address",
    }

    # Also filter fields containing these substrings
    sensitive_substrings = {"password", "token", "secret", "key", "credential"}

    # Filter metadata
    filtered_metadata = {}
    for key, value in metadata.items():
        # Check if key is in PII fields
        if key.lower() in pii_fields:
            continue
        # Check if key contains sensitive substrings
        if any(substring in key.lower() for substring in sensitive_substrings):
            continue
        filtered_metadata[key] = value

    # Add correlation ID if present
    if correlation_id:
        filtered_metadata["correlation_id"] = correlation_id

    # Get logger and log at appropriate level
    logger = get_logger(__name__)
    log_level = getattr(logging, level.upper(), logging.INFO)

    logger.log(
        log_level,
        event,
        extra=filtered_metadata,
    )


def log_error(
    logger: logging.Logger,
    error: Exception,
    context: dict[str, Any],
) -> None:
    """
    Log an error with contextual information.

    Args:
        logger: The logger instance to use
        error: The exception that occurred
        context: Additional context information about the error

    Example:
        >>> logger = get_logger(__name__)
        >>> try:
        ...     # Some operation
        ...     raise ValueError("Invalid phone number")
        ... except ValueError as e:
        ...     log_error(
        ...         logger,
        ...         error=e,
        ...         context={"phone": "123", "invoice_id": "INV-123"}
        ...     )
    """
    logger.error(
        f"Error occurred: {str(error)}",
        extra={
            "error_type": type(error).__name__,
            "error_message": str(error),
            "context": context,
        },
        exc_info=True,
    )