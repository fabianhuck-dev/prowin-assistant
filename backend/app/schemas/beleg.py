from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class BelegIngestRequest(BaseModel):
    mandant_id: uuid.UUID
    # Inhalt base64-kodiert (für JSON-Ingest ohne Multipart).
    content_base64: str
    filename: str = "beleg.jpg"
    mime_type: str = "image/jpeg"
    quelle: str = "upload"


class BelegIngestResponse(BaseModel):
    beleg_id: uuid.UUID
    storage_key: str
    sha256_hash: str
    status: str
    is_duplicate: bool


class BelegOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mandant_id: uuid.UUID
    storage_key: str
    sha256_hash: str
    original_filename: str | None
    mime_type: str | None
    betrag: float | None
    datum: date | None
    haendler: str | None
    belegtyp: str | None
    ocr_status: str
    llm_vorschlag: dict | None
    confidence: float | None
    status: str
    quelle: str
    plausi_warnung: str | None
    created_at: datetime


class BelegUrlResponse(BaseModel):
    beleg_id: uuid.UUID
    signed_url: str
