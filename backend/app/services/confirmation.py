"""Bestätigungs-Service.

WICHTIG (Compliance Regel 1 & 2):
``bestaetige_und_buche`` ist der EINZIGE Pfad im gesamten System, der eine
``buchung``-Zeile erzeugt. Er wird ausschließlich aufgerufen, NACHDEM ein Mensch
explizit bestätigt hat (WhatsApp-Button oder Dashboard). Ohne diesen Aufruf
entsteht KEINE Buchung — egal wie hoch die Confidence ist.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Beleg, Buchung, Kategorie
from app.services.audit import append_audit

_EINNAHME_TYPEN = {"einnahme", "provision", "kundenrechnung"}


def _normalize_typ(belegtyp: str | None) -> str:
    if belegtyp in _EINNAHME_TYPEN:
        return "einnahme"
    return "ausgabe"


def _parse_iso_date(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


async def _resolve_kategorie_id(
    session: AsyncSession, kategorie_name: str | None, typ: str
) -> uuid.UUID | None:
    if not kategorie_name:
        return None
    kat = await session.scalar(
        select(Kategorie).where(Kategorie.name == kategorie_name, Kategorie.typ == typ)
    )
    if kat is None:
        kat = await session.scalar(select(Kategorie).where(Kategorie.name == kategorie_name))
    return kat.id if kat else None


async def bestaetige_und_buche(
    session: AsyncSession,
    beleg_id: uuid.UUID,
    mandant_id: uuid.UUID,
    bestaetigt_via: str,
    korrekturen: dict | None = None,
) -> Buchung:
    """Erzeugt nach menschlicher Bestätigung genau EINE Buchung aus einem Beleg."""
    korrekturen = korrekturen or {}

    beleg = await session.get(Beleg, beleg_id)
    if beleg is None:
        raise ValueError(f"Beleg {beleg_id} nicht gefunden")
    if beleg.mandant_id != mandant_id:
        raise ValueError("Beleg gehört nicht zu diesem Mandanten")
    if beleg.status == "bestaetigt":
        raise ValueError("Beleg wurde bereits gebucht")

    vorschlag = beleg.llm_vorschlag or {}

    # Werte ableiten: Korrektur > Vorschlag > Beleg-Extraktion.
    typ = korrekturen.get("typ") or _normalize_typ(vorschlag.get("belegtyp") or beleg.belegtyp)
    betrag = korrekturen.get("betrag")
    if betrag is None:
        betrag = vorschlag.get("betrag") if vorschlag.get("betrag") is not None else beleg.betrag
    datum = (
        _parse_iso_date(korrekturen.get("datum"))
        or _parse_iso_date(vorschlag.get("datum"))
        or beleg.datum
    )
    haendler = korrekturen.get("haendler") or vorschlag.get("haendler") or beleg.haendler
    buchungstext = korrekturen.get("buchungstext") or f"Beleg {beleg.id}"

    # Validierung im Code (Regel 7) — niemals eine Buchung mit fehlenden Pflichtwerten.
    if betrag is None:
        raise ValueError("Kein Betrag vorhanden — Buchung nicht möglich")
    if float(betrag) <= 0:
        raise ValueError("Betrag muss positiv sein")
    if datum is None:
        datum = date.today()

    kategorie_id = korrekturen.get("kategorie_id") or await _resolve_kategorie_id(
        session, vorschlag.get("kategorie_vorschlag"), typ
    )

    buchung = Buchung(
        mandant_id=mandant_id,
        beleg_id=beleg.id,
        kategorie_id=kategorie_id,
        typ=typ,
        betrag=betrag,
        datum=datum,
        haendler=haendler,
        buchungstext=buchungstext,
        bestaetigt_via=bestaetigt_via,
        bestaetigt_am=datetime.now(UTC),
        storniert=False,
    )
    session.add(buchung)
    await session.flush()

    beleg.status = "bestaetigt"

    await append_audit(
        session,
        mandant_id=mandant_id,
        entity_type="buchung",
        entity_id=buchung.id,
        action="buchung.created",
        actor=f"mensch:{bestaetigt_via}",
        payload={
            "beleg_id": str(beleg.id),
            "typ": typ,
            "betrag": float(betrag),
            "datum": datum.isoformat(),
            "kategorie_id": str(kategorie_id) if kategorie_id else None,
            "bestaetigt_via": bestaetigt_via,
        },
    )
    return buchung


async def storniere_und_neu(
    session: AsyncSession,
    buchung_id: uuid.UUID,
    mandant_id: uuid.UUID,
    korrekturen: dict,
    bestaetigt_via: str,
) -> tuple[Buchung, Buchung]:
    """Korrektur OHNE Overwrite (Regel 2/4 + T9).

    Setzt die alte Buchung auf storniert=True und legt eine NEUE Buchung an.
    Erzeugt ZWEI Audit-Einträge (Storno + Neuanlage).
    """
    alt = await session.get(Buchung, buchung_id)
    if alt is None:
        raise ValueError(f"Buchung {buchung_id} nicht gefunden")
    if alt.mandant_id != mandant_id:
        raise ValueError("Buchung gehört nicht zu diesem Mandanten")
    if alt.storniert:
        raise ValueError("Buchung ist bereits storniert")

    # 1) Alte Buchung stornieren (kein Overwrite der Geschäftsdaten).
    alt.storniert = True
    await session.flush()
    await append_audit(
        session,
        mandant_id=mandant_id,
        entity_type="buchung",
        entity_id=alt.id,
        action="buchung.storniert",
        actor=f"mensch:{bestaetigt_via}",
        payload={"grund": "korrektur"},
    )

    # 2) Neue Buchung mit Korrekturwerten anlegen.
    neu = Buchung(
        mandant_id=mandant_id,
        beleg_id=alt.beleg_id,
        kategorie_id=korrekturen.get("kategorie_id") or alt.kategorie_id,
        typ=korrekturen.get("typ") or alt.typ,
        betrag=korrekturen.get("betrag") if korrekturen.get("betrag") is not None else alt.betrag,
        datum=_parse_iso_date(korrekturen.get("datum")) or alt.datum,
        haendler=korrekturen.get("haendler") or alt.haendler,
        buchungstext=korrekturen.get("buchungstext") or alt.buchungstext,
        bestaetigt_via=bestaetigt_via,
        bestaetigt_am=datetime.now(UTC),
        storniert=False,
        storno_von_id=alt.id,
    )
    session.add(neu)
    await session.flush()
    await append_audit(
        session,
        mandant_id=mandant_id,
        entity_type="buchung",
        entity_id=neu.id,
        action="buchung.neu_aus_korrektur",
        actor=f"mensch:{bestaetigt_via}",
        payload={"storno_von_id": str(alt.id), "betrag": float(neu.betrag)},
    )
    return alt, neu
