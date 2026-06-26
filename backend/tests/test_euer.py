"""EÜR-Aggregations-Tests: korrekte Summen, stornierte Buchungen ausgeschlossen."""

from __future__ import annotations

from datetime import date

from app.providers.llm.stub import StubLlmProvider
from app.providers.ocr.stub import StubOcrProvider
from app.services.classification import ingest_beleg, klassifiziere_beleg, run_ocr
from app.services.confirmation import bestaetige_und_buche, storniere_und_neu
from app.services.euer import berechne_euer


async def _buche(session, mandant, kind: str):
    data = f"STUB-IMAGE:{kind}".encode()
    beleg, _ = await ingest_beleg(
        session, mandant_id=mandant.id, data=data, filename=f"{kind}.jpg", mime_type="image/jpeg"
    )
    ocr = await run_ocr(session, beleg, data, StubOcrProvider())
    await klassifiziere_beleg(session, beleg.id, ocr, StubLlmProvider())
    b = await bestaetige_und_buche(
        session, beleg_id=beleg.id, mandant_id=mandant.id, bestaetigt_via="dashboard"
    )
    await session.commit()
    return b


async def test_euer_aggregiert_korrekt(session, mandant, seed_kategorien):
    await _buche(session, mandant, "tankquittung")  # ausgabe 45.50
    await _buche(session, mandant, "wareneinkauf")  # ausgabe 128.00
    await _buche(session, mandant, "provision")  # einnahme 350.00

    euer = await berechne_euer(session, mandant.id, date.today().year)
    assert euer.einnahmen_gesamt == 350.00
    assert euer.ausgaben_gesamt == 173.50
    assert euer.gewinn == 176.50
    assert euer.anzahl_buchungen == 3
    kategorien = {k.kategorie for k in euer.ausgaben_nach_kategorie}
    assert "Fahrtkosten" in kategorien
    assert "Wareneinkauf ProWin" in kategorien


async def test_euer_excludes_stornierte(session, mandant, seed_kategorien):
    b = await _buche(session, mandant, "wareneinkauf")  # ausgabe 128.00
    # Storno + Neuanlage mit anderem Betrag.
    await storniere_und_neu(
        session,
        buchung_id=b.id,
        mandant_id=mandant.id,
        korrekturen={"betrag": 100.00},
        bestaetigt_via="dashboard",
    )
    await session.commit()

    euer = await berechne_euer(session, mandant.id, date.today().year)
    # Nur die neue (nicht stornierte) Buchung zählt.
    assert euer.ausgaben_gesamt == 100.00
    assert euer.anzahl_buchungen == 1
