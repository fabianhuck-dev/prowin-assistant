from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class ExportRequest(BaseModel):
    mandant_id: uuid.UUID
    von: date
    bis: date
    format: str = "datev_csv"  # "datev_csv" | "pdf"


class ExportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mandant_id: uuid.UUID
    format: str
    von: date
    bis: date
    storage_key: str
    sha256_hash: str
    anzahl_buchungen: int
    created_at: datetime


class ExportWithUrl(ExportOut):
    signed_url: str
