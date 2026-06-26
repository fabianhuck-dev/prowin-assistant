from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import belege, buchungen, webhooks
from app.config import settings
from app.services.immutability import S3Storage, get_storage

logger = logging.getLogger("prowin")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Bucket beim Startup anlegen (nur für echtes S3/MinIO-Backend).
    storage = get_storage()
    if isinstance(storage, S3Storage):
        try:
            await storage.ensure_bucket()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Bucket-Initialisierung übersprungen: %s", exc)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="ProWin Buchhaltungs-Assistent",
        version="0.1.0",
        description=(
            "WhatsApp-zentrierter Beleg- & EÜR-Vorbereitungs-Assistent. "
            "Der LLM bucht nie selbst — Buchungen entstehen nur nach menschlicher Bestätigung."
        ),
        lifespan=lifespan,
    )

    app.include_router(belege.router)
    app.include_router(buchungen.router)
    app.include_router(webhooks.router)

    # Optionale Router (in späteren Phasen ergänzt) defensiv registrieren.
    try:
        from app.api import euer, exports

        app.include_router(euer.router)
        app.include_router(exports.router)
    except ImportError:  # pragma: no cover
        pass
    try:
        from app.api import audit

        app.include_router(audit.router)
    except ImportError:  # pragma: no cover
        pass

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        return {
            "status": "ok",
            "providers": {
                "whatsapp": settings.whatsapp_provider,
                "ocr": settings.ocr_provider,
                "llm": settings.llm_provider,
            },
        }

    return app


app = create_app()
