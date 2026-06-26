"""Export-Tests T8 (DATEV/PDF + Audit) und T9 (PATCH = Storno + Neuanlage, kein Overwrite)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from app.db.models import AuditLog, Buchung
from app.providers.llm.stub import StubLlmProvider
from app.providers.ocr.stub import StubOcrProvider
from app.services.classification import ingest_beleg, klassifiziere_beleg, run_ocr
from app.services.confirmation import bestaetige_und_buche, storniere_und_neu
from app.services.export import erstelle_datev_csv, erstelle_export, erstelle_pdf_bundle


async def _bestaetigte_buchung(session, mandant, kind: str):
    data = f"STUB-IMAGE:{kind}".encode()
    beleg, _ = await ingest_beleg(
        session, mandant_id=mandant.id, data=data, filename=f"{kind}.jpg", mime_type="image/jpeg"
    )
    ocr = await run_ocr(session, beleg, data, StubOcrProvider())
    await klassifiziere_beleg(session, beleg.id, ocr, StubLlmProvider())
    buchung = await bestaetige_und_buche(
        session, beleg_id=beleg.id, mandant_id=mandant.id, bestaetigt_via="dashboard"
    )
    await session.commit()
    return buchung


async def test_t8_datev_csv_pdf_und_audit(session, mandant, seed_kategorien):
    await _bestaetigte_buchung(session, mandant, "tankquittung")
    await _bestaetigte_buchung(session, mandant, "wareneinkauf")
    await _bestaetigte_buchung(session, mandant, "provision")

    von, bis = date(date.today().year, 1, 1), date(date.today().year, 12, 31)

    csv_bytes = await erstelle_datev_csv(session, mandant.id, von, bis)
    assert csv_bytes
    text = csv_bytes.decode("cp1252")
    assert "Soll/Haben-Kennzeichen" in text
    # 1 Headerzeile + 3 Buchungszeilen
    assert len([ln for ln in text.splitlines() if ln.strip()]) == 4

    pdf_bytes = await erstelle_pdf_bundle(session, mandant.id, von, bis)
    assert pdf_bytes.startswith(b"%PDF")

    # Vollständiger Export-Pfad mit Persistenz + Audit.
    export = await erstelle_export(session, mandant.id, von, bis, "datev_csv")
    await session.commit()
    assert export.anzahl_buchungen == 3
    assert len(export.sha256_hash) == 64

    audit_count = (
        await session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.entity_type == "export", AuditLog.action == "export.created")
        )
    ).scalar_one()
    assert audit_count == 1


async def test_t9_patch_storno_und_neuanlage(session, mandant, seed_kategorien):
    """T9: PATCH = kein Overwrite. Alte Zeile storniert=True, neue Zeile, ZWEI Audit-Einträge."""
    buchung = await _bestaetigte_buchung(session, mandant, "tankquittung")
    alt_id = buchung.id
    alt_betrag = float(buchung.betrag)

    alt, neu = await storniere_und_neu(
        session,
        buchung_id=alt_id,
        mandant_id=mandant.id,
        korrekturen={"betrag": 99.99},
        bestaetigt_via="dashboard",
    )
    await session.commit()

    # Alte Zeile existiert weiterhin, ist storniert (kein Overwrite).
    refreshed_alt = await session.get(Buchung, alt_id)
    assert refreshed_alt.storniert is True
    assert float(refreshed_alt.betrag) == alt_betrag  # Geschäftsdaten unverändert

    # Neue Zeile separat, mit Korrekturwert und Verweis auf das Original.
    assert neu.id != alt_id
    assert neu.storniert is False
    assert float(neu.betrag) == 99.99
    assert neu.storno_von_id == alt_id

    # Genau zwei Zeilen insgesamt (alt + neu).
    total = (
        await session.execute(
            select(func.count()).select_from(Buchung).where(Buchung.mandant_id == mandant.id)
        )
    ).scalar_one()
    assert total == 2

    # ZWEI Audit-Einträge: Storno + Neuanlage.
    storno_audits = (
        await session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action.in_(["buchung.storniert", "buchung.neu_aus_korrektur"]))
        )
    ).scalar_one()
    assert storno_audits == 2
