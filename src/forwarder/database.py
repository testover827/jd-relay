"""Database setup — SQLAlchemy async engine and session."""

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from .models import Base


def create_engine(mysql_url: str):
    """Create an async SQLAlchemy engine."""
    return create_async_engine(mysql_url, echo=False, pool_size=5, max_overflow=10)


def create_session_factory(engine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db(engine) -> None:
    """Create all tables (for development; use Alembic for production)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
