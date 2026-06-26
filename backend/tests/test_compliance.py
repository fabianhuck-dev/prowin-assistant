"""Compliance-Kern-Tests (Phase 2).

Deckt die nicht-verhandelbaren Regeln ab:
- Write-once Storage (GoBD-Unveränderbarkeit)
- SHA-256-Hash-Verifikation
- Append-only audit_log (UPDATE/DELETE verboten)
- Duplikat-Erkennung über identischen Hash (T6-Grundlage)
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from app.services import immutability
from app.services.audit import append_audit
from app.services.immutability import (
    WriteOnceError,
    compute_sha256,
    upload_beleg_write_once,
)
from sqlalchemy import text


async def test_compute_sha256_matches_hashlib():
    data = b"ein beleg inhalt"
    assert compute_sha256(data) == hashlib.sha256(data).hexdigest()
    assert len(compute_sha256(data)) == 64


async def test_write_once_raises_on_overwrite():
    store = immutability.get_storage()
    await store.put_write_once("belege/x/abc.jpg", b"a", "image/jpeg")
    with pytest.raises(WriteOnceError):
        await store.put_write_once("belege/x/abc.jpg", b"anders", "image/jpeg")


async def test_upload_beleg_is_idempotent_same_hash():
    data = b"tankquittung-bytes"
    key1, hash1 = await upload_beleg_write_once(data, "beleg.jpg")
    # Zweites Hochladen desselben Inhalts: gleicher Key, gleicher Hash, kein Fehler.
    key2, hash2 = await upload_beleg_write_once(data, "beleg.jpg")
    assert key1 == key2
    assert hash1 == hash2 == compute_sha256(data)


async def test_storage_get_returns_stored_bytes():
    data = b"original-beleg"
    key, _ = await upload_beleg_write_once(data, "r.pdf")
    assert await immutability.get_storage().get(key) == data


async def test_duplicate_detected_via_hash(session, mandant):
    """T6-Grundlage: gleicher Inhalt -> gleicher Hash -> nur EINE beleg-Zeile möglich."""
    from app.db.models import Beleg

    data = b"identischer-beleg"
    key, sha = await upload_beleg_write_once(data, "a.jpg")
    session.add(Beleg(mandant_id=mandant.id, storage_key=key, sha256_hash=sha))
    await session.commit()

    # Zweiter Versuch mit identischem Hash verletzt die Unique-Constraint.
    key2, sha2 = await upload_beleg_write_once(data, "a.jpg")
    assert sha2 == sha
    session.add(Beleg(mandant_id=mandant.id, storage_key=key2, sha256_hash=sha2))
    with pytest.raises(Exception):
        await session.commit()
    await session.rollback()


async def test_audit_log_is_append_only(session, mandant):
    entry = await append_audit(
        session,
        mandant_id=mandant.id,
        entity_type="beleg",
        entity_id=uuid.uuid4(),
        action="created",
        actor="system",
        payload={"k": "v"},
    )
    await session.commit()

    # UPDATE muss durch DB-Trigger scheitern.
    with pytest.raises(Exception):
        await session.execute(
            text("UPDATE audit_log SET action = 'tampered' WHERE id = :i"), {"i": entry.id}
        )
        await session.commit()
    await session.rollback()

    # DELETE muss ebenfalls scheitern.
    with pytest.raises(Exception):
        await session.execute(text("DELETE FROM audit_log WHERE id = :i"), {"i": entry.id})
        await session.commit()
    await session.rollback()


async def test_append_audit_only_inserts(session, mandant):
    for i in range(3):
        await append_audit(
            session,
            mandant_id=mandant.id,
            entity_type="buchung",
            entity_id=uuid.uuid4(),
            action=f"action_{i}",
            actor="user",
        )
    await session.commit()
    rows = (await session.execute(text("SELECT COUNT(*) FROM audit_log"))).scalar_one()
    assert rows == 3
