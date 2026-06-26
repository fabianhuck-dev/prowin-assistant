from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    """Deklarative Basis für alle ORM-Modelle."""


def create_engine(url: str | None = None) -> AsyncEngine:
    return create_async_engine(url or settings.database_url, future=True, pool_pre_ping=True)


engine: AsyncEngine = create_engine()
SessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine, expire_on_commit=False, class_=AsyncSession
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI-Dependency: liefert eine Async-Session pro Request."""
    async with SessionFactory() as session:
        yield session
