"""
Supabase database client setup for InvoiceIQ.

This module provides the Supabase client instance and dependency injection
for FastAPI routes. All database operations use the Supabase Python client
with the service_role key for privileged backend access.
"""

from typing import Optional

from supabase import Client, create_client

from .config import settings
from .utils.logging import get_logger

# Set up logger
logger = get_logger(__name__)

# Module-level client cache for lazy initialization
_supabase_client: Optional[Client] = None


def get_supabase() -> Client:
    """
    Get or create the Supabase client instance.

    Uses lazy initialization to defer client creation until first use.
    This allows for better debugging and prevents initialization errors
    at module import time.

    The client is created once and cached for subsequent calls.

    FastAPI dependency for Supabase client - provides a Supabase client
    instance to route handlers. The client is configured with service_role
    key for privileged backend operations.

    Returns:
        Client: A Supabase client instance.

    Raises:
        ValueError: If SUPABASE_SECRET_KEY is empty
        Exception: If client initialization fails

    Example:
        ```python
        @app.get("/invoices")
        async def list_invoices(supabase: Client = Depends(get_supabase)):
            response = supabase.table("invoices").select("*").execute()
            return response.data
        ```
    """
    global _supabase_client

    if _supabase_client is None:
        # Add debug logging to diagnose initialization issues
        logger.info("Initializing Supabase client...")
        logger.info(f"SUPABASE_URL: {settings.supabase_url}")
        logger.info(f"SUPABASE_SECRET_KEY length: {len(settings.supabase_secret_key)}")
        logger.info(f"SUPABASE_SECRET_KEY first 10 chars: {settings.supabase_secret_key[:10]}")
        logger.info(f"SUPABASE_SECRET_KEY last 10 chars: {settings.supabase_secret_key[-10:]}")

        # Validate key is not empty
        if not settings.supabase_secret_key:
            logger.error("SUPABASE_SECRET_KEY is empty")
            raise ValueError("SUPABASE_SECRET_KEY is empty")

        try:
            _supabase_client = create_client(
                supabase_url=settings.supabase_url,
                supabase_key=settings.supabase_secret_key,
            )
            logger.info("Supabase client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Supabase client: {e}", exc_info=True)
            raise

    return _supabase_client