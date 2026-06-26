from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.providers.llm.base import LlmVorschlag


class VorschlagResponse(BaseModel):
    """Klassifikations-Ergebnis. Ausdrücklich nur ein VORSCHLAG, keine Buchung."""

    beleg_id: uuid.UUID
    vorschlag: LlmVorschlag
    rueckfrage_id: uuid.UUID | None = None
    plausi_warnung: str | None = None
    hinweis: str = "Dies ist nur ein Vorschlag. Eine Buchung entsteht erst nach Bestätigung."


__all__ = ["LlmVorschlag", "VorschlagResponse"]
