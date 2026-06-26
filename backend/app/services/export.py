"""Export-Service: DATEV-CSV und PDF-Bundle.

Exporte werden write-once im Object Storage abgelegt (SHA-256), eine Export-Zeile
und ein Audit-Eintrag werden geschrieben. Es werden ausschließlich bestätigte
(= nicht stornierte) Buchungen exportiert.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Buchung, Export, Kategorie
from app.services.audit import append_audit
from app.services.immutability import compute_sha256, get_storage

# DATEV-übliche Spaltenüberschriften (vereinfachtes Buchungsstapel-Format).
_DATEV_HEADER = [
    "Umsatz (ohne Soll/Haben-Kz)",
    "Soll/Haben-Kennzeichen",
    "WKZ Umsatz",
    "Konto",
    "Gegenkonto (ohne BU-Schluessel)",
    "Belegdatum",
    "Belegfeld 1",
    "Buchungstext",
]


def _de_betrag(value) -> str:
    return f"{float(value):.2f}".replace(".", ",")


async def _buchungen_im_zeitraum(
    session: AsyncSession, mandant_id: uuid.UUID, von: date, bis: date
) -> list[tuple[Buchung, str | None]]:
    stmt = (
        select(Buchung, Kategorie.name)
        .join(Kategorie, Kategorie.id == Buchung.kategorie_id, isouter=True)
        .where(
            Buchung.mandant_id == mandant_id,
            Buchung.storniert.is_(False),
            Buchung.datum >= von,
            Buchung.datum <= bis,
        )
        .order_by(Buchung.datum)
    )
    return [(b, name) for b, name in (await session.execute(stmt)).all()]


async def erstelle_datev_csv(
    session: AsyncSession, mandant_id: uuid.UUID, von: date, bis: date
) -> bytes:
    rows = await _buchungen_im_zeitraum(session, mandant_id, von, bis)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_ALL)
    writer.writerow(_DATEV_HEADER)
    for b, kat_name in rows:
        sh = "H" if b.typ == "einnahme" else "S"
        writer.writerow(
            [
                _de_betrag(b.betrag),
                sh,
                "EUR",
                kat_name or "Sonstiges",
                "1000",  # Gegenkonto-Platzhalter (z. B. Bank/Kasse)
                b.datum.strftime("%d%m"),
                str(b.id)[:8],
                (b.buchungstext or b.haendler or "")[:60],
            ]
        )
    # DATEV erwartet Windows-1252; nicht abbildbare Zeichen werden ersetzt.
    return buf.getvalue().encode("cp1252", errors="replace")


async def erstelle_pdf_bundle(
    session: AsyncSession, mandant_id: uuid.UUID, von: date, bis: date
) -> bytes:
    from fpdf import FPDF

    rows = await _buchungen_im_zeitraum(session, mandant_id, von, bis)

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "ProWin - Buchungsuebersicht", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(
        0,
        7,
        f"Zeitraum: {von.isoformat()} bis {bis.isoformat()}  |  Buchungen: {len(rows)}",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(24, 7, "Datum", border=1)
    pdf.cell(22, 7, "Typ", border=1)
    pdf.cell(26, 7, "Betrag", border=1)
    pdf.cell(45, 7, "Kategorie", border=1)
    pdf.cell(0, 7, "Text", border=1, new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 9)
    summe_ein = 0.0
    summe_aus = 0.0
    for b, kat_name in rows:
        if b.typ == "einnahme":
            summe_ein += float(b.betrag)
        else:
            summe_aus += float(b.betrag)
        pdf.cell(24, 6, b.datum.isoformat(), border=1)
        pdf.cell(22, 6, b.typ, border=1)
        pdf.cell(26, 6, f"{float(b.betrag):.2f}", border=1)
        pdf.cell(45, 6, (kat_name or "-")[:24], border=1)
        text = (b.buchungstext or b.haendler or "")[:40]
        pdf.cell(0, 6, text, border=1, new_x="LMARGIN", new_y="NEXT")

    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(
        0,
        7,
        f"Einnahmen: {summe_ein:.2f} EUR   Ausgaben: {summe_aus:.2f} EUR   "
        f"Saldo: {summe_ein - summe_aus:.2f} EUR",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    out = pdf.output()
    return bytes(out)


async def erstelle_export(
    session: AsyncSession, mandant_id: uuid.UUID, von: date, bis: date, format: str
) -> Export:
    if format == "datev_csv":
        data = await erstelle_datev_csv(session, mandant_id, von, bis)
        ext = "csv"
        content_type = "text/csv"
    elif format == "pdf":
        data = await erstelle_pdf_bundle(session, mandant_id, von, bis)
        ext = "pdf"
        content_type = "application/pdf"
    else:
        raise ValueError(f"Unbekanntes Export-Format: {format}")

    anzahl = len(await _buchungen_im_zeitraum(session, mandant_id, von, bis))

    sha256 = compute_sha256(data)
    storage_key = f"exports/{mandant_id}/{von.isoformat()}_{bis.isoformat()}_{sha256[:12]}.{ext}"
    storage = get_storage()
    if not await storage.exists(storage_key):
        await storage.put_write_once(storage_key, data, content_type)

    export = Export(
        mandant_id=mandant_id,
        format=format,
        von=von,
        bis=bis,
        storage_key=storage_key,
        sha256_hash=sha256,
        anzahl_buchungen=anzahl,
    )
    session.add(export)
    await session.flush()
    await append_audit(
        session,
        mandant_id=mandant_id,
        entity_type="export",
        entity_id=export.id,
        action="export.created",
        actor="system",
        payload={
            "format": format,
            "von": von.isoformat(),
            "bis": bis.isoformat(),
            "anzahl_buchungen": anzahl,
            "sha256": sha256,
        },
    )
    return export
