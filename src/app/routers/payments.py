"""
Payment API router for M-PESA STK Push operations.

This module provides API endpoints for initiating M-PESA STK Push payments
and handling payment callbacks.
"""

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db
from ..models import Invoice, Payment
from ..schemas import PaymentCreate, PaymentResponse
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


@router.post("/stk/initiate", response_model=PaymentResponse, status_code=200)
async def initiate_stk_push(
    payment_request: PaymentCreate,
    db: AsyncSession = Depends(get_db),
    mpesa_service: MPesaService = Depends(get_mpesa_service),
) -> PaymentResponse:
    """
    Initiate M-PESA STK Push payment.

    Validates invoice exists and has status "SENT", checks idempotency key
    to prevent duplicate charges, creates payment record, and initiates
    STK Push request to customer's phone.

    Args:
        payment_request: Payment creation request with invoice_id and idempotency_key
        db: Database session
        mpesa_service: M-PESA service instance

    Returns:
        PaymentResponse with payment details

    Raises:
        HTTPException 404: If invoice not found
        HTTPException 400: If invoice status is not "SENT"
        HTTPException 500: If STK Push initiation fails
    """
    logger.info(
        "Received STK Push initiate request",
        extra={
            "invoice_id": payment_request.invoice_id,
            "idempotency_key": payment_request.idempotency_key,
        },
    )

    # Check idempotency: if payment with same key exists, return cached response
    existing_payment_stmt = select(Payment).where(
        Payment.idempotency_key == payment_request.idempotency_key
    )
    existing_payment_result = await db.execute(existing_payment_stmt)
    existing_payment = existing_payment_result.scalar_one_or_none()

    if existing_payment:
        logger.info(
            "Duplicate STK Push request detected - returning cached response",
            extra={
                "idempotency_key": payment_request.idempotency_key,
                "payment_id": existing_payment.id,
            },
        )
        return PaymentResponse.model_validate(existing_payment)

    # Retrieve invoice
    invoice_stmt = select(Invoice).where(Invoice.id == payment_request.invoice_id)
    invoice_result = await db.execute(invoice_stmt)
    invoice = invoice_result.scalar_one_or_none()

    if not invoice:
        logger.warning(
            "Invoice not found",
            extra={"invoice_id": payment_request.invoice_id},
        )
        raise HTTPException(status_code=404, detail="Invoice not found")

    # Validate invoice status
    if invoice.status != "SENT":
        logger.warning(
            "Invalid invoice status for payment",
            extra={
                "invoice_id": invoice.id,
                "status": invoice.status,
                "expected_status": "SENT",
            },
        )
        raise HTTPException(
            status_code=400,
            detail=f"Invoice status must be SENT (current: {invoice.status})",
        )

    # Convert amount from cents to whole KES
    amount_kes = round(invoice.amount_cents / 100)

    logger.info(
        "Creating payment record",
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
        idempotency_key=payment_request.idempotency_key,
        raw_request={},  # Will be populated before STK call
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

    try:
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

        return PaymentResponse.model_validate(payment)

    except Exception as e:
        # Update payment status to FAILED
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
            "STK Push initiation failed",
            extra={
                "payment_id": payment.id,
                "invoice_id": invoice.id,
                "error": str(e),
            },
            exc_info=True,
        )

        raise HTTPException(
            status_code=500,
            detail=f"Failed to initiate STK Push: {str(e)}",
        )