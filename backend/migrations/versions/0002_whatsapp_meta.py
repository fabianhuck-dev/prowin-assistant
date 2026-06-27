"""whatsapp meta: webhook_event idempotency table

Revision ID: 0002_whatsapp_meta
Revises: 0001_initial
Create Date: 2026-06-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_whatsapp_meta"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "webhook_event",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
        ),
        sa.Column("wamid", sa.String(length=256), nullable=False, unique=True),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_webhook_event_wamid", "webhook_event", ["wamid"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_webhook_event_wamid", table_name="webhook_event")
    op.drop_table("webhook_event")
