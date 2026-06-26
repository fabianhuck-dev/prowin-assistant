"""Deterministischer WhatsApp-Stub für Tests und lokale Entwicklung."""

from __future__ import annotations

from app.providers.whatsapp.base import (
    InboundMessage,
    OutboundMessage,
    WhatsAppProvider,
)

# Deterministische Test-Mediendaten je media_url-Schlüssel.
STUB_MEDIA: dict[str, bytes] = {
    "tankquittung": b"STUB-IMAGE:tankquittung",
    "wareneinkauf": b"STUB-IMAGE:wareneinkauf",
    "provision": b"STUB-IMAGE:provision",
    "unbekannt": b"STUB-IMAGE:unbekannt",
    "kaputt": b"STUB-IMAGE:kaputt",
}


class StubWhatsAppProvider(WhatsAppProvider):
    def __init__(self) -> None:
        # Protokolliert gesendete Nachrichten, damit Tests sie inspizieren können.
        self.sent: list[OutboundMessage] = []

    async def send_message(self, msg: OutboundMessage) -> str:
        self.sent.append(msg)
        return f"stub-msg-{len(self.sent)}"

    async def download_media(self, media_url: str) -> bytes:
        key = (media_url or "").rsplit("/", 1)[-1]
        return STUB_MEDIA.get(key, b"STUB-IMAGE:default")

    def make_inbound(
        self, phone: str, kind: str = "tankquittung", message_id: str = "wamid.stub1"
    ) -> InboundMessage:
        """Hilfsfunktion für Tests/Demo: erzeugt eine eingehende Bild-Nachricht."""
        return InboundMessage(
            phone=phone,
            message_type="image",
            media_url=f"https://stub.local/media/{kind}",
            text=None,
            message_id=message_id,
        )
