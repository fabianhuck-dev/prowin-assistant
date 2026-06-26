"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-27

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mandant",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("whatsapp_phone", sa.String(length=32), nullable=False, unique=True),
        sa.Column("is_kleinunternehmer", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("steuernummer", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "kategorie",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("typ", sa.String(length=32), nullable=False),
        sa.Column("is_system_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mandant_id", sa.Uuid(), sa.ForeignKey("mandant.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "beleg",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("mandant_id", sa.Uuid(), sa.ForeignKey("mandant.id"), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("sha256_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("original_filename", sa.String(length=512), nullable=True),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("betrag", sa.Numeric(12, 2), nullable=True),
        sa.Column("datum", sa.Date(), nullable=True),
        sa.Column("haendler", sa.String(length=255), nullable=True),
        sa.Column("belegtyp", sa.String(length=32), nullable=True),
        sa.Column("ocr_status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("ocr_raw", sa.JSON(), nullable=True),
        sa.Column("llm_vorschlag", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="eingegangen"),
        sa.Column("quelle", sa.String(length=16), nullable=False, server_default="whatsapp"),
        sa.Column("plausi_warnung", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_beleg_sha256_hash", "beleg", ["sha256_hash"], unique=True)

    op.create_table(
        "buchung",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("mandant_id", sa.Uuid(), sa.ForeignKey("mandant.id"), nullable=False),
        sa.Column("beleg_id", sa.Uuid(), sa.ForeignKey("beleg.id"), nullable=True),
        sa.Column("kategorie_id", sa.Uuid(), sa.ForeignKey("kategorie.id"), nullable=True),
        sa.Column("typ", sa.String(length=32), nullable=False),
        sa.Column("betrag", sa.Numeric(12, 2), nullable=False),
        sa.Column("datum", sa.Date(), nullable=False),
        sa.Column("haendler", sa.String(length=255), nullable=True),
        sa.Column("buchungstext", sa.Text(), nullable=True),
        sa.Column("bestaetigt_via", sa.String(length=32), nullable=True),
        sa.Column("bestaetigt_am", sa.DateTime(timezone=True), nullable=True),
        sa.Column("storniert", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("storno_von_id", sa.Uuid(), sa.ForeignKey("buchung.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "kundenrechnung",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("mandant_id", sa.Uuid(), sa.ForeignKey("mandant.id"), nullable=False),
        sa.Column("rechnungsnummer", sa.String(length=64), nullable=False),
        sa.Column("kunde_name", sa.String(length=255), nullable=False),
        sa.Column("betrag", sa.Numeric(12, 2), nullable=False),
        sa.Column("datum", sa.Date(), nullable=False),
        sa.Column("faellig_am", sa.Date(), nullable=True),
        sa.Column("bezahlt", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("bezahlt_am", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="offen"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "zahlungserinnerung",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kundenrechnung_id", sa.Uuid(), sa.ForeignKey("kundenrechnung.id"), nullable=False),
        sa.Column("stufe", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("versendet_am", sa.DateTime(timezone=True), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "rueckfrage",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("mandant_id", sa.Uuid(), sa.ForeignKey("mandant.id"), nullable=False),
        sa.Column("beleg_id", sa.Uuid(), sa.ForeignKey("beleg.id"), nullable=True),
        sa.Column("frage_text", sa.Text(), nullable=False),
        sa.Column("feld", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="offen"),
        sa.Column("antwort", sa.Text(), nullable=True),
        sa.Column("beantwortet_am", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "export",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("mandant_id", sa.Uuid(), sa.ForeignKey("mandant.id"), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("von", sa.Date(), nullable=False),
        sa.Column("bis", sa.Date(), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("sha256_hash", sa.String(length=64), nullable=False),
        sa.Column("anzahl_buchungen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # audit_log: append-only, bigserial PK
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("mandant_id", sa.Uuid(), nullable=True),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Regel 4: audit_log ist append-only. UPDATE/DELETE werden auf DB-Ebene verboten.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'audit_log is append-only (no UPDATE/DELETE allowed)';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            "CREATE TRIGGER audit_log_no_modify BEFORE UPDATE OR DELETE ON audit_log "
            "FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_log_no_modify ON audit_log;")
        op.execute("DROP FUNCTION IF EXISTS audit_log_immutable();")
    op.drop_table("audit_log")
    op.drop_table("export")
    op.drop_table("rueckfrage")
    op.drop_table("zahlungserinnerung")
    op.drop_table("kundenrechnung")
    op.drop_table("buchung")
    op.drop_index("ix_beleg_sha256_hash", table_name="beleg")
    op.drop_table("beleg")
    op.drop_table("kategorie")
    op.drop_table("mandant")
