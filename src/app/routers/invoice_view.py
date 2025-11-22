"""
Invoice view router for customer-facing invoice display and payment.

This module provides public endpoints for customers to view invoices and
initiate M-PESA STK Push payments via a web interface.
"""

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db
from ..models import Invoice, Payment
from ..services.mpesa import MPesaService
from ..utils.logging import get_logger

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
    invoice: Invoice,
    payment_type: str,
    shortcode: str,
) -> str:
    """
    Generate HTML for invoice display.

    Args:
        invoice: Invoice object from database
        payment_type: "paybill" or "till"
        shortcode: M-PESA shortcode/business number

    Returns:
        HTML string for invoice display
    """
    # Calculate amounts
    total_amount = invoice.amount_cents / 100  # Convert from cents to KES
    vat_amount = invoice.vat_amount / 100  # Convert from cents to KES
    subtotal = total_amount - vat_amount

    # Determine payment details text
    if payment_type.lower() == "paybill":
        payment_details = f"Paybill: {shortcode}<br>Account: {invoice.id}"
    else:  # till
        payment_details = f"Till Number: {shortcode}"

    # Get merchant name (use merchant_msisdn as fallback)
    merchant_name = invoice.merchant_msisdn

    # Determine button state based on invoice status
    button_disabled = ""
    button_text = "Pay with M-PESA"

    if invoice.status == "PAID":
        button_disabled = "disabled"
        button_text = "Already Paid"
    elif invoice.status in ["CANCELLED", "FAILED"]:
        button_disabled = "disabled"
        button_text = f"Invoice {invoice.status.title()}"

    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Invoice {invoice.id}</title>
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
        function initiatePayment() {{
            const button = document.getElementById('pay-button');
            button.disabled = true;
            button.textContent = 'Processing...';
            window.location.href = '/pay/{invoice.id}';
        }}
    </script>
</head>
<body>
    <div class="container">
        <div class="invoice-card">
            <div class="invoice-header">
                <div class="invoice-title">Invoice</div>
                <div class="invoice-id">{invoice.id}</div>
                <div style="margin-top: 12px;">
                    <span class="status-badge status-{invoice.status.lower()}">{invoice.status}</span>
                </div>
            </div>

            <div class="invoice-section">
                <div class="section-label">Invoice From</div>
                <div class="section-value">{merchant_name}</div>
            </div>

            <div class="invoice-section">
                <div class="section-label">Invoice For</div>
                <div class="section-value">{invoice.description}</div>
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

            <button
                id="pay-button"
                class="pay-button"
                onclick="initiatePayment()"
                {button_disabled}
            >
                {button_text}
            </button>
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
async def view_invoice(
    invoice_id: str,
    db: AsyncSession = Depends(get_db),
) -> str:
    """
    Display invoice details in HTML format with payment button.

    Args:
        invoice_id: Invoice ID to display
        db: Database session

    Returns:
        HTML page with invoice details and payment button

    Raises:
        HTTPException: 404 if invoice not found
    """
    logger.info(
        "Invoice view requested",
        extra={"invoice_id": invoice_id},
    )

    # Lookup invoice in database
    invoice_stmt = select(Invoice).where(Invoice.id == invoice_id)
    invoice_result = await db.execute(invoice_stmt)
    invoice = invoice_result.scalar_one_or_none()

    if not invoice:
        logger.warning(
            "Invoice not found for view",
            extra={"invoice_id": invoice_id},
        )
        raise HTTPException(status_code=404, detail="Invoice not found")

    logger.info(
        "Displaying invoice",
        extra={
            "invoice_id": invoice.id,
            "status": invoice.status,
            "amount_cents": invoice.amount_cents,
        },
    )

    # Generate and return HTML
    html = generate_invoice_html(
        invoice=invoice,
        payment_type=settings.mpesa_payment_type,
        shortcode=settings.mpesa_shortcode,
    )

    return html


