"""Mistral Small LLM Provider für Beleg-Klassifikation.

Nimmt den OCR-Text + extrahierte Felder und liefert einen LlmVorschlag:
belegtyp, Kategorie aus der ProWin-Kategorieliste, confidence, ggf. Rückfrage.

Compliance: Gibt IMMER nur einen Vorschlag zurück — bucht nie selbst.
"""

from __future__ import annotations

import json
import logging

import httpx

from app.config import settings
from app.providers.llm.base import LlmProvider, LlmVorschlag

logger = logging.getLogger("prowin.llm.mistral")

_KATEGORIEN_AUSGABE = [
    "Wareneinkauf ProWin",
    "Vorführmaterial/Proben",
    "Fahrtkosten",
    "Bewirtung",
    "Telefon/Internet (anteilig)",
    "Bürobedarf",
    "Sonstiges",
]
_KATEGORIEN_EINNAHME = [
    "Provision ProWin",
    "Produktverkauf",
    "Sonstige Einnahme",
]

_SYSTEM_TEMPLATE = """\
Du bist Buchhalter für einen ProWin-Direktvertriebspartner in Deutschland.
Klassifiziere den folgenden Beleg anhand der OCR-Daten.

Kontext des Mandanten:
- Kleinunternehmer (§ 19 UStG): {kleinunternehmer}
- Bekannte Händler/Partner: {bekannte_haendler}

Antworte NUR mit diesem JSON-Objekt (kein Markdown, keine Erklärungen):
{{
  "belegtyp": "ausgabe" | "einnahme" | "provision" | "unbekannt",
  "betrag": <float in EUR oder null>,
  "datum": <"YYYY-MM-DD" oder null>,
  "haendler": <string oder null>,
  "kategorie_vorschlag": <string aus der Kategorie-Liste oder null>,
  "confidence": <0.0–1.0>,
  "fehlende_felder": <Liste der fehlenden Pflichtfelder, z. B. ["betrag", "datum"]>,
  "rueckfrage_text": <freundliche Rückfrage auf Deutsch oder null>
}}

Belegtyp-Regeln:
- "ausgabe": Kosten/Ausgaben des Vertriebspartners (Tankquittung, Einkauf, Bürobedarf, …)
- "provision": Provisionsabrechnung / Gutschrift von ProWin
- "einnahme": sonstige Einnahmen (Produktverkauf an Kunden, …)
- "unbekannt": Belegtyp nicht erkennbar

Kategorie-Optionen für ausgabe:
{kategorien_ausgabe}

Kategorie-Optionen für einnahme / provision:
{kategorien_einnahme}

Rückfrage-Regeln:
- Setze rueckfrage_text wenn confidence < 0.60 ODER ein Pflichtfeld (betrag, datum) fehlt.
- Formuliere die Rückfrage präzise (was genau fehlt oder unklar ist).
- Sonst rueckfrage_text = null.
"""


class MistralLlmProvider(LlmProvider):
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.mistral_api_key}",
            "Content-Type": "application/json",
        }

    async def klassifiziere(self, ocr_result: dict, kontext: dict) -> LlmVorschlag:
        system = _SYSTEM_TEMPLATE.format(
            kleinunternehmer="ja" if kontext.get("is_kleinunternehmer") else "nein",
            bekannte_haendler=", ".join(kontext.get("bekannte_haendler", [])) or "keine",
            kategorien_ausgabe="\n".join(f"  - {k}" for k in _KATEGORIEN_AUSGABE),
            kategorien_einnahme="\n".join(f"  - {k}" for k in _KATEGORIEN_EINNAHME),
        )

        user = (
            f"OCR-Text:\n{ocr_result.get('raw_text', '(kein Text)')}\n\n"
            f"Bereits extrahierte Felder:\n"
            f"  betrag:   {ocr_result.get('betrag')}\n"
            f"  datum:    {ocr_result.get('datum')}\n"
            f"  haendler: {ocr_result.get('haendler')}\n"
            f"  confidence (OCR): {ocr_result.get('confidence')}"
        )

        payload = {
            "model": settings.llm_model,
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
            "max_tokens": 512,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{settings.mistral_api_base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
        resp.raise_for_status()

        raw_content = resp.json()["choices"][0]["message"]["content"]
        logger.debug("LLM-Antwort: %.500s", raw_content)

        try:
            data = json.loads(raw_content)
        except ValueError:
            logger.warning("LLM: ungültiges JSON: %.200s", raw_content)
            return _fallback(ocr_result)

        return _parse_vorschlag(data, ocr_result)


def _parse_vorschlag(data: dict, ocr_result: dict) -> LlmVorschlag:
    betrag = _to_float(data.get("betrag")) or _to_float(ocr_result.get("betrag"))
    datum = data.get("datum") or ocr_result.get("datum")
    haendler = data.get("haendler") or ocr_result.get("haendler") or None
    confidence = float(data.get("confidence") or 0.5)
    fehlende = list(data.get("fehlende_felder") or [])

    # Fehlende Felder im Code prüfen (Regel 7 — nicht vom LLM abhängen).
    if betrag is None and "betrag" not in fehlende:
        fehlende.append("betrag")
    if datum is None and "datum" not in fehlende:
        fehlende.append("datum")

    rueckfrage = data.get("rueckfrage_text") or None
    if not rueckfrage and (confidence < 0.60 or fehlende):
        fehlende_str = ", ".join(fehlende)
        rueckfrage = (
            f"Ich bin mir nicht sicher. Fehlende Angaben: {fehlende_str}. Kannst du helfen?"
        )

    return LlmVorschlag(
        belegtyp=data.get("belegtyp") or "unbekannt",
        betrag=betrag,
        datum=datum,
        haendler=haendler,
        kategorie_vorschlag=data.get("kategorie_vorschlag") or None,
        confidence=confidence,
        fehlende_felder=fehlende,
        rueckfrage_text=rueckfrage,
    )


def _fallback(ocr_result: dict) -> LlmVorschlag:
    return LlmVorschlag(
        belegtyp="unbekannt",
        betrag=_to_float(ocr_result.get("betrag")),
        datum=ocr_result.get("datum"),
        haendler=ocr_result.get("haendler"),
        kategorie_vorschlag=None,
        confidence=0.0,
        fehlende_felder=["belegtyp"],
        rueckfrage_text=(
            "Ich konnte den Beleg leider nicht klassifizieren. "
            "Um welche Art von Ausgabe oder Einnahme handelt es sich?"
        ),
    )


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except (ValueError, TypeError):
        return None
