# Architektur

## Datenfluss

```
 ┌────────────┐   Beleg-Foto    ┌──────────────────────┐
 │  WhatsApp  │ ──────────────▶ │  POST /webhooks/      │
 │  (Partner) │                 │       whatsapp        │
 └────────────┘                 └──────────┬───────────┘
        ▲                                   │
        │ Bestätigungsfrage (Buttons)       ▼
        │                        ┌──────────────────────┐
        │                        │ ingest_beleg          │  write-once + SHA-256
        │                        │  (immutability)       │ ───────────────▶ ┌─────────┐
        │                        └──────────┬───────────┘                   │  MinIO  │
        │                                   │                               │  / S3   │
        │                                   ▼                               └─────────┘
        │                        ┌──────────────────────┐
        │                        │ run_ocr (OCR-Provider)│
        │                        └──────────┬───────────┘
        │                                   ▼
        │                        ┌──────────────────────┐
        │                        │ klassifiziere_beleg   │  NUR Vorschlag,
        │                        │  (LLM-Provider)       │  ggf. Rückfrage
        │                        └──────────┬───────────┘
        │                                   │
        │            ░░░ CONFIRM-GATE ░░░    │  (kein automatischer Übergang)
        │                                   ▼
        │   Mensch bestätigt    ┌──────────────────────┐
        └────────────────────── │ POST /buchungen  ODER │
                                │ /webhooks/whatsapp/   │
                                │        confirm        │
                                └──────────┬───────────┘
                                           ▼
                                ┌──────────────────────┐     ┌──────────────┐
                                │ bestaetige_und_buche  │ ──▶ │  PostgreSQL  │
                                │  (EINZIGER Buchungs-  │     │  buchung     │
                                │   pfad) + append_audit│ ──▶ │  audit_log   │ (append-only)
                                └──────────┬───────────┘     └──────────────┘
                                           │
                         ┌─────────────────┴─────────────────┐
                         ▼                                     ▼
              ┌────────────────────┐               ┌────────────────────┐
              │ GET /euer          │               │ POST /exports      │
              │  berechne_euer     │               │  DATEV-CSV / PDF    │
              │ (ohne Stornos)     │               │  write-once + Audit │
              └────────────────────┘               └────────────────────┘
```

## Komponenten

### API (FastAPI, async)
- `api/belege.py` — Ingest (write-once), Klassifikation (Vorschlag), Signed-URL.
- `api/buchungen.py` — Buchung anlegen (nur nach Bestätigung), PATCH = Storno + Neuanlage.
- `api/webhooks.py` — WhatsApp-Inbound (nie Buchung) und Confirm (einziger Buchungspfad).
- `api/euer.py` — EÜR-Vorschau.
- `api/exports.py` — DATEV-/PDF-Export.
- `api/audit.py` — read-only Audit-Abfrage.

### Services (Geschäftslogik)
- `immutability.py` — write-once Object Storage + SHA-256 (pluggable: S3 / In-Memory).
- `audit.py` — append-only Audit-Log (nur INSERT).
- `classification.py` — Ingest, OCR-Orchestrierung, LLM-Klassifikation, Plausi-Prüfung. **Bucht nie.**
- `confirmation.py` — `bestaetige_und_buche` (einziger Buchungspfad), `storniere_und_neu` (kein Overwrite).
- `euer.py` — Aggregation aus nicht-stornierten Buchungen.
- `export.py` — DATEV-CSV + PDF, write-once Ablage + Audit.

### Provider (austauschbar per Env)
- `providers/whatsapp` — Meta Cloud API (live) / Stub.
- `providers/ocr` — OCR-Anbieter (live) / Stub.
- `providers/llm` — LLM-Anbieter EU-Region (live) / Stub.
- Auswahl über `WHATSAPP_PROVIDER`, `OCR_PROVIDER`, `LLM_PROVIDER` (`stub` | `live`).

### Persistenz
- **PostgreSQL 16** — einzige Source of Truth (`mandant`, `beleg`, `buchung`,
  `kategorie`, `kundenrechnung`, `zahlungserinnerung`, `rueckfrage`, `export`,
  `audit_log`).
- **MinIO / S3** — write-once Binär-Originale und Exporte.

### Orchestrierung
- **n8n** — Webhooks, OCR-/LLM-Trigger, Bestätigungs-/Rückfrage-Handler,
  Zahlungserinnerungen (cron), Monats-Export, Fehlerbehandlung
  (siehe `n8n/README.md`).

## Deployment / Hosting (EU/DE)
Alle Kernkomponenten sind selbst hostbar (z. B. Hetzner): PostgreSQL, MinIO bzw.
Hetzner Object Storage, API-Container, n8n. Der LLM-Anbieter wird EU-Region-fähig
konfiguriert. Secrets ausschließlich über Umgebungsvariablen.
