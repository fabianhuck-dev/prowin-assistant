"""Deterministischer OCR-Stub.

Wählt das Ergebnis anhand eines Markers in den Bilddaten (``STUB-IMAGE:<kind>``).
So lassen sich die Testfälle T1–T7 reproduzierbar auslösen.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.providers.ocr.base import OcrProvider, OcrResult


class OcrExtractionError(Exception):
    """Simuliert ein fehlgeschlagenes OCR (T5)."""


def _kind_from_data(image_data: bytes) -> str:
    text = image_data.decode("utf-8", errors="ignore")
    if ":" in text:
        return text.rsplit(":", 1)[-1].strip()
    return "default"


class StubOcrProvider(OcrProvider):
    async def extract(self, image_data: bytes, mime_type: str) -> OcrResult:
        kind = _kind_from_data(image_data)
        heute = date.today().isoformat()

        if kind == "tankquittung":
            return OcrResult(
                raw_text="ARAL Station\nSuper E10\nSUMME 45,50 EUR",
                betrag=45.50,
                datum=heute,
                haendler="ARAL München",
                confidence=0.95,
                raw_json={"kind": kind, "items": ["Super E10"]},
            )
        if kind == "wareneinkauf":
            return OcrResult(
                raw_text="ProWin GmbH\nReiniger-Set\nGesamt 128,00 EUR",
                betrag=128.00,
                datum=heute,
                haendler="ProWin GmbH",
                confidence=0.92,
                raw_json={"kind": kind},
            )
        if kind == "provision":
            return OcrResult(
                raw_text="ProWin\nProvisionsabrechnung\nAuszahlung 350,00 EUR",
                betrag=350.00,
                datum=heute,
                haendler="ProWin",
                confidence=0.88,
                raw_json={"kind": kind},
            )
        if kind == "unbekannt":
            return OcrResult(
                raw_text="unleserlicher beleg",
                betrag=None,
                datum=None,
                haendler=None,
                confidence=0.45,
                raw_json={"kind": kind},
            )
        if kind == "zukunft":
            # T7: Datum in der Zukunft -> Plausibilitätswarnung im Code.
            future = (date.today() + timedelta(days=30)).isoformat()
            return OcrResult(
                raw_text="Beleg mit Zukunftsdatum\n42,00 EUR",
                betrag=42.00,
                datum=future,
                haendler="Test Händler",
                confidence=0.90,
                raw_json={"kind": kind},
            )
        if kind == "kaputt":
            raise OcrExtractionError("OCR konnte das Bild nicht verarbeiten")

        return OcrResult(
            raw_text="generischer beleg\n10,00 EUR",
            betrag=10.00,
            datum=heute,
            haendler="Unbekannt",
            confidence=0.70,
            raw_json={"kind": "default"},
        )
