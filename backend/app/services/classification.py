"""Klassifikations-Service.

WICHTIG (Compliance Regel 1):
Keine Funktion hier erzeugt jemals eine Buchung. Es entstehen ausschließlich
Vorschlagsdaten (am Beleg) und ggf. eine Rückfrage. Der Weg zur Buchung führt
einzig über services/confirmation.py nach expliziter menschlicher Bestätigung.

Zahlen- und Datums-Plausibilität wird hier im CODE geprüft (Regel 7), nicht im Prompt.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Beleg, Rueckfrage
from app.providers.llm.base import LlmProvider, LlmVorschlag
from app.providers.ocr.base import OcrProvider, OcrResult
from app.services.audit import append_audit
from app.services.immutability import upload_beleg_write_once


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def pruefe_plausibilitaet(datum: date | None, betrag: float | None) -> str | None:
    """Plausibilitätsprüfung in Code (Regel 7/8). Gibt eine Warnung oder None zurück."""
    warnungen: list[str] = []
    if datum is not None and datum > date.today():
        warnungen.append(f"Belegdatum {datum.isoformat()} liegt in der Zukunft.")
    if betrag is not None and betrag <= 0:
        warnungen.append("Betrag ist nicht positiv.")
    if betrag is not None and betrag > 100_000:
        warnungen.append("Ungewöhnlich hoher Betrag — bitte prüfen.")
    return " ".join(warnungen) or None


async def ingest_beleg(
    session: AsyncSession,
    *,
    mandant_id: uuid.UUID,
    data: bytes,
    filename: str,
    mime_type: str,
    quelle: str = "upload",
) -> tuple[Beleg, bool]:
    """Speichert das Original write-once und legt (falls neu) eine beleg-Zeile an.

    Rückgabe: (beleg, is_duplicate). Bei identischem Hash wird KEINE zweite Zeile
    erzeugt (Regel 2/3 + T6) — der vorhandene Beleg wird zurückgegeben.
    """
    storage_key, sha256 = await upload_beleg_write_once(data, filename)

    existing = await session.scalar(select(Beleg).where(Beleg.sha256_hash == sha256))
    if existing is not None:
        await append_audit(
            session,
            mandant_id=mandant_id,
            entity_type="beleg",
            entity_id=existing.id,
            action="beleg.duplikat_erkannt",
            actor="system",
            payload={"sha256": sha256},
        )
        return existing, True

    beleg = Beleg(
        mandant_id=mandant_id,
        storage_key=storage_key,
        sha256_hash=sha256,
        original_filename=filename,
        mime_type=mime_type,
        quelle=quelle,
        status="eingegangen",
        ocr_status="pending",
    )
    session.add(beleg)
    await session.flush()
    await append_audit(
        session,
        mandant_id=mandant_id,
        entity_type="beleg",
        entity_id=beleg.id,
        action="beleg.ingested",
        actor="system",
        payload={"sha256": sha256, "storage_key": storage_key, "quelle": quelle},
    )
    return beleg, False


async def run_ocr(
    session: AsyncSession, beleg: Beleg, data: bytes, ocr_provider: OcrProvider
) -> OcrResult | None:
    """Führt OCR aus. Bei Fehler: ocr_status=failed, keine weitere Verarbeitung (T5)."""
    try:
        result = await ocr_provider.extract(data, beleg.mime_type or "image/jpeg")
    except Exception as exc:  # noqa: BLE001 - bewusst breit: OCR darf den Flow nicht abbrechen
        beleg.ocr_status = "failed"
        await append_audit(
            session,
            mandant_id=beleg.mandant_id,
            entity_type="beleg",
            entity_id=beleg.id,
            action="beleg.ocr_failed",
            actor="system",
            payload={"error": str(exc)},
        )
        return None

    beleg.ocr_status = "done"
    beleg.ocr_raw = {
        "raw_text": result.raw_text,
        "betrag": result.betrag,
        "datum": result.datum,
        "haendler": result.haendler,
        "confidence": result.confidence,
        "raw_json": result.raw_json,
    }
    beleg.betrag = result.betrag
    beleg.datum = _parse_iso_date(result.datum)
    beleg.haendler = result.haendler
    await session.flush()
    return result


async def klassifiziere_beleg(
    session: AsyncSession,
    beleg_id: uuid.UUID,
    ocr_result: OcrResult,
    llm_provider: LlmProvider,
) -> LlmVorschlag:
    """Erzeugt einen LlmVorschlag und ggf. eine Rückfrage. NIEMALS eine Buchung."""
    beleg = await session.get(Beleg, beleg_id)
    if beleg is None:
        raise ValueError(f"Beleg {beleg_id} nicht gefunden")

    ocr_dict = {
        "betrag": ocr_result.betrag,
        "datum": ocr_result.datum,
        "haendler": ocr_result.haendler,
        "confidence": ocr_result.confidence,
        "raw_text": ocr_result.raw_text,
        "raw_json": ocr_result.raw_json,
    }
    kontext = {
        "is_kleinunternehmer": True,
        "bekannte_haendler": ["ProWin", "ProWin GmbH"],
    }
    vorschlag = await llm_provider.klassifiziere(ocr_dict, kontext)

    beleg.belegtyp = vorschlag.belegtyp
    beleg.llm_vorschlag = vorschlag.model_dump()
    beleg.confidence = vorschlag.confidence
    beleg.status = "klassifiziert"

    # Plausibilität im Code (Regel 7/8), unabhängig vom LLM.
    warnung = pruefe_plausibilitaet(_parse_iso_date(vorschlag.datum), vorschlag.betrag)
    if warnung:
        beleg.plausi_warnung = warnung

    # Rückfrage bei niedriger Confidence / fehlenden Feldern (T4).
    if vorschlag.rueckfrage_text:
        feld = vorschlag.fehlende_felder[0] if vorschlag.fehlende_felder else None
        rf = Rueckfrage(
            mandant_id=beleg.mandant_id,
            beleg_id=beleg.id,
            frage_text=vorschlag.rueckfrage_text,
            feld=feld,
            status="offen",
        )
        session.add(rf)

    await session.flush()
    await append_audit(
        session,
        mandant_id=beleg.mandant_id,
        entity_type="beleg",
        entity_id=beleg.id,
        action="beleg.klassifiziert",
        actor="llm",
        payload={
            "belegtyp": vorschlag.belegtyp,
            "kategorie_vorschlag": vorschlag.kategorie_vorschlag,
            "confidence": vorschlag.confidence,
            "hinweis": "Vorschlag, keine Buchung",
        },
    )
    return vorschlag
