"""
Database setup and session management for InvoiceIQ.

This module provides async database engine, session factory, and dependency
injection for FastAPI routes. Supports both SQLite (development) and
PostgreSQL/Supabase (production) via DATABASE_URL configuration.
"""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings


# Create async engine with database URL from settings
# SQLite: sqlite+aiosqlite:///./data.db
# PostgreSQL: postgresql+asyncpg://user:pass@host/db
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,  # Log SQL statements in debug mode
    # SQLite-specific settings (only applied if using SQLite)
    connect_args=(
        {"check_same_thread": False}
        if settings.database_url.startswith("sqlite")
        else {}
    ),
)

# Create async session factory
# expire_on_commit=False prevents attributes from being expired after commit
# This is important for async operations where we might access attributes
# after the session has been committed
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """
    Base class for all SQLAlchemy ORM models.

    All models should inherit from this class to get SQLAlchemy functionality.
    Uses DeclarativeBase for SQLAlchemy 2.0+ style declarative mapping.
    """

    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency for database sessions.

    Provides an async database session to route handlers. The session is
    automatically closed after the request is completed.

    Yields:
        AsyncSession: An async SQLAlchemy session.

    Example:
        ```python
        @app.get("/invoices")
        async def list_invoices(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(Invoice))
            return result.scalars().all()
        ```
    """
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def create_tables() -> None:
    """
    Create all database tables defined in models.

    This function uses Base.metadata to create all tables. Should be called
    on application startup for development. In production, use Alembic migrations.

    Example:
        ```python
        @app.on_event("startup")
        async def startup():
            await create_tables()
        ```
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)