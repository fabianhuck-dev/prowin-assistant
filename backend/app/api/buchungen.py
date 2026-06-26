from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import Buchung
from app.schemas.buchung import (
    BuchungConfirmRequest,
    BuchungOut,
    BuchungPatchRequest,
)
from app.services.confirmation import bestaetige_und_buche, storniere_und_neu

router = APIRouter(prefix="/buchungen", tags=["buchungen"])


@router.post("", response_model=BuchungOut, status_code=201)
async def buchung_anlegen(
    payload: BuchungConfirmRequest, session: AsyncSession = Depends(get_session)
):
    """Erzeugt eine Buchung — NUR nach expliziter Bestätigung (confirmation.py)."""
    korrekturen = payload.korrekturen.model_dump(exclude_none=True) if payload.korrekturen else None
    try:
        buchung = await bestaetige_und_buche(
            session,
            beleg_id=payload.beleg_id,
            mandant_id=payload.mandant_id,
            bestaetigt_via=payload.bestaetigt_via,
            korrekturen=korrekturen,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return BuchungOut.model_validate(buchung)


@router.patch("/{buchung_id}")
async def buchung_korrigieren(
    buchung_id: uuid.UUID,
    payload: BuchungPatchRequest,
    session: AsyncSession = Depends(get_session),
):
    """Korrektur ohne Overwrite: alte Buchung storniert=True, neue Buchung angelegt."""
    try:
        alt, neu = await storniere_und_neu(
            session,
            buchung_id=buchung_id,
            mandant_id=payload.mandant_id,
            korrekturen=payload.korrekturen.model_dump(exclude_none=True),
            bestaetigt_via=payload.bestaetigt_via,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return {
        "storniert": BuchungOut.model_validate(alt),
        "neu": BuchungOut.model_validate(neu),
    }


@router.get("", response_model=list[BuchungOut])
async def buchungen_liste(
    mandant_id: uuid.UUID,
    include_storniert: bool = False,
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Buchung).where(Buchung.mandant_id == mandant_id)
    if not include_storniert:
        stmt = stmt.where(Buchung.storniert.is_(False))
    stmt = stmt.order_by(Buchung.datum)
    rows = (await session.scalars(stmt)).all()
    return [BuchungOut.model_validate(b) for b in rows]
