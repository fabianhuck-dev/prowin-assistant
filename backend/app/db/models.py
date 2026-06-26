import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Mandant(Base):
    __tablename__ = "mandant"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    whatsapp_phone: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    is_kleinunternehmer: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    steuernummer: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Kategorie(Base):
    __tablename__ = "kategorie"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # typ: "ausgabe" | "einnahme"
    typ: Mapped[str] = mapped_column(String(32), nullable=False)
    is_system_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mandant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("mandant.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Beleg(Base):
    """Original-Beleg. GoBD-unveränderbar (write-once). Wird nie inhaltlich überschrieben."""

    __tablename__ = "beleg"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    mandant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("mandant.id"), nullable=False
    )
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Extrahierte Felder (Vorschlagsdaten, keine Buchung!)
    betrag: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    datum: Mapped[date | None] = mapped_column(Date, nullable=True)
    haendler: Mapped[str | None] = mapped_column(String(255), nullable=True)
    belegtyp: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # ocr_status: "pending" | "done" | "failed"
    ocr_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    ocr_raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    llm_vorschlag: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)

    # status: "eingegangen" | "klassifiziert" | "bestaetigt" | "verworfen" | "dupliziert"
    status: Mapped[str] = mapped_column(String(16), default="eingegangen", nullable=False)
    quelle: Mapped[str] = mapped_column(String(16), default="whatsapp", nullable=False)
    plausi_warnung: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Buchung(Base):
    """Entsteht AUSSCHLIESSLICH nach expliziter menschlicher Bestätigung (confirmation.py)."""

    __tablename__ = "buchung"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    mandant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("mandant.id"), nullable=False
    )
    beleg_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("beleg.id"), nullable=True
    )
    kategorie_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("kategorie.id"), nullable=True
    )

    # typ: "ausgabe" | "einnahme"
    typ: Mapped[str] = mapped_column(String(32), nullable=False)
    betrag: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    datum: Mapped[date] = mapped_column(Date, nullable=False)
    haendler: Mapped[str | None] = mapped_column(String(255), nullable=True)
    buchungstext: Mapped[str | None] = mapped_column(Text, nullable=True)

    bestaetigt_via: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bestaetigt_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    storniert: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    storno_von_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("buchung.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Kundenrechnung(Base):
    __tablename__ = "kundenrechnung"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    mandant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("mandant.id"), nullable=False
    )
    rechnungsnummer: Mapped[str] = mapped_column(String(64), nullable=False)
    kunde_name: Mapped[str] = mapped_column(String(255), nullable=False)
    betrag: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    datum: Mapped[date] = mapped_column(Date, nullable=False)
    faellig_am: Mapped[date | None] = mapped_column(Date, nullable=True)
    bezahlt: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    bezahlt_am: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="offen", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Zahlungserinnerung(Base):
    __tablename__ = "zahlungserinnerung"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kundenrechnung_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("kundenrechnung.id"), nullable=False
    )
    stufe: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    versendet_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Rueckfrage(Base):
    __tablename__ = "rueckfrage"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    mandant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("mandant.id"), nullable=False
    )
    beleg_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("beleg.id"), nullable=True
    )
    frage_text: Mapped[str] = mapped_column(Text, nullable=False)
    feld: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # status: "offen" | "beantwortet"
    status: Mapped[str] = mapped_column(String(16), default="offen", nullable=False)
    antwort: Mapped[str | None] = mapped_column(Text, nullable=True)
    beantwortet_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Export(Base):
    __tablename__ = "export"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    mandant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("mandant.id"), nullable=False
    )
    # format: "datev_csv" | "pdf"
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    von: Mapped[date] = mapped_column(Date, nullable=False)
    bis: Mapped[date] = mapped_column(Date, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    anzahl_buchungen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditLog(Base):
    """Append-only. Wird von der Applikation AUSSCHLIESSLICH per INSERT geschrieben.
    Niemals UPDATE oder DELETE. Kein cascade-delete-Relationship zeigt hierauf."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mandant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# Bewusst KEINE ORM-Relationships mit cascade auf audit_log oder beleg-Originale,
# um versehentliche Löschungen/Updates (GoBD) auszuschließen.
__all__ = [
    "Mandant",
    "Kategorie",
    "Beleg",
    "Buchung",
    "Kundenrechnung",
    "Zahlungserinnerung",
    "Rueckfrage",
    "Export",
    "AuditLog",
]
