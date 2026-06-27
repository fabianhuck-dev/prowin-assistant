"""Provider-Factory. Auswahl per Env-Variable (stub | live)."""

from __future__ import annotations

from app.config import settings


def get_whatsapp_provider():
    if settings.whatsapp_provider == "stub":
        from app.providers.whatsapp.stub import StubWhatsAppProvider

        return StubWhatsAppProvider()
    if settings.whatsapp_provider == "meta":
        from app.providers.whatsapp.meta import MetaWhatsAppProvider

        return MetaWhatsAppProvider()
    raise ValueError(f"Unknown WhatsApp provider: {settings.whatsapp_provider}")


def get_ocr_provider():
    if settings.ocr_provider == "stub":
        from app.providers.ocr.stub import StubOcrProvider

        return StubOcrProvider()
    if settings.ocr_provider == "live":
        from app.providers.ocr.live import MistralOcrProvider

        return MistralOcrProvider()
    raise ValueError(f"Unknown OCR provider: {settings.ocr_provider}")


def get_llm_provider():
    if settings.llm_provider == "stub":
        from app.providers.llm.stub import StubLlmProvider

        return StubLlmProvider()
    if settings.llm_provider == "live":
        from app.providers.llm.live import MistralLlmProvider

        return MistralLlmProvider()
    raise ValueError(f"Unknown LLM provider: {settings.llm_provider}")
