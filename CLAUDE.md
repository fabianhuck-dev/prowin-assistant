# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Befehle

```bash
# Infra starten (postgres, minio, api, n8n)
make up

# Schema anlegen + Default-Kategorien + Test-Mandant einspielen
make migrate && make seed

# Tests (laufen vollständig ohne Docker via SQLite/InMemoryStorage)
make test

# Einzelnen Test laufen
cd backend && uv run pytest tests/test_beleg_flow.py::test_t10_confirm_gate -v

# Lint + Format-Check
make lint

# Auto-Format
make format

# End-to-End Demo gegen laufende API
make demo
```

Alembic-Migrations von Repo-Root aus: `uv run alembic upgrade head` (nicht `cd backend` zuerst — `alembic.ini` liegt im Root, `script_location = backend/migrations`).

## Architektur

### Datenfluss (der wichtigste Invariant)

```
WhatsApp Inbound → ingest_beleg → run_ocr → klassifiziere_beleg → Vorschlag an Nutzer
                                                                         ↓
WhatsApp Confirm → bestaetige_und_buche → Buchung + Audit-Log
```

`/webhooks/whatsapp` (inbound) erzeugt **niemals** eine Buchung. Die einzige Funktion, die eine `buchung`-Zeile schreibt, ist `services/confirmation.py::bestaetige_und_buche`. Das ist Compliance Regel 1 — wenn du daran vorbei arbeitest, ist das ein kritischer Fehler.

### Schichten

- **`app/api/`** — FastAPI-Router, nur HTTP-Handling und Dependency Injection. Keine Geschäftslogik.
- **`app/services/`** — Geschäftslogik. `classification.py` (ingest/OCR/LLM-Vorschlag), `confirmation.py` (Buchung erzeugen), `immutability.py` (write-once Storage), `audit.py` (append-only Log), `euer.py` (Aggregation), `export.py` (DATEV-CSV/PDF).
- **`app/providers/`** — Externe Anbindungen hinter ABCs. Jeder Provider hat `base.py` (ABC) + `stub.py`. Wahl per `settings.whatsapp_provider / ocr_provider / llm_provider` (Wert: `stub` oder später `live`). Factory in `providers/__init__.py`.
- **`app/db/`** — SQLAlchemy 2.0 async. `Base` + `get_session()` in `base.py`, alle Modelle in `models.py`. Keine ORM-Relationships mit cascade auf `audit_log` oder Beleg-Originale.

### Datenmodell-Invarianten

- `beleg.sha256_hash` ist `UNIQUE` — natürliche Duplikat-Erkennung bei gleichem Inhalt (T6).
- `buchung.storniert` + `buchung.storno_von_id`: Korrekturen sind Storno+Neuanlage, niemals `UPDATE` auf Geschäftsdaten (T9).
- `audit_log` hat DB-Trigger (PostgreSQL: in der Migration; SQLite: in `conftest.py`), die `UPDATE`/`DELETE` ablehnen.

### Tests ohne Docker

Die Test-Suite braucht keine laufenden Services:
- DB: `sqlite+aiosqlite:///:memory:` mit `StaticPool` (Fixture `db_engine` in `conftest.py`)
- Storage: `InMemoryStorage` (Fixture `memory_storage`, `autouse=True`)
- FastAPI-Client: `httpx.AsyncClient(transport=ASGITransport(app=app))`

### OCR-Stub triggern

Der `StubOcrProvider` liest einen Marker aus den Bilddaten: `STUB-IMAGE:<kind>`. Gültige Werte: `tankquittung`, `wareneinkauf`, `provision`, `unbekannt`, `zukunft`, `kaputt`. Beispiel in Tests: `data = b"STUB-IMAGE:tankquittung"`.

### Neue Provider implementieren

1. Datei anlegen unter `providers/<typ>/live.py`, ABC aus `base.py` implementieren.
2. In `providers/__init__.py` den neuen Wert in der Factory-Funktion ergänzen.
3. Env-Variable auf den neuen Wert setzen (z.B. `OCR_PROVIDER=live`).

## Compliance-Regeln (Kurzfassung)

Volltext in `COMPLIANCE.md`. Die wichtigsten für den Code-Alltag:

- **Kein Auto-Buchen**: LLM-Output → Vorschlag. Buchung → nur via `bestaetige_und_buche()`.
- **GoBD-Unveränderbarkeit**: `upload_beleg_write_once()` — nur einmal schreiben, nie überschreiben. Buchungskorrekturen: `storniere_und_neu()`.
- **Audit-Log**: Nur `services/audit.py::append_audit()` verwenden — niemals direkt in `audit_log` schreiben oder von außen `UPDATE`/`DELETE`.
- **Zahlen-Validierung im Code**: Datum-/Betrag-Plausibilitätsprüfung in `classification.py::pruefe_plausibilitaet()`, nicht im LLM-Prompt.
- **Keine echten Secrets committen**: `.env.example` zeigt nur Platzhalter, `.env` ist gitignored.

## Bekannte Einschränkung

Das `backend/Dockerfile` hat `COPY pyproject.toml .` mit Build-Context `./backend`, aber `pyproject.toml` liegt im Repo-Root. Vor `docker compose build` muss der Dockerfile-Pfad oder der Build-Context angepasst werden.
