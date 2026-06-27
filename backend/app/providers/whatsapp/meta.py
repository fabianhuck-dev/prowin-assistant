"""Meta WhatsApp Cloud API Provider.

Direktanbindung an die Meta Graph API — kein BSP-Zwischendienstleister.
Media-Downloads laufen serverseitig und landen direkt im write-once Storage.

24-h-Kundendienstfenster:
  Innerhalb von 24 h nach dem letzten Nutzer-Kontakt: free-form + interaktive Buttons.
  Außerhalb: nur genehmigte Templates (send_template). Templates müssen separat von Meta
  genehmigt werden — bis dahin ist send_template als TODO markiert.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import settings
from app.providers.whatsapp.base import OutboundMessage, WhatsAppProvider

logger = logging.getLogger("prowin.whatsapp.meta")

_RETRY_STATUSES = {429, 500, 502, 503, 504}
_RETRY_DELAYS = [1.0, 2.0, 4.0]


async def _request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs,
) -> httpx.Response:
    """HTTP-Request mit exponentiellem Backoff bei 429/5xx."""
    last_exc: Exception | None = None
    for attempt, delay in enumerate([0.0] + _RETRY_DELAYS):
        if delay:
            await asyncio.sleep(delay)
        try:
            resp = await client.request(method, url, **kwargs)
            if resp.status_code not in _RETRY_STATUSES:
                return resp
            last_exc = None
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt >= len(_RETRY_DELAYS):
                raise
    if last_exc:
        raise last_exc
    return resp  # type: ignore[return-value]


class MetaWhatsAppProvider(WhatsAppProvider):
    """Echter WhatsApp-Provider via Meta Graph API v{graph_version}."""

    def __init__(self) -> None:
        self._token = settings.whatsapp_access_token
        self._phone_number_id = settings.whatsapp_phone_number_id
        self._base_url = f"https://graph.facebook.com/{settings.whatsapp_graph_version}"

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def send_message(self, msg: OutboundMessage) -> str:
        """Sendet Text- oder interaktive Button-Nachricht (innerhalb 24-h-Fenster).

        Gibt die wamid der gesendeten Nachricht zurück.
        """
        payload = self._build_interactive(msg) if msg.buttons else self._build_text(msg)

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await _request_with_retry(
                client,
                "POST",
                f"{self._base_url}/{self._phone_number_id}/messages",
                json=payload,
                headers=self._auth_headers(),
            )
        resp.raise_for_status()
        data = resp.json()
        return data.get("messages", [{}])[0].get("id", "")

    def _build_text(self, msg: OutboundMessage) -> dict:
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": msg.to,
            "type": "text",
            "text": {"preview_url": False, "body": msg.text},
        }

    def _build_interactive(self, msg: OutboundMessage) -> dict:
        buttons = [
            {
                "type": "reply",
                "reply": {
                    "id": btn["id"],
                    "title": btn["title"][:20],
                },
            }
            for btn in (msg.buttons or [])[:3]
        ]
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": msg.to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": msg.text},
                "action": {"buttons": buttons},
            },
        }

    async def download_media(self, media_id: str) -> bytes:
        """Zweistufiger serverseitiger Media-Download.

        Schritt 1: media_id → Graph-API → kurzlebige Download-URL.
        Schritt 2: URL → Bytes (mit Bearer-Token).

        Ergebnis direkt in den write-once Storage (via ingest_beleg im Caller).
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp1 = await _request_with_retry(
                client,
                "GET",
                f"{self._base_url}/{media_id}",
                headers=self._auth_headers(),
            )
            resp1.raise_for_status()
            download_url: str = resp1.json()["url"]

            resp2 = await _request_with_retry(
                client,
                "GET",
                download_url,
                headers=self._auth_headers(),
            )
            resp2.raise_for_status()
            return resp2.content

    async def send_template(
        self,
        to: str,
        template_name: str,
        language: str = "de",
        components: list | None = None,
    ) -> str:
        """Template-Nachricht für außerhalb des 24-h-Fensters.

        TODO: Template-Namen müssen von Meta genehmigt sein (Meta App Review).
        Bis zur Genehmigung können keine Template-Nachrichten versendet werden.
        Nutzbar z. B. für monatliche Abschluss-Erinnerungen (proaktive Kontaktaufnahme).
        """
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
                "components": components or [],
            },
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await _request_with_retry(
                client,
                "POST",
                f"{self._base_url}/{self._phone_number_id}/messages",
                json=payload,
                headers=self._auth_headers(),
            )
        resp.raise_for_status()
        data = resp.json()
        return data.get("messages", [{}])[0].get("id", "")
