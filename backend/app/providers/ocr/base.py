from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class OcrResult:
    raw_text: str
    betrag: float | None
    datum: str | None  # ISO-Format YYYY-MM-DD
    haendler: str | None
    confidence: float
    raw_json: dict = field(default_factory=dict)


class OcrProvider(ABC):
    @abstractmethod
    async def extract(self, image_data: bytes, mime_type: str) -> OcrResult: ...
