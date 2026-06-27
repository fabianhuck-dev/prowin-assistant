"""Mistral OCR Provider (mistral-ocr-latest + Mistral Small für Feld-Extraktion).

Zweistufig:
  1. Mistral OCR → Markdown-Text (präzise Texterkennung aus Bild/PDF)
  2. Mistral Small → strukturierte Feld-Extraktion (betrag, datum, haendler)

Belege werden per Base64 übergeben — kein Upload in externe Systeme außer der
Mistral API (Unterauftragsverarbeiter, s. COMPLIANCE.md §9).
"""

from __future__ import annotations

import base64
import json
import logging

import httpx

from app.config import settings
from app.providers.ocr.base import OcrProvider, OcrResult

logger = logging.getLogger("prowin.ocr.mistral")

_EXTRACT_SYSTEM = """\
Du extrahierst Daten aus dem OCR-Text eines deutschen Belegs.

Antworte NUR mit diesem JSON-Objekt (keine Erklärungen, kein Markdown):
{
  "betrag": <Gesamtbetrag als Dezimalzahl in EUR, z. B. 45.50 — null wenn nicht eindeutig>,
  "datum": <Belegdatum als "YYYY-MM-DD" — null wenn nicht gefunden>,
  "haendler": <Name des Ausstellers/Händlers — null wenn nicht gefunden>,
  "confidence": <0.0–1.0, wie sicher du dir bei der Extraktion bist>
}

Regeln:
- Betrag: nimm den Gesamtbetrag/Summe (nicht Teilbeträge). Komma als Dezimaltrennzeichen umwandeln.
- Datum: erkenne Formate wie 15.01.2024, 15/01/2024, 2024-01-15, "15. Januar 2024".
- Haendler: Firmenname oder Ausstellername oben auf dem Beleg.
- confidence: 0.9+ wenn alle drei Felder klar lesbar; 0.5 wenn unsicher; 0.0 wenn Text unleserlich.
"""


class MistralOcrProvider(OcrProvider):
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.mistral_api_key}",
            "Content-Type": "application/json",
        }

    async def extract(self, image_data: bytes, mime_type: str) -> OcrResult:
        raw_text = await self._ocr_to_text(image_data, mime_type)
        fields = await self._extract_fields(raw_text)

        return OcrResult(
            raw_text=raw_text,
            betrag=_to_float(fields.get("betrag")),
            datum=_to_date_str(fields.get("datum")),
            haendler=fields.get("haendler") or None,
            confidence=float(fields.get("confidence") or 0.5),
            raw_json={
                "ocr_model": settings.ocr_model,
                "llm_model": settings.llm_model,
                "extracted": fields,
            },
        )

    async def _ocr_to_text(self, image_data: bytes, mime_type: str) -> str:
        """Schritt 1: Mistral OCR → Markdown-Text."""
        b64 = base64.b64encode(image_data).decode()

        # PDFs als document_url, Bilder als image_url
        if mime_type == "application/pdf":
            doc = {
                "type": "document_url",
                "document_url": f"data:application/pdf;base64,{b64}",
            }
        else:
            effective_mime = mime_type if mime_type.startswith("image/") else "image/jpeg"
            doc = {
                "type": "image_url",
                "image_url": f"data:{effective_mime};base64,{b64}",
            }

        payload = {"model": settings.ocr_model, "document": doc}

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{settings.mistral_api_base_url}/ocr",
                json=payload,
                headers=self._headers(),
            )
        resp.raise_for_status()
        data = resp.json()
        pages = data.get("pages", [])
        text = "\n\n".join(p.get("markdown", "") for p in pages).strip()
        logger.debug("OCR-Text (%d Seiten, %d Zeichen)", len(pages), len(text))
        return text

    async def _extract_fields(self, raw_text: str) -> dict:
        """Schritt 2: Mistral Small → strukturierte Felder aus OCR-Text."""
        if not raw_text.strip():
            return {"confidence": 0.0}

        payload = {
            "model": settings.llm_model,
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
            "max_tokens": 256,
            "messages": [
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": f"OCR-Text:\n{raw_text}"},
            ],
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{settings.mistral_api_base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]

        try:
            return json.loads(content)
        except ValueError:
            logger.warning("Feld-Extraktion: ungültiges JSON vom LLM: %.200s", content)
            return {"confidence": 0.4}


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except (ValueError, TypeError):
        return None


def _to_date_str(value) -> str | None:
    if not value or not isinstance(value, str):
        return None
    # Bereits ISO-Format
    if len(value) == 10 and value[4] == "-":
        return value
    return None
