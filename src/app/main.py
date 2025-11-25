"""
Main FastAPI application module for InvoiceIQ MVP.

This module initializes the FastAPI application, configures middleware,
sets up health check endpoints, and registers API routers.
"""

import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .config import settings
from .db import get_supabase
from .routers import invoice_view, invoices, payments, sms, whatsapp
from .services.metrics import (
    get_average_payment_time,
    get_conversion_rate,
    get_invoice_stats,
)
from .utils.logging import get_logger, setup_logging

# Set up structured logging
setup_logging(level="DEBUG" if settings.debug else "INFO")
logger = get_logger(__name__)

# Initialize rate limiter
# Uses client IP address as the key for rate limiting
limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan context manager.

    Handles startup and shutdown events for the application.
    With Supabase, no database initialization is needed on startup.

    Args:
        app: The FastAPI application instance

    Yields:
        None
    """
    # Startup
    logger.info("Application starting up")
    logger.info("Using Supabase for database operations")

    yield

    # Shutdown
    logger.info("Application shutting down")


# Initialize FastAPI application
app = FastAPI(
    title="InvoiceIQ MVP",
    description="WhatsApp-first invoicing system with M-PESA payment integration",
    version="1.0.0",
    lifespan=lifespan,
)

# Attach rate limiter to app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Configure CORS middleware (allow all origins for MVP)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for MVP - restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_correlation_id(request: Request, call_next) -> Response:
    """
    Correlation ID middleware.

    Generates a unique correlation ID for each request to enable request tracing
    across logs. The correlation ID is stored in request state and added to
    response headers.

    Args:
        request: The incoming HTTP request
        call_next: The next middleware or route handler

    Returns:
        The HTTP response with X-Correlation-ID header
    """
    # Generate or extract correlation ID
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    request.state.correlation_id = correlation_id

    # Process request
    response = await call_next(request)

    # Add correlation ID to response headers for client tracking
    response.headers["X-Correlation-ID"] = correlation_id

    return response


@app.middleware("http")
async def log_requests(request: Request, call_next) -> Response:
    """
    Request logging middleware.

    Logs all incoming HTTP requests with method, path, and processing time.
    Includes correlation ID for request tracing.

    Args:
        request: The incoming HTTP request
        call_next: The next middleware or route handler

    Returns:
        The HTTP response
    """
    start_time = time.time()
    correlation_id = getattr(request.state, "correlation_id", None)

    # Log incoming request
    logger.info(
        f"Incoming request: {request.method} {request.url.path}",
        extra={
            "method": request.method,
            "path": request.url.path,
            "query_params": str(request.query_params),
            "correlation_id": correlation_id,
        },
    )

    # Process request
    response = await call_next(request)

    # Calculate processing time
    process_time = time.time() - start_time

    # Log response
    logger.info(
        f"Request completed: {request.method} {request.url.path}",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "process_time": f"{process_time:.4f}s",
            "correlation_id": correlation_id,
        },
    )

    # Add processing time header
    response.headers["X-Process-Time"] = str(process_time)

    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Global exception handler for unhandled exceptions.

    Catches all unhandled exceptions, logs them with full stack trace,
    and returns a 500 error with a unique error ID for tracking.
    Does not expose technical details to users.

    Args:
        request: The incoming HTTP request
        exc: The unhandled exception

    Returns:
        JSON response with error ID and generic message
    """
    # Generate unique error ID for tracking
    error_id = str(uuid.uuid4())
    correlation_id = getattr(request.state, "correlation_id", None)

    # Log the exception with full stack trace
    logger.error(
        "Unhandled exception occurred",
        extra={
            "error_id": error_id,
            "correlation_id": correlation_id,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "path": request.url.path,
            "method": request.method,
        },
        exc_info=True,
    )

    # Return generic error response without exposing technical details
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "error_id": error_id,
            "message": "An unexpected error occurred. Please contact support with the error ID if the issue persists.",
        },
        headers={"X-Error-ID": error_id},
    )


# Health check endpoints
@app.get("/healthz", tags=["health"])
async def health_check() -> dict[str, str]:
    """
    Basic health check endpoint.

    Returns a simple status response to indicate the service is running.

    Returns:
        Dictionary with status: ok
    """
    return {"status": "ok"}


@app.get("/readyz", tags=["health"])
async def readiness_check(supabase=Depends(get_supabase)) -> dict[str, str]:
    """
    Readiness check endpoint.

    Verifies that the application is ready to accept requests by checking
    the Supabase connection.

    Returns:
        Dictionary with status and database connection info

    Raises:
        HTTPException: 503 if database is not accessible
    """
    try:
        # Test Supabase connection with a simple query
        _ = supabase.table("invoices").select("id").limit(1).execute()
        logger.info("Readiness check passed - Supabase connected")
        return {"status": "ready", "database": "connected"}
    except Exception as e:
        logger.error(
            "Readiness check failed - Supabase connection error",
            extra={"error": str(e)},
            exc_info=True,
        )
        raise HTTPException(
            status_code=503,
            detail="Database connection unavailable",
        )


# Stats endpoint
@app.get("/stats/summary", tags=["stats"])
async def stats_summary(supabase=Depends(get_supabase)) -> dict:
    """
    Get summary statistics for business metrics.

    Returns aggregate statistics including:
    - Invoice counts by status (total, pending, sent, paid, failed, cancelled)
    - Conversion rate (percentage of sent invoices that were paid)
    - Average payment time in seconds (time from payment initiation to completion)

    Args:
        supabase: Supabase client dependency

    Returns:
        Dictionary with invoice statistics, conversion rate, and average payment time

    Raises:
        HTTPException: 500 if metrics calculation fails
    """
    try:
        # Get all metrics
        invoice_stats = await get_invoice_stats(supabase)
        conversion_rate = await get_conversion_rate(supabase)
        avg_payment_time = await get_average_payment_time(supabase)

        logger.info(
            "Stats summary generated",
            extra={
                "total_invoices": invoice_stats["total"],
                "conversion_rate": f"{conversion_rate:.2f}%",
            },
        )

        return {
            "invoice_stats": invoice_stats,
            "conversion_rate": conversion_rate,
            "average_payment_time_seconds": avg_payment_time,
        }

    except Exception as e:
        logger.error(
            "Failed to generate stats summary",
            extra={"error": str(e)},
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate statistics: {str(e)}",
        )


# Register routers
app.include_router(whatsapp.router, prefix="/whatsapp", tags=["whatsapp"])
app.include_router(sms.router, prefix="/sms", tags=["sms"])
app.include_router(invoices.router, prefix="/invoices", tags=["invoices"])
app.include_router(payments.router, prefix="/payments", tags=["payments"])
# IMPORTANT: invoice_view router MUST be registered LAST to avoid route conflicts
# since it uses catch-all pattern /{invoice_id}
app.include_router(invoice_view.router, tags=["invoice-view"])

logger.info("InvoiceIQ application initialized successfully")