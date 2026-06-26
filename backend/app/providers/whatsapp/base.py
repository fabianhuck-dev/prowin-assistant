from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class InboundMessage:
    phone: str
    message_type: str  # "image" | "document" | "text"
    media_url: str | None
    text: str | None
    message_id: str


@dataclass
class OutboundMessage:
    to: str
    text: str
    buttons: list[dict] | None = None


class WhatsAppProvider(ABC):
    @abstractmethod
    async def send_message(self, msg: OutboundMessage) -> str: ...

    @abstractmethod
    async def download_media(self, media_url: str) -> bytes: ...
