"""
Main FastAPI application module for InvoiceIQ MVP.

This module initializes the FastAPI application, configures middleware,
sets up health check endpoints, and registers API routers.
"""

import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .config import settings
from .db import create_tables, engine, get_db
from .routers import invoices, payments, sms, whatsapp
from .utils.logging import get_logger, setup_logging

# Set up structured logging
setup_logging(level="DEBUG" if settings.debug else "INFO")
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan context manager.

    Handles startup and shutdown events for the application.
    Creates database tables on startup.

    Args:
        app: The FastAPI application instance

    Yields:
        None
    """
    # Startup: Create database tables
    logger.info("Application starting up")
    await create_tables()
    logger.info("Database tables created/verified")

    yield

    # Shutdown: Clean up resources
    logger.info("Application shutting down")
    await engine.dispose()
    logger.info("Database connections closed")


# Initialize FastAPI application
app = FastAPI(
    title="InvoiceIQ MVP",
    description="WhatsApp-first invoicing system with M-PESA payment integration",
    version="1.0.0",
    lifespan=lifespan,
)

# Configure CORS middleware (allow all origins for MVP)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for MVP - restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next) -> Response:
    """
    Request logging middleware.

    Logs all incoming HTTP requests with method, path, and processing time.

    Args:
        request: The incoming HTTP request
        call_next: The next middleware or route handler

    Returns:
        The HTTP response
    """
    start_time = time.time()

    # Log incoming request
    logger.info(
        f"Incoming request: {request.method} {request.url.path}",
        extra={
            "method": request.method,
            "path": request.url.path,
            "query_params": str(request.query_params),
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
        },
    )

    # Add processing time header
    response.headers["X-Process-Time"] = str(process_time)

    return response


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
async def readiness_check() -> dict[str, str]:
    """
    Readiness check endpoint.

    Verifies that the application is ready to accept requests by checking
    the database connection.

    Returns:
        Dictionary with status and database connection info

    Raises:
        HTTPException: 503 if database is not accessible
    """
    try:
        # Test database connection
        async for db in get_db():
            await db.execute(text("SELECT 1"))
            logger.info("Readiness check passed - database connected")
            return {"status": "ready", "database": "connected"}
        # This should never be reached, but mypy needs it
        raise HTTPException(
            status_code=503,
            detail="Database connection unavailable",
        )
    except Exception as e:
        logger.error(
            "Readiness check failed - database connection error",
            extra={"error": str(e)},
            exc_info=True,
        )
        raise HTTPException(
            status_code=503,
            detail="Database connection unavailable",
        )


# Register routers
app.include_router(whatsapp.router, prefix="/whatsapp", tags=["whatsapp"])
app.include_router(sms.router, prefix="/sms", tags=["sms"])
app.include_router(invoices.router, prefix="/invoices", tags=["invoices"])
app.include_router(payments.router, prefix="/payments", tags=["payments"])

logger.info("InvoiceIQ application initialized successfully")