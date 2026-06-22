"""Database setup — SQLAlchemy async engine and session."""

from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)
from .models import Base

# 全局 session factory（在 ForwarderServer 启动时初始化）
_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_engine(mysql_url: str):
    """Create an async SQLAlchemy engine."""
    return create_async_engine(mysql_url, echo=False, pool_size=5, max_overflow=10)


def create_session_factory(engine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    global _session_factory
    _session_factory = factory
    return factory


async def init_db(engine) -> None:
    """Create all tables (for development; use Alembic for production)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield an async DB session."""
    if _session_factory is None:
        raise RuntimeError("Database session factory not initialized")
    async with _session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
