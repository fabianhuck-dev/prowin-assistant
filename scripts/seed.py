"""Seed-Skript: System-Default-Kategorien + Test-Mandant.

Aufruf:  cd backend && uv run python ../scripts/seed.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# backend/ auf den Pfad legen, damit `app` importierbar ist.
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

# (name, typ)
DEFAULT_KATEGORIEN: list[tuple[str, str]] = [
    # Ausgaben
    ("Wareneinkauf ProWin", "ausgabe"),
    ("Vorführmaterial/Proben", "ausgabe"),
    ("Fahrtkosten", "ausgabe"),
    ("Bewirtung", "ausgabe"),
    ("Telefon/Internet (anteilig)", "ausgabe"),
    ("Bürobedarf", "ausgabe"),
    ("Sonstiges", "ausgabe"),
    # Einnahmen
    ("Provision ProWin", "einnahme"),
    ("Produktverkauf", "einnahme"),
    ("Sonstige Einnahme", "einnahme"),
]

TEST_MANDANT = {
    "name": "Max Muster",
    "whatsapp_phone": "+4915100000000",
    "is_kleinunternehmer": True,
}


async def seed_kategorien(session: AsyncSession) -> int:
    from app.db.models import Kategorie

    created = 0
    for name, typ in DEFAULT_KATEGORIEN:
        exists = await session.scalar(
            select(Kategorie).where(
                Kategorie.name == name, Kategorie.is_system_default.is_(True)
            )
        )
        if exists is None:
            session.add(Kategorie(name=name, typ=typ, is_system_default=True))
            created += 1
    return created


async def seed_mandant(session: AsyncSession):
    from app.db.models import Mandant

    mandant = await session.scalar(
        select(Mandant).where(Mandant.whatsapp_phone == TEST_MANDANT["whatsapp_phone"])
    )
    if mandant is None:
        mandant = Mandant(**TEST_MANDANT)
        session.add(mandant)
    return mandant


async def main() -> None:
    from app.db.base import SessionFactory

    async with SessionFactory() as session:
        n = await seed_kategorien(session)
        mandant = await seed_mandant(session)
        await session.commit()
        await session.refresh(mandant)
        print(f"Seed abgeschlossen: {n} Kategorien neu, Mandant={mandant.name} ({mandant.id})")


if __name__ == "__main__":
    asyncio.run(main())