@router.get("/pay/{invoice_id}", response_class=HTMLResponse)
async def initiate_payment(
    invoice_id: str,
    db: AsyncSession = Depends(get_db),
    mpesa_service: MPesaService = Depends(get_mpesa_service),
) -> str:
    """
    Initiate M-PESA STK Push payment for an invoice.

    Args:
        invoice_id: Invoice ID to pay
        db: Database session
        mpesa_service: M-PESA service instance

    Returns:
        HTML page with success or error message

    Raises:
        HTTPException: 404 if invoice not found
    """
    logger.info(
        "Payment initiation requested",
        extra={"invoice_id": invoice_id},
    )

    # Lookup invoice
    invoice_stmt = select(Invoice).where(Invoice.id == invoice_id)
    invoice_result = await db.execute(invoice_stmt)
    invoice = invoice_result.scalar_one_or_none()

    if not invoice:
        logger.warning(
            "Invoice not found for payment",
            extra={"invoice_id": invoice_id},
        )
        raise HTTPException(status_code=404, detail="Invoice not found")

    # Check invoice status
    if invoice.status == "PAID":
        logger.info(
            "Invoice already paid",
            extra={"invoice_id": invoice_id},
        )
        return generate_error_html(
            invoice_id=invoice_id,
            error_message="This invoice has already been paid.",
        )

    if invoice.status in ["CANCELLED", "FAILED"]:
        logger.info(
            "Invoice cannot be paid due to status",
            extra={"invoice_id": invoice_id, "status": invoice.status},
        )
        return generate_error_html(
            invoice_id=invoice_id,
            error_message=f"This invoice is {invoice.status.lower()} and cannot be paid.",
        )

    # Check if payment already exists for this invoice
    existing_payment_stmt = select(Payment).where(
        Payment.invoice_id == invoice_id,
        Payment.status == "INITIATED",
    )
    existing_payment_result = await db.execute(existing_payment_stmt)
    existing_payment = existing_payment_result.scalar_one_or_none()

    if existing_payment:
        logger.info(
            "Payment already initiated for this invoice",
            extra={"invoice_id": invoice_id, "payment_id": existing_payment.id},
        )
        return generate_payment_success_html(invoice_id=invoice_id)

    # Create payment record and initiate STK Push
    try:
        # Generate idempotency key
        idempotency_key = f"{invoice_id}-{str(uuid4())[:8]}"

        # Convert amount from cents to whole KES
        amount_kes = round(invoice.amount_cents / 100)

        logger.info(
            "Creating payment record and initiating STK Push",
            extra={
                "invoice_id": invoice.id,
                "amount_cents": invoice.amount_cents,
                "amount_kes": amount_kes,
                "customer_msisdn": invoice.msisdn,
            },
        )

        # Create Payment record with status INITIATED
        payment = Payment(
            id=str(uuid4()),
            invoice_id=invoice.id,
            method="MPESA_STK",
            status="INITIATED",
            amount_cents=invoice.amount_cents,
            idempotency_key=idempotency_key,
            raw_request={},
            raw_callback=None,
            mpesa_receipt=None,
        )

        db.add(payment)
        await db.commit()
        await db.refresh(payment)

        logger.info(
            "Payment record created",
            extra={"payment_id": payment.id, "status": payment.status},
        )

        # Prepare STK Push request
        account_reference = invoice.id[:20]  # Max 20 characters
        transaction_desc = invoice.description[:20]  # Max 20 characters

        # Initiate STK Push
        stk_response = await mpesa_service.initiate_stk_push(
            phone_number=invoice.msisdn,
            amount=amount_kes,
            account_reference=account_reference,
            transaction_desc=transaction_desc,
        )

        # Update payment with raw request and response
        payment.raw_request = {
            "phone_number": invoice.msisdn,
            "amount": amount_kes,
            "account_reference": account_reference,
            "transaction_desc": transaction_desc,
            "stk_response": stk_response,
        }

        # Store CheckoutRequestID and MerchantRequestID for callback matching
        payment.checkout_request_id = stk_response.get("CheckoutRequestID")
        payment.merchant_request_id = stk_response.get("MerchantRequestID")

        await db.commit()
        await db.refresh(payment)

        logger.info(
            "STK Push initiated successfully",
            extra={
                "payment_id": payment.id,
                "invoice_id": invoice.id,
                "stk_response": stk_response,
            },
        )

        return generate_payment_success_html(invoice_id=invoice_id)

    except Exception as e:
        # Update payment status to FAILED if it was created
        if "payment" in locals():
            payment.status = "FAILED"
            payment.raw_request = {
                "phone_number": invoice.msisdn,
                "amount": amount_kes,
                "account_reference": account_reference,
                "transaction_desc": transaction_desc,
                "error": str(e),
            }
            await db.commit()

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
