"""Beleg-Flow-Tests T1–T7 und T10 (Confirm-Gate).

Zentrale Compliance-Invariante: Ohne expliziten Bestätigungsaufruf entsteht
NIEMALS eine Buchung — egal wie hoch die Confidence ist (T10).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.db.models import Beleg, Buchung, Rueckfrage
from app.providers.llm.stub import StubLlmProvider
from app.providers.ocr.stub import StubOcrProvider
from app.services.classification import ingest_beleg, klassifiziere_beleg, run_ocr
from app.services.confirmation import bestaetige_und_buche


async def _ingest_and_classify(session, mandant, kind: str):
    data = f"STUB-IMAGE:{kind}".encode()
    beleg, dup = await ingest_beleg(
        session,
        mandant_id=mandant.id,
        data=data,
        filename=f"{kind}.jpg",
        mime_type="image/jpeg",
        quelle="whatsapp",
    )
    ocr = await run_ocr(session, beleg, data, StubOcrProvider())
    vorschlag = None
    if ocr is not None:
        vorschlag = await klassifiziere_beleg(session, beleg.id, ocr, StubLlmProvider())
    await session.commit()
    return beleg, ocr, vorschlag, dup


async def _count_buchungen(session, mandant_id) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(Buchung).where(Buchung.mandant_id == mandant_id)
        )
    ).scalar_one()


async def test_t1_tankquittung_ausgabe_fahrtkosten(session, mandant, seed_kategorien):
    beleg, ocr, vorschlag, _ = await _ingest_and_classify(session, mandant, "tankquittung")
    assert vorschlag.belegtyp == "ausgabe"
    assert vorschlag.kategorie_vorschlag == "Fahrtkosten"
    assert vorschlag.confidence >= 0.85
    # T10/T1: KEINE Buchung allein durch Klassifikation.
    assert await _count_buchungen(session, mandant.id) == 0
    assert beleg.status == "klassifiziert"


async def test_t2_wareneinkauf_kategorie(session, mandant, seed_kategorien):
    _, _, vorschlag, _ = await _ingest_and_classify(session, mandant, "wareneinkauf")
    assert vorschlag.belegtyp == "ausgabe"
    assert vorschlag.kategorie_vorschlag == "Wareneinkauf ProWin"
    assert await _count_buchungen(session, mandant.id) == 0


async def test_t3_provision_einnahme(session, mandant, seed_kategorien):
    _, _, vorschlag, _ = await _ingest_and_classify(session, mandant, "provision")
    assert vorschlag.belegtyp == "provision"
    assert vorschlag.kategorie_vorschlag == "Provision ProWin"
    # Provision ist eine Einnahme -> nach Bestätigung typ=einnahme (hier nur Vorschlag).
    assert await _count_buchungen(session, mandant.id) == 0


async def test_t4_low_confidence_creates_rueckfrage(session, mandant):
    beleg, _, vorschlag, _ = await _ingest_and_classify(session, mandant, "unbekannt")
    assert vorschlag.confidence < 0.6
    assert vorschlag.kategorie_vorschlag is None
    assert vorschlag.rueckfrage_text is not None
    rf = await session.scalar(select(Rueckfrage).where(Rueckfrage.beleg_id == beleg.id))
    assert rf is not None
    assert rf.status == "offen"
    assert await _count_buchungen(session, mandant.id) == 0


async def test_t5_ocr_failed_no_buchung(session, mandant):
    data = b"STUB-IMAGE:kaputt"
    beleg, _ = await ingest_beleg(
        session,
        mandant_id=mandant.id,
        data=data,
        filename="kaputt.jpg",
        mime_type="image/jpeg",
        quelle="whatsapp",
    )
    ocr = await run_ocr(session, beleg, data, StubOcrProvider())
    await session.commit()
    assert ocr is None
    assert beleg.ocr_status == "failed"
    assert await _count_buchungen(session, mandant.id) == 0


async def test_t6_duplicate_no_second_beleg(session, mandant):
    data = b"STUB-IMAGE:tankquittung"
    beleg1, dup1 = await ingest_beleg(
        session, mandant_id=mandant.id, data=data, filename="a.jpg", mime_type="image/jpeg"
    )
    await session.commit()
    beleg2, dup2 = await ingest_beleg(
        session, mandant_id=mandant.id, data=data, filename="a.jpg", mime_type="image/jpeg"
    )
    await session.commit()
    assert dup1 is False
    assert dup2 is True
    assert beleg1.id == beleg2.id
    total = (
        await session.execute(select(func.count()).select_from(Beleg))
    ).scalar_one()
    assert total == 1


async def test_t7_future_date_plausi_warning(session, mandant):
    beleg, _, vorschlag, _ = await _ingest_and_classify(session, mandant, "zukunft")
    assert beleg.plausi_warnung is not None
    assert "Zukunft" in beleg.plausi_warnung
    # Plausi-Warnung stammt aus dem Code, nicht aus dem LLM.
    future = (date.today() + timedelta(days=30)).isoformat()
    assert vorschlag.datum == future


async def test_t10_confirm_gate_no_buchung_without_confirm(session, mandant, seed_kategorien):
    """T10: Hohe Confidence, vollständige Daten — trotzdem KEINE Buchung ohne confirm."""
    beleg, _, vorschlag, _ = await _ingest_and_classify(session, mandant, "tankquittung")
    assert vorschlag.confidence >= 0.85
    assert await _count_buchungen(session, mandant.id) == 0

    # Erst der explizite Bestätigungsaufruf erzeugt genau eine Buchung.
    buchung = await bestaetige_und_buche(
        session,
        beleg_id=beleg.id,
        mandant_id=mandant.id,
        bestaetigt_via="dashboard",
        korrekturen=None,
    )
    await session.commit()
    assert buchung.id is not None
    assert buchung.typ == "ausgabe"
    assert await _count_buchungen(session, mandant.id) == 1

    # Erneute Bestätigung desselben Belegs ist nicht möglich (kein Doppel-Buchen).
    with pytest.raises(ValueError):
        await bestaetige_und_buche(
            session,
            beleg_id=beleg.id,
            mandant_id=mandant.id,
            bestaetigt_via="dashboard",
            korrekturen=None,
        )
