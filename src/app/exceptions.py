"""
Custom exception classes for InvoiceIQ.

This module defines domain-specific exceptions for better error handling
and reporting across the application.
"""


class InvoiceNotFound(Exception):
    """
    Exception raised when an invoice cannot be found.

    Attributes:
        invoice_id: The ID of the invoice that was not found
        message: Explanation of the error
    """

    def __init__(self, invoice_id: str, message: str = "Invoice not found") -> None:
        """
        Initialize InvoiceNotFound exception.

        Args:
            invoice_id: The ID of the invoice that was not found
            message: Custom error message (optional)
        """
        self.invoice_id = invoice_id
        self.message = f"{message}: {invoice_id}"
        super().__init__(self.message)

    def __str__(self) -> str:
        """Return string representation of the exception."""
        return f"InvoiceNotFound(invoice_id={self.invoice_id}, message={self.message})"


class PaymentFailed(Exception):
    """
    Exception raised when a payment operation fails.

    Attributes:
        invoice_id: The ID of the invoice associated with the failed payment
        reason: The reason for the payment failure
        message: Explanation of the error
    """

    def __init__(
        self, invoice_id: str, reason: str = "Unknown", message: str = "Payment failed"
    ) -> None:
        """
        Initialize PaymentFailed exception.

        Args:
            invoice_id: The ID of the invoice associated with the failed payment
            reason: The reason for the payment failure
            message: Custom error message (optional)
        """
        self.invoice_id = invoice_id
        self.reason = reason
        self.message = f"{message} for invoice {invoice_id}: {reason}"
        super().__init__(self.message)

    def __str__(self) -> str:
        """Return string representation of the exception."""
        return (
            f"PaymentFailed(invoice_id={self.invoice_id}, "
            f"reason={self.reason}, message={self.message})"
        )


class InvalidMSISDN(Exception):
    """
    Exception raised when a phone number (MSISDN) is invalid.

    Attributes:
        msisdn: The invalid MSISDN
        message: Explanation of the error
    """

    def __init__(self, msisdn: str, message: str = "Invalid MSISDN format") -> None:
        """
        Initialize InvalidMSISDN exception.

        Args:
            msisdn: The invalid MSISDN
            message: Custom error message (optional)
        """
        self.msisdn = msisdn
        self.message = f"{message}: {msisdn}"
        super().__init__(self.message)

    def __str__(self) -> str:
        """Return string representation of the exception."""
        return f"InvalidMSISDN(msisdn={self.msisdn}, message={self.message})"