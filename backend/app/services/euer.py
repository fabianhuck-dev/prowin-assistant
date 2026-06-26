"""EÜR-Aggregation.

Aggregiert ausschließlich aus bestätigten (= nicht stornierten) Buchungen.
Liefert eine reine Rechen-Vorschau — KEINE steuerliche Beratung (Regel 6).
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Buchung, Kategorie
from app.schemas.euer import EuerVorschau, KategorieSumme


async def _summen_nach_kategorie(
    session: AsyncSession, mandant_id: uuid.UUID, jahr: int, typ: str
) -> list[KategorieSumme]:
    von = date(jahr, 1, 1)
    bis = date(jahr, 12, 31)
    stmt = (
        select(
            Buchung.kategorie_id,
            Kategorie.name,
            func.coalesce(func.sum(Buchung.betrag), 0),
            func.count(Buchung.id),
        )
        .select_from(Buchung)
        .join(Kategorie, Kategorie.id == Buchung.kategorie_id, isouter=True)
        .where(
            Buchung.mandant_id == mandant_id,
            Buchung.typ == typ,
            Buchung.storniert.is_(False),
            Buchung.datum >= von,
            Buchung.datum <= bis,
        )
        .group_by(Buchung.kategorie_id, Kategorie.name)
        .order_by(Kategorie.name)
    )
    rows = (await session.execute(stmt)).all()
    return [
        KategorieSumme(
            kategorie_id=kat_id,
            kategorie=name or "Ohne Kategorie",
            typ=typ,
            summe=float(summe),
            anzahl=int(anzahl),
        )
        for kat_id, name, summe, anzahl in rows
    ]


async def berechne_euer(
    session: AsyncSession, mandant_id: uuid.UUID, jahr: int
) -> EuerVorschau:
    einnahmen = await _summen_nach_kategorie(session, mandant_id, jahr, "einnahme")
    ausgaben = await _summen_nach_kategorie(session, mandant_id, jahr, "ausgabe")

    einnahmen_gesamt = round(sum(k.summe for k in einnahmen), 2)
    ausgaben_gesamt = round(sum(k.summe for k in ausgaben), 2)
    anzahl = sum(k.anzahl for k in einnahmen) + sum(k.anzahl for k in ausgaben)

    return EuerVorschau(
        mandant_id=mandant_id,
        jahr=jahr,
        einnahmen_gesamt=einnahmen_gesamt,
        ausgaben_gesamt=ausgaben_gesamt,
        gewinn=round(einnahmen_gesamt - ausgaben_gesamt, 2),
        anzahl_buchungen=anzahl,
        einnahmen_nach_kategorie=einnahmen,
        ausgaben_nach_kategorie=ausgaben,
    )
