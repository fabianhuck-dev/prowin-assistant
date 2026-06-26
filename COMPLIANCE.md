# Compliance-Regeln

Diese Regeln sind nicht verhandelbar und haben höchste Priorität. Sie sind im
Code technisch verankert (siehe Verweise) und durch Tests abgesichert
(`backend/tests/test_compliance.py`, `test_beleg_flow.py`, `test_export.py`).

## 1. Der LLM bucht nie selbst
LLM-Ausgaben sind **immer nur Vorschläge**. Eine `buchung`-Zeile entsteht
ausschließlich nach **expliziter menschlicher Bestätigung** (WhatsApp-Button
oder Dashboard). Es gibt **keinen** Code-Pfad vom LLM-Ergebnis direkt zur
Buchung.
→ `services/classification.py` erzeugt nie eine Buchung;
`services/confirmation.py::bestaetige_und_buche` ist der **einzige** Buchungspfad.
Abgesichert durch **T10** (Confirm-Gate).

## 2. `beleg` und `buchung` sind strikt getrennt
Der Beleg ist die unveränderbare Eingangsdokumentation; die Buchung entsteht
erst durch Bestätigung. Es sind zwei getrennte Tabellen ohne automatischen
Übergang.
→ `db/models.py` (`Beleg`, `Buchung`).

## 3. GoBD-Unveränderbarkeit (write-once)
Original-Belege werden **write-once** im Object Storage abgelegt und per
**SHA-256** identifiziert. Es gibt **keinen** Update-/Delete-Pfad auf Originale.
→ `services/immutability.py` (`upload_beleg_write_once`, `WriteOnceError`).
Abgesichert durch die Write-once- und Hash-Tests.

## 4. `audit_log` ist append-only
Jede zustandsverändernde Aktion erzeugt eine **neue** Zeile. **Niemals** UPDATE
oder DELETE. Auf DB-Ebene zusätzlich per Trigger erzwungen (PostgreSQL) bzw. per
Trigger in den Tests (SQLite).
→ `services/audit.py` (nur INSERT), Migration `0001_initial` (PG-Trigger).
Kein ORM-Relationship führt cascade-delete auf `audit_log`.

## 5. PostgreSQL ist die einzige Source of Truth
Sämtliche verbindlichen Geschäftsdaten liegen in PostgreSQL. Object Storage hält
nur die unveränderlichen Binär-Originale/Exporte; n8n/Provider halten keinen
verbindlichen Zustand.

## 6. Keine Steuer-Rechtsauskunft
Das System bereitet Daten auf (EÜR-Vorschau, Exporte), erteilt aber **keine**
steuerliche Beratung. Ausgaben enthalten entsprechende Hinweise.
→ `schemas/euer.py` (`EuerVorschau.hinweis`).

## 7. Zahlen-Validierung im Code, nicht im Prompt
Betrags- und Datums-Plausibilität wird deterministisch im Code geprüft, nicht dem
LLM überlassen.
→ `services/classification.py::pruefe_plausibilitaet`,
`services/confirmation.py` (Pflichtfeld-/Positiv-Prüfung). Abgesichert durch **T7**.

## 8. Secrets niemals committen — EU-/DE-Hosting-fähig
Keine Secrets im Repository (`.env` ist in `.gitignore`, nur `.env.example`
eingecheckt). Die Architektur ist auf EU-/DE-Hosting ausgelegt (Postgres + S3
selbst gehostet, z. B. Hetzner; LLM-Anbieter EU-Region-fähig konfigurierbar).
