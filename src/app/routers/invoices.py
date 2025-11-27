"""
Invoice router for InvoiceIQ.

This module handles invoice creation endpoints, including creating invoices,
sending them to customers via WhatsApp, and managing invoice status.
"""

import random
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from supabase import Client

from ..db import get_supabase
from ..schemas import InvoiceCreate, InvoiceResponse
from ..services.whatsapp import WhatsAppService
from ..utils.logging import get_logger

# Set up logger
logger = get_logger(__name__)

# Create router
router = APIRouter()

# Initialize rate limiter (uses client IP address as key)
limiter = Limiter(key_func=get_remote_address)


def generate_invoice_id() -> str:
    """
    Generate a unique invoice ID in the format: INV-{timestamp}-{random}.

    Returns:
        A unique invoice ID string (e.g., "INV-1699999999-1234")
    """
    timestamp = int(time.time())
    random_num = random.randint(1000, 9999)
    invoice_id = f"INV-{timestamp}-{random_num}"
    logger.debug(
        "Invoice ID generated",
        extra={"invoice_id": invoice_id, "timestamp": timestamp, "random": random_num},
    )
    return invoice_id


@router.get("/{invoice_id}", response_model=InvoiceResponse)
def get_invoice(
    invoice_id: str,
    supabase: Client = Depends(get_supabase),
) -> dict:
    """
    Get invoice details by ID.

    This endpoint is public (no authentication required) to allow customers
    to view invoices they received via WhatsApp link.

    Args:
        invoice_id: Invoice ID
        supabase: Supabase client

    Returns:
        Invoice details

    Raises:
        HTTPException: 404 if invoice not found
    """
    try:
        response = supabase.table("invoices").select("*").eq("id", invoice_id).execute()

        if not response.data:
            logger.warning(
                "Invoice not found for viewing",
                extra={"invoice_id": invoice_id},
            )
            raise HTTPException(
                status_code=404,
                detail="Invoice not found"
            )

        invoice = response.data[0]

        logger.info(
            "Invoice viewed",
            extra={"invoice_id": invoice_id, "status": invoice["status"]},
        )

        return invoice

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error fetching invoice",
            extra={"invoice_id": invoice_id, "error": str(e)},
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Database error"
        )


@router.post("", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def create_invoice(
    request: Request,
    invoice_data: InvoiceCreate,
    supabase: Client = Depends(get_supabase),
) -> dict:
    """
    Create a new invoice and send it to the customer via WhatsApp.

    Rate limiting: 10 requests per minute per IP address.
    Exceeding the limit returns HTTP 429 (Too Many Requests).

    This endpoint:
    1. Creates an invoice record in the database with PENDING status
    2. Sends the invoice to the customer via WhatsApp with "Pay with M-PESA" button
    3. Updates invoice status to SENT if delivery succeeds
    4. Returns the created invoice

    Args:
        request: The HTTP request (required for rate limiting)
        invoice_data: Invoice creation data (customer info, amount, description)
        supabase: Supabase client

    Returns:
        The created invoice with current status

    Raises:
        HTTPException: 429 if rate limit exceeded
        HTTPException: 400 if invoice data is invalid
        HTTPException: 500 if database operation fails
    """
    logger.info(
        "Creating invoice",
        extra={
            "msisdn": invoice_data.msisdn,
            "amount_cents": invoice_data.amount_cents,
            "customer_name": invoice_data.customer_name,
        },
    )

    try:
        # Generate invoice ID
        invoice_id = generate_invoice_id()

        # Calculate VAT (16% of total amount)
        # Total amount includes VAT, so VAT = (amount_cents * 16) / 116
        vat_amount = int((invoice_data.amount_cents * 16) / 116)

        # Create invoice record
        invoice_data_dict = {
            "id": invoice_id,
            "customer_name": invoice_data.customer_name,
            "msisdn": invoice_data.msisdn,
            "merchant_msisdn": invoice_data.merchant_msisdn,
            "amount_cents": invoice_data.amount_cents,
            "vat_amount": vat_amount,
            "currency": "KES",  # Hardcoded for MVP
            "description": invoice_data.description,
            "status": "PENDING",  # Initial status
            "pay_ref": None,  # Will be set in Phase 7
            "pay_link": None,  # Will be set in Phase 7
        }

        # Add to database
        response = supabase.table("invoices").insert(invoice_data_dict).execute()
        invoice = response.data[0]

        logger.info(
            "Invoice created in database",
            extra={
                "invoice_id": invoice["id"],
                "status": invoice["status"],
                "msisdn": invoice["msisdn"],
            },
        )

        # Initialize WhatsApp service
        whatsapp_service = WhatsAppService()

        # Send invoice to customer
        send_success = await whatsapp_service.send_invoice_to_customer(
            invoice_id=invoice["id"],
            customer_msisdn=invoice["msisdn"],
            customer_name=invoice["customer_name"],
            amount_cents=invoice["amount_cents"],
            db_session=supabase,
        )

        # Update invoice status based on send result
        if send_success:
            update_response = (
                supabase.table("invoices")
                .update({"status": "SENT"})
                .eq("id", invoice_id)
                .execute()
            )
            invoice = update_response.data[0]

            logger.info(
                "Invoice sent successfully and status updated",
                extra={"invoice_id": invoice["id"], "status": invoice["status"]},
            )
        else:
            logger.warning(
                "Invoice created but failed to send to customer - status remains PENDING",
                extra={"invoice_id": invoice["id"], "msisdn": invoice["msisdn"]},
            )

        return invoice

    except Exception as e:
        logger.error(
            "Failed to create invoice",
            extra={
                "error": str(e),
                "msisdn": invoice_data.msisdn,
                "amount_cents": invoice_data.amount_cents,
            },
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create invoice: {str(e)}",
        )