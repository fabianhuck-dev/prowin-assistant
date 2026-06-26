"""Read-only Audit-API. Es gibt bewusst KEINE Schreib-/Lösch-Endpunkte (Regel 4)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import AuditLog

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    mandant_id: uuid.UUID | None
    entity_type: str
    entity_id: uuid.UUID | None
    action: str
    actor: str
    payload: dict | None
    created_at: datetime


@router.get("", response_model=list[AuditOut])
async def audit_liste(
    mandant_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    limit: int = Query(default=100, le=1000),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(AuditLog)
    if mandant_id is not None:
        stmt = stmt.where(AuditLog.mandant_id == mandant_id)
    if entity_type is not None:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    stmt = stmt.order_by(AuditLog.id.desc()).limit(limit)
    rows = (await session.scalars(stmt)).all()
    return [AuditOut.model_validate(r) for r in rows]
