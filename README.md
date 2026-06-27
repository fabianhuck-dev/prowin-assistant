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

## Echten WhatsApp-Provider aktivieren (Meta Cloud API)

### Voraussetzungen (im Meta Developer Dashboard — kein Code)
1. Meta-App + WhatsApp-Produkt anlegen auf `developers.facebook.com`.
2. Test-Telefonnummer und bis zu 5 Empfängernummern hinterlegen (für Testbetrieb ohne Business-Verifizierung).
3. **Permanenten System-User-Token** anlegen: Business Settings → System Users → Token mit `whatsapp_business_messaging` + `whatsapp_business_management`.
4. `PHONE_NUMBER_ID` und App-Secret aus dem Dashboard kopieren.

### Lokales Testen mit Tunnel

```bash
# Tunnel starten (öffentliche HTTPS-URL für Meta erforderlich)
ngrok http 8000
# oder: cloudflared tunnel --url http://localhost:8000

# Tunnel-URL als Webhook im Meta Dashboard eintragen
# (Dashboard → WhatsApp → Configuration → Webhook-URL: https://<tunnel>/webhooks/whatsapp)
# Webhook-Feld "messages" subscriben.
```

### Provider umschalten

```bash
# .env anpassen:
WHATSAPP_PROVIDER=meta
WHATSAPP_VERIFY_TOKEN=<frei wählbar, identisch im Meta-Dashboard>
WHATSAPP_APP_SECRET=<aus Meta App-Einstellungen>
WHATSAPP_ACCESS_TOKEN=<permanenter System-User-Token>
WHATSAPP_PHONE_NUMBER_ID=<aus dem WhatsApp-Setup>
WHATSAPP_GRAPH_VERSION=v21.0

# Stub (Tests + lokale Entwicklung ohne Meta-Account):
WHATSAPP_PROVIDER=stub
```

Tests laufen immer mit `WHATSAPP_PROVIDER=stub` (kein echter Netzwerk-Call).
Mit `WHATSAPP_PROVIDER=meta` + Tunnel können echte Belege vom Handy den
vollständigen Pfad durchlaufen: Foto → Storage → OCR → LLM-Vorschlag
→ ✅-Button → Buchung → EÜR-Update im Chat.

## Intelligenter Frage-Agent (Schritt 4)

Neben dem Beleg-Flow kann Beli auf WhatsApp auch **Freitext-Fragen** zu den
eigenen Buchhaltungsdaten beantworten.

### Was Beli beantwortet

| Frage (Beispiele) | Tool |
|---|---|
| „Was ist mein Gewinn dieses Jahr?" | `get_euer` |
| „Ausgaben Oktober 2026" | `query_finanzen` |
| „Vergleich Oktober mit September" | `query_finanzen` (vergleich) |
| „Teuerste Ausgaben dieses Jahr" | `query_finanzen` (liste) |
| „Ausgaben pro Kategorie" | `query_finanzen` (pro_kategorie) |
| „Monatsverlauf meiner Einnahmen" | `query_finanzen` (pro_monat) |
| „Fahrtkosten letzten Monat" | `query_finanzen` + Kategorie |
| „Alle Belege über 50 €" | `query_finanzen` + betrag_min |
| „Wer hat noch nicht bezahlt?" | `get_offene_rechnungen` |
| „Export für meinen Steuerberater" | `create_export` |

### Was Beli bewusst NICHT tut

- **Keine Steuer-/Rechtsberatung** — Fragen wie „Kann ich X absetzen?", „Wie
  viel Steuer zahle ich?" oder „Lohnt sich ProWin?" beantwortet Beli nicht
  inhaltlich. Sie aktivieren automatisch `steuerberatung_grenze` und verweisen
  auf den Steuerberater.
- **Keine Geschäfts-/Finanzempfehlungen** — „Soll ich mehr investieren?" → Guard.
- **Kein Weltwissen** — Beli kennt nur die eigenen Daten der Mandantin.
- **Keine Buchungen** — der Frage-Agent liest ausschließlich, ändert nie Buchungen.

### Technisches Prinzip (ADR-007/008/009)

```
Freitext → Mistral Function Calling → Tool-Auswahl (LLM)
                                              ↓
                              Code führt DB-Query aus (mandant_id aus Kontext)
                                              ↓
                              Code baut Antwort aus DB-Zahlen (Template)
                                              ↓
                                         WhatsApp-Antwort
```

**Zahlen kommen IMMER aus Code, nie aus dem LLM.** Kein Framework
(LangChain/LangGraph) — natives Mistral Function Calling + einfaches Python-Dict
als Router. Details: [docs/decisions.md](docs/decisions.md) ADR-007/008/009.

### Konfiguration

```bash
# .env für den Live-Betrieb:
LLM_PROVIDER=live          # aktiviert Mistral-API
MISTRAL_API_KEY=<key>
# Optional: stärkeres Modell nur für den Frage-Agenten
INTENT_MODEL=mistral-medium-latest   # leer = LLM_MODEL
```

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
