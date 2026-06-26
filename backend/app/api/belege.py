from __future__ import annotations

import base64
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import Beleg, Rueckfrage
from app.providers import get_llm_provider, get_ocr_provider
from app.schemas.beleg import (
    BelegIngestRequest,
    BelegIngestResponse,
    BelegOut,
    BelegUrlResponse,
)
from app.schemas.llm import VorschlagResponse
from app.services.classification import ingest_beleg, klassifiziere_beleg, run_ocr
from app.services.immutability import get_signed_url, get_storage

router = APIRouter(prefix="/belege", tags=["belege"])


@router.post("/ingest", response_model=BelegIngestResponse)
async def ingest(payload: BelegIngestRequest, session: AsyncSession = Depends(get_session)):
    try:
        data = base64.b64decode(payload.content_base64)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Ungültiges base64: {exc}") from exc

    beleg, is_duplicate = await ingest_beleg(
        session,
        mandant_id=payload.mandant_id,
        data=data,
        filename=payload.filename,
        mime_type=payload.mime_type,
        quelle=payload.quelle,
    )
    await session.commit()
    return BelegIngestResponse(
        beleg_id=beleg.id,
        storage_key=beleg.storage_key,
        sha256_hash=beleg.sha256_hash,
        status="dupliziert" if is_duplicate else beleg.status,
        is_duplicate=is_duplicate,
    )


@router.post("/{beleg_id}/klassifiziere", response_model=VorschlagResponse)
async def klassifiziere(beleg_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """Führt OCR + LLM-Klassifikation aus. Erzeugt NUR einen Vorschlag, keine Buchung."""
    beleg = await session.get(Beleg, beleg_id)
    if beleg is None:
        raise HTTPException(status_code=404, detail="Beleg nicht gefunden")

    data = await get_storage().get(beleg.storage_key)
    ocr_result = await run_ocr(session, beleg, data, get_ocr_provider())
    if ocr_result is None:
        await session.commit()
        raise HTTPException(status_code=422, detail="OCR fehlgeschlagen (ocr_status=failed)")

    vorschlag = await klassifiziere_beleg(session, beleg.id, ocr_result, get_llm_provider())

    rf = await session.scalar(
        select(Rueckfrage)
        .where(Rueckfrage.beleg_id == beleg.id, Rueckfrage.status == "offen")
        .order_by(Rueckfrage.created_at.desc())
    )
    await session.commit()
    return VorschlagResponse(
        beleg_id=beleg.id,
        vorschlag=vorschlag,
        rueckfrage_id=rf.id if rf else None,
        plausi_warnung=beleg.plausi_warnung,
    )


@router.get("/{beleg_id}", response_model=BelegOut)
async def get_beleg(beleg_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    beleg = await session.get(Beleg, beleg_id)
    if beleg is None:
        raise HTTPException(status_code=404, detail="Beleg nicht gefunden")
    return BelegOut.model_validate(beleg)


@router.get("/{beleg_id}/url", response_model=BelegUrlResponse)
async def get_beleg_url(beleg_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    beleg = await session.get(Beleg, beleg_id)
    if beleg is None:
        raise HTTPException(status_code=404, detail="Beleg nicht gefunden")
    url = await get_signed_url(beleg.storage_key)
    return BelegUrlResponse(beleg_id=beleg.id, signed_url=url)
