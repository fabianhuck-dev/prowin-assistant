from __future__ import annotations

import uuid

from pydantic import BaseModel


class KategorieSumme(BaseModel):
    kategorie_id: uuid.UUID | None
    kategorie: str
    typ: str
    summe: float
    anzahl: int


class EuerVorschau(BaseModel):
    """Reine Rechen-Vorschau. KEINE Steuer-Rechtsauskunft (Regel 6)."""

    mandant_id: uuid.UUID
    jahr: int
    einnahmen_gesamt: float
    ausgaben_gesamt: float
    gewinn: float
    anzahl_buchungen: int
    einnahmen_nach_kategorie: list[KategorieSumme]
    ausgaben_nach_kategorie: list[KategorieSumme]
    hinweis: str = (
        "Dies ist eine unverbindliche Aufstellung zur Vorbereitung und ersetzt keine "
        "steuerliche Beratung. Stornierte Buchungen sind nicht enthalten."
    )
