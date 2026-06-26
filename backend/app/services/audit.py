"""Append-only Audit-Log.

Dieser Service ist der EINZIGE zulässige Schreibpfad auf ``audit_log`` und führt
ausschließlich INSERTs aus. Niemals UPDATE oder DELETE. Jede zustandsverändernde
Aktion im System erzeugt hier genau eine neue Zeile.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


async def append_audit(
    session: AsyncSession,
    *,
    mandant_id: uuid.UUID | None,
    entity_type: str,
    entity_id: uuid.UUID | None,
    action: str,
    actor: str,
    payload: dict[str, Any] | None = None,
) -> AuditLog:
    """Hängt einen Audit-Eintrag an (nur INSERT).

    Wird NICHT committet — der Aufrufer entscheidet über die Transaktionsgrenze,
    damit Geschäftsdaten und Audit-Eintrag atomar zusammen geschrieben werden.
    """
    entry = AuditLog(
        mandant_id=mandant_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor=actor,
        payload=payload,
    )
    session.add(entry)
    await session.flush()
    return entry
