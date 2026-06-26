from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.schemas.euer import EuerVorschau
from app.services.euer import berechne_euer

router = APIRouter(prefix="/euer", tags=["euer"])


@router.get("", response_model=EuerVorschau)
async def euer_vorschau(
    mandant_id: uuid.UUID,
    jahr: int = date.today().year,
    session: AsyncSession = Depends(get_session),
):
    return await berechne_euer(session, mandant_id, jahr)
