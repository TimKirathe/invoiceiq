"""
Invoice view router for customer-facing invoice display and payment.

This module provides public endpoints for customers to view invoices and
initiate M-PESA STK Push payments via a web interface.
"""

from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse
from supabase import Client

from ..config import settings
from ..db import get_supabase
from ..services.mpesa import MPesaService
from ..utils.logging import get_logger
from ..utils.payment_retry import (
    can_retry_payment,
    get_payment_by_invoice_id,
    reset_invoice_to_pending,
)

logger = get_logger(__name__)

router = APIRouter()


def get_mpesa_service() -> MPesaService:
    """
    Dependency to get MPesaService instance.

    Returns:
        MPesaService instance configured with environment from settings
    """
    return MPesaService(environment=settings.mpesa_environment)


def generate_invoice_html(
    invoice: dict,
) -> str:
    """
    Generate HTML for invoice display.

    Args:
        invoice: Invoice dict from database

    Returns:
        HTML string for invoice display
    """
    # Calculate amounts
    total_amount = invoice["amount_cents"] / 100  # Convert from cents to KES
    vat_amount = invoice["vat_amount"] / 100  # Convert from cents to KES
    subtotal = total_amount - vat_amount

    # Determine payment details text based on invoice's mpesa_method
    mpesa_method = invoice.get("mpesa_method")
    if mpesa_method == "PAYBILL":
        paybill_number = invoice.get("mpesa_paybill_number", "N/A")
        account_number = invoice.get("mpesa_account_number", "N/A")
        payment_details = f"Paybill: {paybill_number}<br>Account: {account_number}"
    elif mpesa_method == "TILL":
        till_number = invoice.get("mpesa_till_number", "N/A")
        payment_details = f"Till Number: {till_number}"
    elif mpesa_method == "PHONE":
        phone_number = invoice.get("mpesa_phone_number", "N/A")
        payment_details = f"Send to: {phone_number}"
    else:
        # Fallback for missing or unexpected payment method
        payment_details = "Payment details not available"

    # Get merchant name (use merchant_msisdn as fallback)
    merchant_name = invoice["merchant_name"]

    # Format line items for display - show all items in full detail
    line_items = invoice.get("line_items")
    if line_items and isinstance(line_items, list) and len(line_items) > 0:
        # Format each item with full detail: "Item Name – KES X.XX (x Quantity)"
        formatted_items_list = []
        for item in line_items:
            unit_price_kes = item["unit_price_cents"] / 100
            formatted_item = (
                f"{item['name']} – KES {unit_price_kes:,.2f} (x{item['quantity']})"
            )
            formatted_items_list.append(formatted_item)
        # Join with HTML line breaks for proper display
        formatted_items = "<br>".join(formatted_items_list)
    else:
        formatted_items = "No items specified"

    # Determine button state based on invoice status
    button_disabled = ""
    button_text = "Pay with M-PESA"

    if invoice["status"] == "PAID":
        button_disabled = "disabled"
        button_text = "Already Paid"
    elif invoice["status"] in ["CANCELLED", "FAILED"]:
        button_disabled = "disabled"
        button_text = f"Invoice {invoice['status'].title()}"

    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Invoice {invoice["id"]}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
            background-color: #f5f5f5;
            padding: 20px;
            line-height: 1.6;
        }}

        .container {{
            max-width: 600px;
            margin: 0 auto;
        }}

        .invoice-card {{
            background: white;
            border-radius: 12px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
            padding: 30px;
            margin-bottom: 20px;
        }}

        .invoice-header {{
            border-bottom: 2px solid #e0e0e0;
            padding-bottom: 20px;
            margin-bottom: 25px;
        }}

        .invoice-title {{
            font-size: 24px;
            font-weight: 600;
            color: #333;
            margin-bottom: 8px;
        }}

        .invoice-id {{
            font-size: 14px;
            color: #666;
            font-family: monospace;
        }}

        .invoice-section {{
            margin-bottom: 25px;
        }}

        .section-label {{
            font-size: 12px;
            color: #888;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
        }}

        .section-value {{
            font-size: 16px;
            color: #333;
            font-weight: 500;
        }}

        .amount-breakdown {{
            background-color: #f9f9f9;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 25px;
        }}

        .amount-row {{
            display: flex;
            justify-content: space-between;
            margin-bottom: 12px;
            font-size: 15px;
        }}

        .amount-row.total {{
            border-top: 2px solid #ddd;
            padding-top: 12px;
            margin-top: 12px;
            font-size: 18px;
            font-weight: 600;
            color: #000;
        }}

        .amount-label {{
            color: #666;
        }}

        .amount-value {{
            color: #333;
            font-weight: 500;
        }}

        .payment-details {{
            background-color: #f0f7ff;
            border-left: 4px solid #2196F3;
            padding: 15px;
            margin-bottom: 25px;
            border-radius: 4px;
        }}

        .payment-details-label {{
            font-size: 12px;
            color: #1976D2;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
        }}

        .payment-details-value {{
            font-size: 15px;
            color: #1565C0;
            font-weight: 500;
            line-height: 1.5;
        }}

        .pay-button {{
            background: #25D366;
            color: white;
            border: none;
            padding: 16px 32px;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            width: 100%;
            transition: background-color 0.3s ease;
        }}

        .pay-button:hover:not(:disabled) {{
            background: #1fb855;
        }}

        .pay-button:active:not(:disabled) {{
            background: #1aa84a;
        }}

        .pay-button:disabled {{
            background: #ccc;
            cursor: not-allowed;
        }}

        .status-badge {{
            display: inline-block;
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .status-pending {{
            background-color: #FFF3CD;
            color: #856404;
        }}

        .status-sent {{
            background-color: #CCE5FF;
            color: #004085;
        }}

        .status-paid {{
            background-color: #D4EDDA;
            color: #155724;
        }}

        .status-failed,
        .status-cancelled {{
            background-color: #F8D7DA;
            color: #721C24;
        }}

        .footer {{
            text-align: center;
            color: #888;
            font-size: 13px;
            margin-top: 30px;
        }}

        .phone-selection {{
            background-color: #f9f9f9;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 25px;
        }}

        .phone-selection-label {{
            font-size: 14px;
            color: #333;
            font-weight: 600;
            margin-bottom: 15px;
        }}

        .radio-option {{
            display: flex;
            align-items: center;
            margin-bottom: 12px;
            cursor: pointer;
        }}

        .radio-option input[type="radio"] {{
            margin-right: 10px;
            cursor: pointer;
            width: 18px;
            height: 18px;
        }}

        .radio-option label {{
            cursor: pointer;
            font-size: 15px;
            color: #333;
            flex: 1;
        }}

        .phone-display {{
            font-family: monospace;
            color: #666;
            margin-left: 28px;
            font-size: 14px;
        }}

        .custom-phone-input {{
            margin-left: 28px;
            margin-top: 8px;
            display: flex;
            align-items: center;
        }}

        .phone-prefix {{
            background-color: #e0e0e0;
            padding: 10px 12px;
            border: 1px solid #ccc;
            border-right: none;
            border-radius: 6px 0 0 6px;
            font-size: 15px;
            color: #333;
            font-family: monospace;
        }}

        .phone-input {{
            flex: 1;
            padding: 10px 12px;
            border: 1px solid #ccc;
            border-radius: 0 6px 6px 0;
            font-size: 15px;
            font-family: monospace;
        }}

        .phone-input:disabled {{
            background-color: #f5f5f5;
            color: #999;
            cursor: not-allowed;
        }}

        .phone-input:focus {{
            outline: none;
            border-color: #2196F3;
        }}

        .validation-error {{
            color: #dc3545;
            font-size: 13px;
            margin-top: 8px;
            margin-left: 28px;
            display: none;
        }}

        @media (max-width: 600px) {{
            body {{
                padding: 10px;
            }}

            .invoice-card {{
                padding: 20px;
            }}

            .invoice-title {{
                font-size: 20px;
            }}
        }}
    </style>
    <script>
        function togglePhoneInput() {{
            const whatsappRadio = document.getElementById('use-whatsapp');
            const customInput = document.getElementById('custom-phone');
            const customPhoneField = document.getElementById('custom-phone-field');

            if (whatsappRadio.checked) {{
                customInput.disabled = true;
                customInput.value = '';
                customPhoneField.style.display = 'none';
            }} else {{
                customInput.disabled = false;
                customPhoneField.style.display = 'block';
                customInput.focus();
            }}
        }}

        function formatPhoneInput(input) {{
            // Remove all non-digits
            let value = input.value.replace(/\D/g, '');

            // Limit to 9 digits
            if (value.length > 9) {{
                value = value.substring(0, 9);
            }}

            // Format with spaces: XXX XXX XXX
            let formatted = '';
            for (let i = 0; i < value.length; i++) {{
                if (i > 0 && i % 3 === 0) {{
                    formatted += ' ';
                }}
                formatted += value[i];
            }}

            input.value = formatted;
        }}

        function validateAndSubmit(event) {{
            event.preventDefault();

            const form = document.getElementById('payment-form');
            const whatsappRadio = document.getElementById('use-whatsapp');
            const customRadio = document.getElementById('use-custom');
            const customInput = document.getElementById('custom-phone');
            const errorDiv = document.getElementById('validation-error');
            const submitButton = document.getElementById('pay-button');
            const paymentPhoneInput = document.getElementById('payment-phone');

            // Clear previous errors
            errorDiv.style.display = 'none';

            let phoneNumber = '';

            if (whatsappRadio.checked) {{
                // Use WhatsApp number
                phoneNumber = '{invoice["msisdn"]}';
            }} else if (customRadio.checked) {{
                // Validate custom number
                const digits = customInput.value.replace(/\D/g, '');

                if (digits.length !== 9) {{
                    errorDiv.textContent = 'Please enter a valid 9-digit phone number (e.g., 712 345 678)';
                    errorDiv.style.display = 'block';
                    return false;
                }}

                phoneNumber = '254' + digits;
            }}

            // Set the payment phone value
            paymentPhoneInput.value = phoneNumber;

            // Disable button and submit
            submitButton.disabled = true;
            submitButton.textContent = 'Processing...';
            form.submit();

            return true;
        }}

        // Initialize on page load
        document.addEventListener('DOMContentLoaded', function() {{
            const whatsappRadio = document.getElementById('use-whatsapp');
            const customRadio = document.getElementById('use-custom');
            const customInput = document.getElementById('custom-phone');

            whatsappRadio.addEventListener('change', togglePhoneInput);
            customRadio.addEventListener('change', togglePhoneInput);
            customInput.addEventListener('input', function() {{
                formatPhoneInput(this);
            }});

            // Initialize state
            togglePhoneInput();
        }});
    </script>
</head>
<body>
    <div class="container">
        <div class="invoice-card">
            <div class="invoice-header">
                <div class="invoice-title">Invoice</div>
                <div class="invoice-id">{invoice["id"]}</div>
                <div style="margin-top: 12px;">
                    <span class="status-badge status-{invoice["status"].lower()}">{invoice["status"]}</span>
                </div>
            </div>

            <div class="invoice-section">
                <div class="section-label">Invoice From</div>
                <div class="section-value">{merchant_name}</div>
            </div>

            <div class="invoice-section">
                <div class="section-label">Invoice For</div>
                <div class="section-value">{formatted_items}</div>
            </div>

            <div class="amount-breakdown">
                <div class="amount-row">
                    <span class="amount-label">Subtotal</span>
                    <span class="amount-value">KES {subtotal:,.2f}</span>
                </div>
                <div class="amount-row">
                    <span class="amount-label">VAT (16%)</span>
                    <span class="amount-value">KES {vat_amount:,.2f}</span>
                </div>
                <div class="amount-row total">
                    <span class="amount-label">Total Amount</span>
                    <span class="amount-value">KES {total_amount:,.2f}</span>
                </div>
            </div>

            <div class="invoice-section">
                <div class="section-label">Due Date</div>
                <div class="section-value">Due on receipt</div>
            </div>

            <div class="payment-details">
                <div class="payment-details-label">M-PESA Payment Details</div>
                <div class="payment-details-value">{payment_details}</div>
            </div>

            <form id="payment-form" method="POST" action="/pay/{invoice["id"]}" onsubmit="return validateAndSubmit(event);">
                <div class="phone-selection">
                    <div class="phone-selection-label">STK push will be sent to:</div>

                    <div class="radio-option">
                        <input
                            type="radio"
                            id="use-whatsapp"
                            name="phone-option"
                            value="whatsapp"
                            checked
                        >
                        <label for="use-whatsapp">Use WhatsApp number</label>
                    </div>
                    <div class="phone-display">+{invoice["msisdn"]}</div>

                    <div class="radio-option" style="margin-top: 15px;">
                        <input
                            type="radio"
                            id="use-custom"
                            name="phone-option"
                            value="custom"
                        >
                        <label for="use-custom">Use different number</label>
                    </div>
                    <div id="custom-phone-field" class="custom-phone-input" style="display: none;">
                        <span class="phone-prefix">+254</span>
                        <input
                            type="text"
                            id="custom-phone"
                            class="phone-input"
                            placeholder="712 345 678"
                            maxlength="11"
                            disabled
                        >
                    </div>
                    <div id="validation-error" class="validation-error"></div>
                </div>

                <input type="hidden" id="payment-phone" name="payment_phone" value="">

                <button
                    id="pay-button"
                    type="submit"
                    class="pay-button"
                    {button_disabled}
                >
                    {button_text}
                </button>
            </form>
        </div>

        <div class="footer">
            Powered by InvoiceIQ
        </div>
    </div>
</body>
</html>
    """

    return html


def generate_payment_success_html(invoice_id: str) -> str:
    """
    Generate HTML for payment initiation success page.

    Args:
        invoice_id: Invoice ID for reference

    Returns:
        HTML string for success page
    """
    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Payment Initiated</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
            background-color: #f5f5f5;
            padding: 20px;
            line-height: 1.6;
        }}

        .container {{
            max-width: 600px;
            margin: 0 auto;
        }}

        .success-card {{
            background: white;
            border-radius: 12px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
            padding: 40px 30px;
            text-align: center;
        }}

        .success-icon {{
            width: 80px;
            height: 80px;
            margin: 0 auto 20px;
            background-color: #25D366;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
        }}

        .success-icon::before {{
            content: "✓";
            font-size: 48px;
            color: white;
            font-weight: bold;
        }}

        .success-title {{
            font-size: 24px;
            font-weight: 600;
            color: #333;
            margin-bottom: 15px;
        }}

        .success-message {{
            font-size: 16px;
            color: #666;
            margin-bottom: 10px;
            line-height: 1.6;
        }}

        .invoice-ref {{
            font-size: 14px;
            color: #888;
            font-family: monospace;
            margin-top: 20px;
            padding-top: 20px;
            border-top: 1px solid #e0e0e0;
        }}

        .back-button {{
            margin-top: 30px;
            display: inline-block;
            padding: 12px 24px;
            background-color: #2196F3;
            color: white;
            text-decoration: none;
            border-radius: 6px;
            font-weight: 500;
            transition: background-color 0.3s ease;
        }}

        .back-button:hover {{
            background-color: #1976D2;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="success-card">
            <div class="success-icon"></div>
            <div class="success-title">Payment Request Sent!</div>
            <div class="success-message">
                Please check your phone for the M-PESA prompt.
                <br><br>
                Enter your M-PESA PIN to complete the payment.
            </div>
            <div class="invoice-ref">
                Invoice: {invoice_id}
            </div>
            <a href="/{invoice_id}" class="back-button">Back to Invoice</a>
        </div>
    </div>
</body>
</html>
    """

    return html


def generate_error_html(invoice_id: str, error_message: str) -> str:
    """
    Generate HTML for payment error page.

    Args:
        invoice_id: Invoice ID for reference
        error_message: Error message to display

    Returns:
        HTML string for error page
    """
    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Payment Error</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
            background-color: #f5f5f5;
            padding: 20px;
            line-height: 1.6;
        }}

        .container {{
            max-width: 600px;
            margin: 0 auto;
        }}

        .error-card {{
            background: white;
            border-radius: 12px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
            padding: 40px 30px;
            text-align: center;
        }}

        .error-icon {{
            width: 80px;
            height: 80px;
            margin: 0 auto 20px;
            background-color: #dc3545;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
        }}

        .error-icon::before {{
            content: "✕";
            font-size: 48px;
            color: white;
            font-weight: bold;
        }}

        .error-title {{
            font-size: 24px;
            font-weight: 600;
            color: #333;
            margin-bottom: 15px;
        }}

        .error-message {{
            font-size: 16px;
            color: #666;
            margin-bottom: 10px;
            line-height: 1.6;
        }}

        .error-details {{
            font-size: 14px;
            color: #888;
            background-color: #f8f9fa;
            padding: 15px;
            border-radius: 6px;
            margin-top: 20px;
            font-family: monospace;
        }}

        .retry-button {{
            margin-top: 30px;
            display: inline-block;
            padding: 12px 24px;
            background-color: #25D366;
            color: white;
            text-decoration: none;
            border-radius: 6px;
            font-weight: 500;
            transition: background-color 0.3s ease;
        }}

        .retry-button:hover {{
            background-color: #1fb855;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="error-card">
            <div class="error-icon"></div>
            <div class="error-title">Payment Could Not Be Initiated</div>
            <div class="error-message">
                We encountered an error while trying to send the payment request.
                <br><br>
                Please try again or contact support if the issue persists.
            </div>
            <div class="error-details">
                {error_message}
            </div>
            <a href="/{invoice_id}" class="retry-button">Try Again</a>
        </div>
    </div>
</body>
</html>
    """

    return html


@router.get("/{invoice_id}", response_class=HTMLResponse)
def view_invoice(
    invoice_id: str,
    supabase: Client = Depends(get_supabase),
) -> str:
    """
    Display invoice details in HTML format with payment button.

    Args:
        invoice_id: Invoice ID to display
        supabase: Supabase client

    Returns:
        HTML page with invoice details and payment button

    Raises:
        HTTPException: 404 if invoice not found
    """
    logger.info(
        "Invoice view requested",
        extra={"invoice_id": invoice_id},
    )

    try:
        # Lookup invoice in database
        response = supabase.table("invoices").select("*").eq("id", invoice_id).execute()

        if not response.data:
            logger.warning(
                "Invoice not found for view",
                extra={"invoice_id": invoice_id},
            )
            raise HTTPException(status_code=404, detail="Invoice not found")

        invoice = response.data[0]

        logger.info(
            "Displaying invoice",
            extra={
                "invoice_id": invoice["id"],
                "status": invoice["status"],
                "amount_cents": invoice["amount_cents"],
            },
        )

        # Generate and return HTML
        html = generate_invoice_html(
            invoice=invoice,
        )

        return html

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error fetching invoice",
            extra={"invoice_id": invoice_id, "error": str(e)},
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Database error")


@router.post("/pay/{invoice_id}", response_class=HTMLResponse)
async def initiate_payment(
    invoice_id: str,
    payment_phone: str = Form(...),
    supabase: Client = Depends(get_supabase),
    mpesa_service: MPesaService = Depends(get_mpesa_service),
) -> str:
    """
    Initiate M-PESA STK Push payment for an invoice.

    Args:
        invoice_id: Invoice ID to pay
        payment_phone: Phone number to receive STK Push (format: 254XXXXXXXXX)
        supabase: Supabase client
        mpesa_service: M-PESA service instance

    Returns:
        HTML page with success or error message

    Raises:
        HTTPException: 404 if invoice not found
    """
    logger.info(
        "Payment initiation requested",
        extra={"invoice_id": invoice_id, "payment_phone": payment_phone},
    )

    # Validate payment phone format
    if (
        not payment_phone
        or len(payment_phone) != 12
        or not payment_phone.startswith("254")
    ):
        logger.warning(
            "Invalid payment phone format",
            extra={"invoice_id": invoice_id, "payment_phone": payment_phone},
        )
        return generate_error_html(
            invoice_id=invoice_id,
            error_message="Invalid phone number format. Must be 254XXXXXXXXX (12 digits).",
        )

    try:
        # Lookup invoice
        response = supabase.table("invoices").select("*").eq("id", invoice_id).execute()

        if not response.data:
            logger.warning(
                "Invoice not found for payment",
                extra={"invoice_id": invoice_id},
            )
            raise HTTPException(status_code=404, detail="Invoice not found")

        invoice = response.data[0]

        # Check invoice status
        if invoice["status"] == "PAID":
            logger.info(
                "Invoice already paid",
                extra={"invoice_id": invoice_id},
            )
            return generate_error_html(
                invoice_id=invoice_id,
                error_message="This invoice has already been paid.",
            )

        if invoice["status"] == "CANCELLED":
            logger.info(
                "Invoice cancelled",
                extra={"invoice_id": invoice_id},
            )
            return generate_error_html(
                invoice_id=invoice_id,
                error_message="This invoice has been cancelled and cannot be paid.",
            )

        # Handle FAILED status with retry logic
        if invoice["status"] == "FAILED":
            logger.info(
                "Attempting payment retry for FAILED invoice",
                extra={"invoice_id": invoice_id},
            )

            # Get existing payment record
            existing_payment = get_payment_by_invoice_id(invoice_id, supabase)

            if not existing_payment:
                logger.warning(
                    "No payment record found for FAILED invoice",
                    extra={"invoice_id": invoice_id},
                )
                return generate_error_html(
                    invoice_id=invoice_id,
                    error_message="Payment record not found. Please contact support.",
                )

            # Check if retry is allowed
            can_retry, error_message = can_retry_payment(existing_payment)

            if not can_retry:
                logger.info(
                    "Payment retry blocked",
                    extra={"invoice_id": invoice_id, "reason": error_message},
                )
                return generate_error_html(
                    invoice_id=invoice_id,
                    error_message=error_message,
                )

            # Retry is allowed - reset invoice status to PENDING
            if not reset_invoice_to_pending(invoice_id, supabase):
                logger.error(
                    "Failed to reset invoice status for retry",
                    extra={"invoice_id": invoice_id},
                )
                return generate_error_html(
                    invoice_id=invoice_id,
                    error_message="Failed to reset invoice status. Please try again.",
                )

            # Update invoice object for processing below
            invoice["status"] = "PENDING"

            # Increment retry count on existing payment
            current_retry_count = existing_payment.get("retry_count", 0)
            try:
                supabase.table("payments").update(
                    {"retry_count": current_retry_count + 1}
                ).eq("id", existing_payment["id"]).execute()

                logger.info(
                    "Incremented payment retry_count for retry attempt",
                    extra={
                        "payment_id": existing_payment["id"],
                        "new_retry_count": current_retry_count + 1,
                    },
                )
            except Exception as retry_error:
                logger.error(
                    "Failed to increment retry_count",
                    extra={
                        "error": str(retry_error),
                        "payment_id": existing_payment["id"],
                    },
                    exc_info=True,
                )
                return generate_error_html(
                    invoice_id=invoice_id,
                    error_message="Failed to update payment retry count. Please try again.",
                )

            logger.info(
                "Payment retry approved - proceeding with STK Push",
                extra={
                    "invoice_id": invoice_id,
                    "retry_count": current_retry_count + 1,
                },
            )

        # Check if payment already exists for this invoice
        payment_response = (
            supabase.table("payments")
            .select("*")
            .eq("invoice_id", invoice_id)
            .eq("status", "INITIATED")
            .execute()
        )

        if payment_response.data:
            existing_payment = payment_response.data[0]
            logger.info(
                "Payment already initiated for this invoice",
                extra={"invoice_id": invoice_id, "payment_id": existing_payment["id"]},
            )
            return generate_payment_success_html(invoice_id=invoice_id)

        # Create payment record and initiate STK Push
        # Generate idempotency key
        idempotency_key = f"{invoice_id}-{str(uuid4())[:8]}"

        # Convert amount from cents to whole KES
        amount_kes = round(invoice["amount_cents"] / 100)

        logger.info(
            "Creating payment record and initiating STK Push",
            extra={
                "invoice_id": invoice["id"],
                "amount_cents": invoice["amount_cents"],
                "amount_kes": amount_kes,
                "customer_msisdn": invoice["msisdn"],
            },
        )

        # Create Payment record with status INITIATED
        payment_id = str(uuid4())
        payment_data = {
            "id": payment_id,
            "invoice_id": invoice["id"],
            "method": "MPESA_STK",
            "status": "INITIATED",
            "amount_cents": invoice["amount_cents"],
            "idempotency_key": idempotency_key,
            "raw_request": {},
            "raw_callback": None,
            "mpesa_receipt": None,
        }

        create_payment_response = (
            supabase.table("payments").insert(payment_data).execute()
        )
        payment = create_payment_response.data[0]

        logger.info(
            "Payment record created",
            extra={"payment_id": payment["id"], "status": payment["status"]},
        )

        # Prepare STK Push request
        # Determine account_reference based on payment method
        payment_method = invoice.get("mpesa_method")
        if payment_method == "PAYBILL":
            # For PAYBILL: use the merchant's paybill account number
            account_reference = invoice.get("mpesa_account_number", invoice["id"][:20])
        else:
            # For TILL (or fallback): use invoice ID
            account_reference = invoice["id"][:20]

        # Ensure account_reference is max 20 characters
        account_reference = account_reference[:20]

        # Generate transaction description from line_items (max 20 characters)
        line_items = invoice.get("line_items")
        if line_items and isinstance(line_items, list) and len(line_items) > 0:
            # Use first item name
            first_item_name = line_items[0]["name"]
            if len(line_items) > 1:
                # Multiple items: "Item & N more"
                desc = f"{first_item_name} & {len(line_items) - 1} more"
            else:
                # Single item: just use the name
                desc = first_item_name
            # Truncate to 20 characters if needed
            transaction_desc = desc[:20]
        else:
            # Fallback if no line items
            transaction_desc = "Invoice payment"

        # Initiate STK Push
        # Pass payment method from invoice (PAYBILL or TILL)
        payment_method = invoice.get("mpesa_method")

        # Validate that payment method is not PHONE (STK Push only supports PAYBILL and TILL)
        if payment_method == "PHONE":
            logger.error(
                "STK Push not supported for PHONE payment method",
                extra={
                    "invoice_id": invoice["id"],
                    "payment_method": payment_method,
                },
            )
            raise HTTPException(
                status_code=400,
                detail="STK Push is not supported for PHONE payment method. Only PAYBILL and TILL are supported.",
            )

        stk_response = await mpesa_service.initiate_stk_push(
            phone_number=payment_phone,
            amount=amount_kes,
            account_reference=account_reference,
            transaction_desc=transaction_desc,
            payment_method=payment_method,
        )

        # Update payment with raw request and response
        update_data = {
            "raw_request": {
                "phone_number": payment_phone,
                "amount": amount_kes,
                "account_reference": account_reference,
                "transaction_desc": transaction_desc,
                "stk_response": stk_response,
            },
            "checkout_request_id": stk_response.get("CheckoutRequestID"),
            "merchant_request_id": stk_response.get("MerchantRequestID"),
        }

        supabase.table("payments").update(update_data).eq("id", payment_id).execute()

        logger.info(
            "STK Push initiated successfully",
            extra={
                "payment_id": payment["id"],
                "invoice_id": invoice["id"],
                "stk_response": stk_response,
            },
        )

        return generate_payment_success_html(invoice_id=invoice_id)

    except HTTPException:
        raise
    except Exception as e:
        # Update payment status to FAILED if it was created
        if "payment" in locals():
            try:
                supabase.table("payments").update(
                    {
                        "status": "FAILED",
                        "raw_request": {
                            "phone_number": payment_phone,
                            "amount": amount_kes,
                            "account_reference": account_reference,
                            "transaction_desc": transaction_desc,
                            "error": str(e),
                        },
                    }
                ).eq("id", payment["id"]).execute()
            except Exception:
                pass  # Ignore errors during error handling

        logger.error(
            "Failed to initiate STK Push",
            extra={
                "invoice_id": invoice_id,
                "error": str(e),
            },
            exc_info=True,
        )

        return generate_error_html(
            invoice_id=invoice_id,
            error_message=str(e),
        )
