# ProWin Buchhaltungs-Assistent

WhatsApp-zentrierter Beleg- & EÜR-Vorbereitungs-Assistent für ProWin-Vertriebspartner.

Vertriebspartner senden Belege per WhatsApp. Das System extrahiert (OCR) und
klassifiziert (LLM) die Daten und macht einen **Vorschlag**. Eine **Buchung
entsteht ausschließlich nach expliziter menschlicher Bestätigung** — der LLM
bucht nie selbst. Anschließend lassen sich EÜR-Vorschau und DATEV-/PDF-Exporte
für den Steuerberater erzeugen.

> Wichtig: Dieses System leistet **keine** steuerliche Rechtsauskunft. Siehe
> [COMPLIANCE.md](COMPLIANCE.md).

## Voraussetzungen

- [Docker](https://www.docker.com/) + Docker Compose
- [uv](https://github.com/astral-sh/uv) (Python-Dependency-Management)
- Python 3.12

## Setup

```bash
# 1. Abhängigkeiten installieren (lokal, für Tests/Tooling)
uv venv --python 3.12
uv pip install -e ".[dev]"

# 2. Infrastruktur starten (Postgres, MinIO, API, n8n)
docker compose up -d          # = make up

# 3. Datenbank-Migration
make migrate                  # alembic upgrade head

# 4. Stammdaten anlegen (Default-Kategorien + Test-Mandant)
make seed                     # gibt die Mandant-UUID aus
```

Die API ist anschließend unter http://localhost:8000 erreichbar
(Swagger-UI: http://localhost:8000/docs). MinIO-Console: http://localhost:9001.

## Demo-Durchlauf

```bash
# Mandant-UUID aus `make seed` übernehmen:
DEMO_MANDANT_ID=<uuid> make demo
```

Der Demo-Durchlauf zeigt: Ingest -> Klassifikation (Vorschlag) ->
Bestätigung (Buchung) -> EÜR-Vorschau -> DATEV-Export.

## Tests

```bash
make test     # läuft vollständig OHNE Docker (Async-SQLite + In-Memory-Storage)
make lint     # ruff check + format --check
make format   # ruff format + --fix
```

Abgedeckt sind die Fälle **T1–T10** inkl. des zentralen **Confirm-Gates**
(T10: keine Buchung ohne Bestätigung) sowie die Compliance-Invarianten
(write-once Storage, append-only Audit-Log).

## Architektur-Überblick

```
WhatsApp ─▶ Webhook ─▶ Ingest (write-once) ─▶ OCR ─▶ LLM-Klassifikation
                                                        │ (nur Vorschlag)
                                                        ▼
                                            Bestätigung durch Mensch
                                                        │ (EINZIGER Buchungspfad)
                                                        ▼
                                                   buchung + audit_log
                                                        │
                                          EÜR-Vorschau · DATEV-/PDF-Export
```

- **FastAPI (async)** + **PostgreSQL 16** (SQLAlchemy 2.0 / Alembic)
- **MinIO / S3** für write-once Original-Belege (SHA-256)
- **Provider-Stubs** für WhatsApp / OCR / LLM (per Env austauschbar)
- **n8n** für Orchestrierung (siehe [n8n/README.md](n8n/README.md))

Details: [docs/architecture.md](docs/architecture.md) ·
Entscheidungen: [docs/decisions.md](docs/decisions.md) ·
Compliance: [COMPLIANCE.md](COMPLIANCE.md)

## Projektstruktur

```
backend/app/
  api/         FastAPI-Router (belege, buchungen, webhooks, euer, exports, audit)
  services/    Geschäftslogik (immutability, audit, classification, confirmation, euer, export)
  providers/   WhatsApp / OCR / LLM (base + stub, Factory)
  db/          SQLAlchemy-Basis & Modelle
  schemas/     Pydantic-Modelle
backend/migrations/   Alembic
backend/tests/        T1–T10 + Compliance
scripts/              seed.py, demo.py
```
