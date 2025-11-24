"""
Payment API router for M-PESA STK Push operations.

This module provides API endpoints for initiating M-PESA STK Push payments
and handling payment callbacks.
"""

from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db
from ..models import Invoice, Payment
from ..schemas import PaymentCreate, PaymentResponse
from ..services.idempotency import check_callback_processed
from ..services.mpesa import MPesaService
from ..services.whatsapp import WhatsAppService
from ..utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


def parse_callback_payload(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Parse M-PESA STK Push callback payload.

    Extracts relevant fields from the callback structure:
    - Body.stkCallback.MerchantRequestID
    - Body.stkCallback.CheckoutRequestID
    - Body.stkCallback.ResultCode
    - Body.stkCallback.ResultDesc
    - Body.stkCallback.CallbackMetadata.Item (if ResultCode == 0)

    Args:
        payload: Raw callback payload from M-PESA

    Returns:
        Parsed dict with extracted fields, or None if parsing fails

    Example successful callback:
        {
            "Body": {
                "stkCallback": {
                    "MerchantRequestID": "29115-34620561-1",
                    "CheckoutRequestID": "ws_CO_191220191020363925",
                    "ResultCode": 0,
                    "ResultDesc": "The service request is processed successfully.",
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "Amount", "Value": 1.00},
                            {"Name": "MpesaReceiptNumber", "Value": "NLJ7RT61SV"},
                            {"Name": "TransactionDate", "Value": 20191219102115},
                            {"Name": "PhoneNumber", "Value": 254708374149}
                        ]
                    }
                }
            }
        }

    Example failed callback:
        {
            "Body": {
                "stkCallback": {
                    "MerchantRequestID": "92334-77894064-1",
                    "CheckoutRequestID": "ws_CO_04112024174011655708374149",
                    "ResultCode": 1032,
                    "ResultDesc": "Request cancelled by user"
                }
            }
        }
    """
    try:
        # Navigate to stkCallback
        body = payload.get("Body", {})
        stk_callback = body.get("stkCallback", {})

        if not stk_callback:
            logger.warning("No stkCallback found in payload")
            return None

        # Extract required fields
        merchant_request_id = stk_callback.get("MerchantRequestID")
        checkout_request_id = stk_callback.get("CheckoutRequestID")
        result_code = stk_callback.get("ResultCode")
        result_desc = stk_callback.get("ResultDesc")

        if not checkout_request_id:
            logger.warning("No CheckoutRequestID in callback")
            return None

        if result_code is None:
            logger.warning("No ResultCode in callback")
            return None

        parsed = {
            "merchant_request_id": merchant_request_id,
            "checkout_request_id": checkout_request_id,
            "result_code": result_code,
            "result_desc": result_desc,
        }

        # If successful (ResultCode == 0), extract CallbackMetadata
        if result_code == 0:
            callback_metadata = stk_callback.get("CallbackMetadata", {})
            items = callback_metadata.get("Item", [])

            # Parse Item array into dict
            metadata_dict = {}
            for item in items:
                name = item.get("Name")
                value = item.get("Value")
                if name:
                    metadata_dict[name] = value

            parsed["amount"] = metadata_dict.get("Amount")
            parsed["mpesa_receipt"] = metadata_dict.get("MpesaReceiptNumber")
            parsed["transaction_date"] = metadata_dict.get("TransactionDate")
            parsed["phone_number"] = metadata_dict.get("PhoneNumber")

            logger.info(
                "Parsed successful callback",
                extra={
                    "checkout_request_id": checkout_request_id,
                    "mpesa_receipt": parsed["mpesa_receipt"],
                },
            )
        else:
            logger.info(
                "Parsed failed callback",
                extra={
                    "checkout_request_id": checkout_request_id,
                    "result_code": result_code,
                    "result_desc": result_desc,
                },
            )

        return parsed

    except (KeyError, TypeError, AttributeError) as e:
        logger.error(
            "Failed to parse callback payload",
            extra={"error": str(e), "payload_keys": list(payload.keys())},
            exc_info=True,
        )
        return None


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


@router.post("/stk/callback", status_code=200)
async def handle_stk_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, str]:
    """
    Handle M-PESA STK Push callback.

    Receives payment result from M-PESA after customer completes or cancels payment.
    Updates payment and invoice status, sends receipts to customer and merchant.

    IMPORTANT: Must return 200 OK within 30 seconds to prevent M-PESA retries.

    Args:
        request: FastAPI request object containing callback payload
        db: Database session

    Returns:
        Success response dict: {"ResultCode": "0", "ResultDesc": "Accepted"}

    Callback Payload Structure:
        Success (ResultCode == 0):
            {
                "Body": {
                    "stkCallback": {
                        "MerchantRequestID": "...",
                        "CheckoutRequestID": "...",
                        "ResultCode": 0,
                        "ResultDesc": "Success",
                        "CallbackMetadata": {
                            "Item": [
                                {"Name": "Amount", "Value": 1.00},
                                {"Name": "MpesaReceiptNumber", "Value": "NLJ7RT61SV"},
                                {"Name": "TransactionDate", "Value": 20191219102115},
                                {"Name": "PhoneNumber", "Value": 254708374149}
                            ]
                        }
                    }
                }
            }

        Failure (ResultCode != 0):
            {
                "Body": {
                    "stkCallback": {
                        "MerchantRequestID": "...",
                        "CheckoutRequestID": "...",
                        "ResultCode": 1032,
                        "ResultDesc": "Request cancelled by user"
                    }
                }
            }
    """
    # Always return 200 OK to M-PESA, regardless of processing outcome
    success_response = {"ResultCode": "0", "ResultDesc": "Accepted"}

    try:
        # Parse request body
        payload = await request.json()

        logger.info(
            "Received STK Push callback",
            extra={"payload_keys": list(payload.keys())},
        )

        # Parse callback payload
        parsed = parse_callback_payload(payload)

        if not parsed:
            logger.error("Failed to parse callback payload")
            return success_response

        checkout_request_id = parsed["checkout_request_id"]
        result_code = parsed["result_code"]

        # Check for duplicate callback (idempotency)
        existing_processed = await check_callback_processed(checkout_request_id, db)
        if existing_processed:
            logger.info(
                "Duplicate callback detected - already processed",
                extra={
                    "checkout_request_id": checkout_request_id,
                    "payment_status": existing_processed.status,
                },
            )
            return success_response

        # Find payment record by CheckoutRequestID
        payment_stmt = select(Payment).where(
            Payment.checkout_request_id == checkout_request_id
        )
        payment_result = await db.execute(payment_stmt)
        payment = payment_result.scalar_one_or_none()

        if not payment:
            logger.warning(
                "Payment not found for CheckoutRequestID",
                extra={"checkout_request_id": checkout_request_id},
            )
            return success_response

        # Load related invoice
        invoice_stmt = select(Invoice).where(Invoice.id == payment.invoice_id)
        invoice_result = await db.execute(invoice_stmt)
        invoice = invoice_result.scalar_one_or_none()

        if not invoice:
            logger.error(
                "Invoice not found for payment",
                extra={"payment_id": payment.id, "invoice_id": payment.invoice_id},
            )
            return success_response

        # Store callback payload
        payment.raw_callback = payload

        # Update payment and invoice based on result
        if result_code == 0:
            # Payment successful
            payment.status = "SUCCESS"
            payment.mpesa_receipt = parsed.get("mpesa_receipt")

            invoice.status = "PAID"
            invoice.pay_ref = parsed.get("mpesa_receipt")

            logger.info(
                "Payment successful",
                extra={
                    "payment_id": payment.id,
                    "invoice_id": invoice.id,
                    "mpesa_receipt": payment.mpesa_receipt,
                },
            )

            # Commit changes before sending messages
            await db.commit()
            await db.refresh(payment)
            await db.refresh(invoice)

            # Send receipts to customer and merchant
            whatsapp_service = WhatsAppService()

            # Convert amount from cents to KES
            amount_kes = invoice.amount_cents / 100

            try:
                # Send receipt to customer
                await whatsapp_service.send_receipt_to_customer(
                    customer_msisdn=invoice.msisdn,
                    invoice_id=invoice.id,
                    amount_kes=amount_kes,
                    mpesa_receipt=payment.mpesa_receipt or "N/A",
                    db_session=db,
                )

                # Send receipt to merchant
                await whatsapp_service.send_receipt_to_merchant(
                    merchant_msisdn=invoice.merchant_msisdn,
                    invoice_id=invoice.id,
                    customer_msisdn=invoice.msisdn,
                    amount_kes=amount_kes,
                    mpesa_receipt=payment.mpesa_receipt or "N/A",
                    db_session=db,
                )

                logger.info(
                    "Receipts sent successfully",
                    extra={"invoice_id": invoice.id, "payment_id": payment.id},
                )

            except Exception as e:
                logger.error(
                    "Failed to send receipts",
                    extra={
                        "error": str(e),
                        "invoice_id": invoice.id,
                        "payment_id": payment.id,
                    },
                    exc_info=True,
                )
                # Don't fail callback due to notification errors

        else:
            # Payment failed
            payment.status = "FAILED"
            invoice.status = "FAILED"

            logger.info(
                "Payment failed",
                extra={
                    "payment_id": payment.id,
                    "invoice_id": invoice.id,
                    "result_code": result_code,
                    "result_desc": parsed.get("result_desc"),
                },
            )

            await db.commit()

            # Notify merchant and customer of payment failure (Task 4.4)
            try:
                whatsapp_service = WhatsAppService()

                # Get readable failure reason
                failure_reasons = {
                    1: "Insufficient balance",
                    1032: "Cancelled by user",
                    1037: "Timeout - user did not respond",
                    2001: "Invalid phone number",
                }
                failure_reason = failure_reasons.get(
                    result_code,
                    f"Payment failed (code {result_code})"
                )

                # Notify merchant
                merchant_message = (
                    f"Payment failed for invoice {invoice.id}\n"
                    f"Customer: {invoice.msisdn}\n"
                    f"Reason: {failure_reason}"
                )
                await whatsapp_service.send_message(
                    invoice.merchant_msisdn,
                    merchant_message
                )

                # Notify customer
                customer_message = (
                    f"Payment for invoice {invoice.id} was not completed.\n"
                    f"Reason: {failure_reason}\n"
                    f"You can try again by clicking the Pay button in the invoice message."
                )
                await whatsapp_service.send_message(
                    invoice.msisdn,
                    customer_message
                )

                logger.info(
                    "Payment failure notifications sent",
                    extra={"invoice_id": invoice.id},
                )

            except Exception as notify_error:
                logger.error(
                    "Failed to send payment failure notifications",
                    extra={
                        "error": str(notify_error),
                        "invoice_id": invoice.id,
                    },
                    exc_info=True,
                )

        return success_response

    except Exception as e:
        logger.error(
            "Error processing STK callback",
            extra={"error": str(e)},
            exc_info=True,
        )
        # Still return 200 OK to prevent retries
        return success_response