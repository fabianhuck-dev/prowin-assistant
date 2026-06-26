"""End-to-End-Demo gegen die laufende API (httpx).

Voraussetzung:  docker compose up  +  make migrate  +  make seed
Aufruf:         make demo   (bzw. uv run python scripts/demo.py)

Durchlauf:
  1. Beleg ingest (Stub-Tankquittung, base64)
  2. Klassifikation abrufen (nur Vorschlag, KEINE Buchung)
  3. Bestätigung senden (confirm) -> erst hier entsteht die Buchung
  4. EÜR-Vorschau abrufen
  5. Export (DATEV-CSV) generieren
"""

from __future__ import annotations

import asyncio
import base64
import os
from datetime import date

import httpx

BASE_URL = os.environ.get("DEMO_BASE_URL", "http://localhost:8000")
PHONE = os.environ.get("DEMO_PHONE", "+4915100000000")


def _log(step: str, resp: httpx.Response, *felder: str) -> dict:
    print(f"\n=== {step} ===")
    print(f"  HTTP {resp.status_code}")
    try:
        data = resp.json()
    except Exception:
        print("  (keine JSON-Antwort)")
        return {}
    if isinstance(data, dict):
        for f in felder:
            if f in data:
                print(f"  {f}: {data[f]}")
    return data if isinstance(data, dict) else {}


async def _mandant_id(client: httpx.AsyncClient) -> str:
    # Mandant-ID über einen Webhook-Ingest ermitteln ist nicht nötig; wir nehmen
    # die Seed-Telefonnummer und lesen die ID aus der ersten Buchungs-/Beleg-Antwort.
    # Für die Demo erlauben wir die Übergabe per ENV, sonst Hinweis.
    mid = os.environ.get("DEMO_MANDANT_ID")
    if not mid:
        raise SystemExit(
            "Bitte DEMO_MANDANT_ID setzen (Ausgabe von `make seed`) oder den "
            "WhatsApp-Webhook nutzen. Beispiel: DEMO_MANDANT_ID=<uuid> make demo"
        )
    return mid


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        health = await client.get("/health")
        _log("0. Health", health, "status", "providers")

        mandant_id = await _mandant_id(client)

        # 1. Ingest (Stub erkennt die Tankquittung am Marker im Inhalt).
        content = base64.b64encode(b"STUB-IMAGE:tankquittung").decode()
        ingest = await client.post(
            "/belege/ingest",
            json={
                "mandant_id": mandant_id,
                "content_base64": content,
                "filename": "tankquittung.jpg",
                "mime_type": "image/jpeg",
                "quelle": "upload",
            },
        )
        ingest_data = _log("1. Beleg ingest", ingest, "beleg_id", "sha256_hash", "is_duplicate")
        beleg_id = ingest_data["beleg_id"]

        # 2. Klassifikation (nur Vorschlag!).
        klass = await client.post(f"/belege/{beleg_id}/klassifiziere")
        _log("2. Klassifikation (Vorschlag, KEINE Buchung)", klass, "vorschlag", "hinweis")

        # 3. Bestätigung -> erst jetzt entsteht die Buchung.
        confirm = await client.post(
            "/buchungen",
            json={
                "beleg_id": beleg_id,
                "mandant_id": mandant_id,
                "bestaetigt_via": "dashboard",
            },
        )
        _log("3. Bestaetigung (confirm -> Buchung)", confirm, "id", "typ", "betrag")

        # 4. EÜR-Vorschau.
        euer = await client.get(
            "/euer", params={"mandant_id": mandant_id, "jahr": date.today().year}
        )
        _log("4. EUeR-Vorschau", euer, "einnahmen_gesamt", "ausgaben_gesamt", "gewinn")

        # 5. Export (DATEV-CSV).
        export = await client.post(
            "/exports",
            json={
                "mandant_id": mandant_id,
                "von": f"{date.today().year}-01-01",
                "bis": f"{date.today().year}-12-31",
                "format": "datev_csv",
            },
        )
        _log("5. Export (DATEV-CSV)", export, "format", "anzahl_buchungen", "signed_url")

        print("\nDemo abgeschlossen.")


if __name__ == "__main__":
    asyncio.run(main())
