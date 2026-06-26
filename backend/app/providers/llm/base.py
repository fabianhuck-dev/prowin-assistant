from abc import ABC, abstractmethod

from pydantic import BaseModel


class LlmVorschlag(BaseModel):
    belegtyp: str  # ausgabe | einnahme | provision | kundenrechnung | unbekannt
    betrag: float | None
    datum: str | None
    haendler: str | None
    kategorie_vorschlag: str | None
    confidence: float
    fehlende_felder: list[str]
    rueckfrage_text: str | None


class LlmProvider(ABC):
    @abstractmethod
    async def klassifiziere(self, ocr_result: dict, kontext: dict) -> LlmVorschlag: ...
