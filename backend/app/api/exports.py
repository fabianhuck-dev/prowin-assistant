from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import Export
from app.schemas.export import ExportOut, ExportRequest, ExportWithUrl
from app.services.export import erstelle_export
from app.services.immutability import get_signed_url

router = APIRouter(prefix="/exports", tags=["exports"])


@router.post("", response_model=ExportWithUrl, status_code=201)
async def export_erstellen(payload: ExportRequest, session: AsyncSession = Depends(get_session)):
    try:
        export = await erstelle_export(
            session, payload.mandant_id, payload.von, payload.bis, payload.format
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    url = await get_signed_url(export.storage_key)
    return ExportWithUrl(**ExportOut.model_validate(export).model_dump(), signed_url=url)


@router.get("", response_model=list[ExportOut])
async def exports_liste(mandant_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    rows = (
        await session.scalars(
            select(Export)
            .where(Export.mandant_id == mandant_id)
            .order_by(Export.created_at.desc())
        )
    ).all()
    return [ExportOut.model_validate(e) for e in rows]
