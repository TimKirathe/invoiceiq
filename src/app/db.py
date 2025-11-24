"""
Supabase database client setup for InvoiceIQ.

This module provides the Supabase client instance and dependency injection
for FastAPI routes. All database operations use the Supabase Python client
with the service_role key for privileged backend access.
"""

from typing import AsyncGenerator

from supabase import Client, create_client

from .config import settings


# Create Supabase client with service_role key for backend operations
# This client has full access to the database, bypassing RLS policies
supabase_client: Client = create_client(
    supabase_url=settings.supabase_url,
    supabase_key=settings.supabase_secret_key,
)


def get_supabase() -> Client:
    """
    FastAPI dependency for Supabase client.

    Provides a Supabase client instance to route handlers. The client is
    configured with service_role key for privileged backend operations.

    Returns:
        Client: A Supabase client instance.

    Example:
        ```python
        @app.get("/invoices")
        async def list_invoices(supabase: Client = Depends(get_supabase)):
            response = supabase.table("invoices").select("*").execute()
            return response.data
        ```
    """
    return supabase_client