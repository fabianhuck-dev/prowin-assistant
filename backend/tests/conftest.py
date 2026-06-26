"""Pytest-Fixtures.

Die Tests laufen vollständig OHNE Docker:
- Datenbank: Async-SQLite (In-Memory, StaticPool).
- Object Storage: In-Memory-Backend (write-once-Semantik erhalten).
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

# Repo-Root auf den Pfad legen, damit `scripts.seed` importierbar ist.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest
import pytest_asyncio
from app.db import models
from app.db.base import Base
from app.services import immutability
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Append-only-Invariante für audit_log (entspricht den PG-Triggern der Migration).
        await conn.exec_driver_sql(
            "CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log "
            "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;"
        )
        await conn.exec_driver_sql(
            "CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log "
            "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;"
        )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture
async def session(session_factory) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as s:
        yield s


@pytest.fixture(autouse=True)
def memory_storage():
    """Setzt für jeden Test ein frisches In-Memory-Storage-Backend."""
    store = immutability.InMemoryStorage()
    immutability.set_storage(store)
    yield store
    immutability.set_storage(None)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def mandant(session) -> models.Mandant:
    m = models.Mandant(
        id=uuid.uuid4(),
        name="Max Muster",
        whatsapp_phone="+4915100000000",
        is_kleinunternehmer=True,
    )
    session.add(m)
    await session.commit()
    await session.refresh(m)
    return m


@pytest_asyncio.fixture
async def seed_kategorien(session) -> dict[str, models.Kategorie]:
    """Legt die System-Default-Kategorien an und gibt sie nach Name zurück."""
    from scripts.seed import DEFAULT_KATEGORIEN

    out: dict[str, models.Kategorie] = {}
    for name, typ in DEFAULT_KATEGORIEN:
        k = models.Kategorie(name=name, typ=typ, is_system_default=True)
        session.add(k)
        out[name] = k
    await session.commit()
    for k in out.values():
        await session.refresh(k)
    return out


@pytest_asyncio.fixture
async def client(session_factory) -> AsyncGenerator[AsyncClient, None]:
    """httpx-Client gegen die FastAPI-App mit überschriebener DB-Session."""
    from app.db.base import get_session
    from app.main import app

    async def _override_get_session() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
