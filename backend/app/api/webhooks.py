"""WhatsApp-Webhooks.

Trennung der Pfade (Compliance Regel 1):
- POST /webhooks/whatsapp         -> Ingest + OCR + Klassifikation + Bestätigungsfrage.
                                     Erzeugt NIEMALS eine Buchung.
- POST /webhooks/whatsapp/confirm -> EINZIGER Pfad, der (nach Button-Bestätigung)
                                     eine Buchung erzeugt.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import Beleg, Mandant
from app.providers import get_llm_provider, get_ocr_provider, get_whatsapp_provider
from app.providers.whatsapp.base import OutboundMessage
from app.services.classification import ingest_beleg, klassifiziere_beleg, run_ocr
from app.services.confirmation import bestaetige_und_buche

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WhatsAppInbound(BaseModel):
    phone: str
    message_type: str = "image"
    media_url: str | None = None
    text: str | None = None
    message_id: str


class WhatsAppConfirm(BaseModel):
    beleg_id: uuid.UUID
    mandant_id: uuid.UUID
    bestaetigt_via: str = "whatsapp_button"
    korrekturen: dict | None = None


@router.post("/whatsapp")
async def whatsapp_inbound(payload: WhatsAppInbound, session: AsyncSession = Depends(get_session)):
    mandant = await session.scalar(select(Mandant).where(Mandant.whatsapp_phone == payload.phone))
    if mandant is None:
        raise HTTPException(status_code=404, detail="Unbekannte Telefonnummer")

    wa = get_whatsapp_provider()

    if payload.message_type not in ("image", "document") or not payload.media_url:
        await wa.send_message(
            OutboundMessage(to=payload.phone, text="Bitte sende ein Foto deines Belegs.")
        )
        return {"status": "ignored", "reason": "kein Beleg-Medium"}

    data = await wa.download_media(payload.media_url)
    beleg, is_duplicate = await ingest_beleg(
        session,
        mandant_id=mandant.id,
        data=data,
        filename=f"{payload.message_id}.jpg",
        mime_type="image/jpeg",
        quelle="whatsapp",
    )
    if is_duplicate:
        await session.commit()
        await wa.send_message(
            OutboundMessage(
                to=payload.phone,
                text="Diesen Beleg habe ich bereits erhalten. Soll ich ihn trotzdem erneut bearbeiten?",
            )
        )
        return {"status": "duplicate", "beleg_id": str(beleg.id)}

    ocr_result = await run_ocr(session, beleg, data, get_ocr_provider())
    if ocr_result is None:
        await session.commit()
        await wa.send_message(
            OutboundMessage(
                to=payload.phone,
                text="Ich konnte den Beleg leider nicht lesen. Bitte sende ihn erneut oder schärfer.",
            )
        )
        return {"status": "ocr_failed", "beleg_id": str(beleg.id)}

    vorschlag = await klassifiziere_beleg(session, beleg.id, ocr_result, get_llm_provider())
    await session.commit()

    if vorschlag.rueckfrage_text:
        await wa.send_message(OutboundMessage(to=payload.phone, text=vorschlag.rueckfrage_text))
        return {
            "status": "rueckfrage",
            "beleg_id": str(beleg.id),
            "vorschlag": vorschlag.model_dump(),
        }

    # WICHTIG: Hier wird NICHT gebucht. Es wird nur um Bestätigung gebeten.
    text = (
        f"Beleg erkannt: {vorschlag.belegtyp}, {vorschlag.betrag} EUR "
        f"({vorschlag.kategorie_vorschlag}). Buchen?"
    )
    warnung = beleg.plausi_warnung
    if warnung:
        text += f"\n⚠ Hinweis: {warnung}"
    await wa.send_message(
        OutboundMessage(
            to=payload.phone,
            text=text,
            buttons=[
                {"id": f"confirm:{beleg.id}", "title": "Ja, buchen"},
                {"id": f"reject:{beleg.id}", "title": "Nein"},
            ],
        )
    )
    return {
        "status": "vorschlag",
        "beleg_id": str(beleg.id),
        "vorschlag": vorschlag.model_dump(),
        "plausi_warnung": warnung,
    }


@router.post("/whatsapp/confirm")
async def whatsapp_confirm(payload: WhatsAppConfirm, session: AsyncSession = Depends(get_session)):
    """EINZIGER Buchungspfad aus WhatsApp — nur nach Button-Bestätigung."""
    try:
        buchung = await bestaetige_und_buche(
            session,
            beleg_id=payload.beleg_id,
            mandant_id=payload.mandant_id,
            bestaetigt_via=payload.bestaetigt_via,
            korrekturen=payload.korrekturen,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()

    beleg = await session.get(Beleg, payload.beleg_id)
    mandant = await session.get(Mandant, payload.mandant_id)
    if mandant is not None:
        await get_whatsapp_provider().send_message(
            OutboundMessage(
                to=mandant.whatsapp_phone,
                text=f"Gebucht: {buchung.typ} {buchung.betrag} EUR am {buchung.datum.isoformat()}.",
            )
        )
    _ = beleg
    return {"status": "gebucht", "buchung_id": str(buchung.id)}
