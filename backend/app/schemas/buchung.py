from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class Korrekturen(BaseModel):
    """Optionale menschliche Korrekturen am Vorschlag vor dem Buchen."""

    kategorie_id: uuid.UUID | None = None
    betrag: float | None = None
    datum: date | None = None
    haendler: str | None = None
    typ: str | None = None
    buchungstext: str | None = None


class BuchungConfirmRequest(BaseModel):
    """Explizite menschliche Bestätigung -> erst hierdurch entsteht eine Buchung."""

    beleg_id: uuid.UUID
    mandant_id: uuid.UUID
    bestaetigt_via: str = "dashboard"  # "whatsapp_button" | "dashboard"
    korrekturen: Korrekturen | None = None


class BuchungPatchRequest(BaseModel):
    """Korrektur einer bestehenden Buchung: Storno der alten + Neuanlage (kein Overwrite)."""

    mandant_id: uuid.UUID
    korrekturen: Korrekturen
    bestaetigt_via: str = "dashboard"


class BuchungOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mandant_id: uuid.UUID
    beleg_id: uuid.UUID | None
    kategorie_id: uuid.UUID | None
    typ: str
    betrag: float
    datum: date
    haendler: str | None
    buchungstext: str | None
    bestaetigt_via: str | None
    bestaetigt_am: datetime | None
    storniert: bool
    storno_von_id: uuid.UUID | None
    created_at: datetime
