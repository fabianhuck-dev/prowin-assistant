"""Deterministischer LLM-Stub.

WICHTIG (Compliance): Dieser Provider liefert ausschließlich einen VORSCHLAG.
Er bucht niemals. Die Entscheidung über eine Buchung trifft allein der Mensch
(siehe services/confirmation.py).
"""

from __future__ import annotations

from app.providers.llm.base import LlmProvider, LlmVorschlag

# Schwelle, unterhalb derer keine Kategorie vorgeschlagen, sondern rückgefragt wird.
CONFIDENCE_RUECKFRAGE = 0.60

_KIND_MAP: dict[str, tuple[str, str]] = {
    # kind -> (belegtyp, kategorie_vorschlag)
    "tankquittung": ("ausgabe", "Fahrtkosten"),
    "wareneinkauf": ("ausgabe", "Wareneinkauf ProWin"),
    "provision": ("provision", "Provision ProWin"),
}


class StubLlmProvider(LlmProvider):
    async def klassifiziere(self, ocr_result: dict, kontext: dict) -> LlmVorschlag:
        raw_json = ocr_result.get("raw_json") or {}
        kind = raw_json.get("kind", "default")
        confidence = float(ocr_result.get("confidence") or 0.0)

        betrag = ocr_result.get("betrag")
        datum = ocr_result.get("datum")
        haendler = ocr_result.get("haendler")

        fehlende_felder: list[str] = []
        if betrag is None:
            fehlende_felder.append("betrag")
        if datum is None:
            fehlende_felder.append("datum")
        if haendler is None:
            fehlende_felder.append("haendler")

        belegtyp, _kat = _KIND_MAP.get(kind, ("ausgabe", "Sonstiges"))
        kategorie: str | None = _kat

        # Niedrige Confidence oder unbekannter Beleg -> keine Kategorie, Rückfrage.
        if confidence < CONFIDENCE_RUECKFRAGE or kind == "unbekannt":
            belegtyp = "unbekannt"
            kategorie = None
            rueckfrage = (
                "Ich konnte den Beleg nicht sicher zuordnen. "
                "Um welche Art von Beleg handelt es sich und wie hoch ist der Betrag?"
            )
            return LlmVorschlag(
                belegtyp=belegtyp,
                betrag=betrag,
                datum=datum,
                haendler=haendler,
                kategorie_vorschlag=kategorie,
                confidence=confidence,
                fehlende_felder=fehlende_felder,
                rueckfrage_text=rueckfrage,
            )

        rueckfrage_text = None
        if fehlende_felder:
            rueckfrage_text = (
                "Mir fehlen noch folgende Angaben: " + ", ".join(fehlende_felder) + "."
            )

        return LlmVorschlag(
            belegtyp=belegtyp,
            betrag=betrag,
            datum=datum,
            haendler=haendler,
            kategorie_vorschlag=kategorie,
            confidence=confidence,
            fehlende_felder=fehlende_felder,
            rueckfrage_text=rueckfrage_text,
        )
