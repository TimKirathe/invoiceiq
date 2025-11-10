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
    logger: logging.Logger,
    service: str,
    endpoint: str,
    request_data: dict[str, Any],
    response_data: dict[str, Any],
) -> None:
    """
    Log an external API call with structured data.

    Args:
        logger: The logger instance to use
        service: Name of the external service (e.g., "WhatsApp", "M-PESA")
        endpoint: The API endpoint being called
        request_data: The request payload or parameters
        response_data: The response data received

    Example:
        >>> logger = get_logger(__name__)
        >>> log_api_call(
        ...     logger,
        ...     service="M-PESA",
        ...     endpoint="/v1/stkpush",
        ...     request_data={"phone": "254712345678", "amount": 100},
        ...     response_data={"status": "success", "checkout_id": "ws_CO_123"}
        ... )
    """
    logger.info(
        f"API call to {service}",
        extra={
            "service": service,
            "endpoint": endpoint,
            "request_data": request_data,
            "response_data": response_data,
        },
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