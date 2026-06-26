# Verfahrensdokumentation (Vorlage)

> Vorlage gemäß GoBD. Bitte je Mandant ausfüllen/anpassen. Diese Vorlage ersetzt
> keine steuerliche Beratung (siehe COMPLIANCE.md, Regel 6).

## 1. Allgemeines
- **Unternehmen / Mandant:** _________________________
- **Verantwortliche Person:** _________________________
- **Gültig ab:** ____________  **Version:** ____________

## 2. Überblick über das Verfahren
Belege werden per WhatsApp eingereicht, automatisch erfasst (OCR) und
vorklassifiziert (LLM). Die verbindliche Verbuchung erfolgt **erst nach
manueller Bestätigung** durch den Mandanten. Die Daten dienen der Vorbereitung
der Einnahmen-Überschuss-Rechnung (EÜR) und werden als DATEV-/PDF-Export an die
steuerliche Beratung übergeben.

## 3. Belegeingang und -identifikation
- **Eingangskanal:** WhatsApp (Foto/Dokument).
- **Erfassungszeitpunkt:** automatisch bei Eingang (`beleg.created_at`).
- **Eindeutige Identifikation:** UUID je Beleg + SHA-256-Hash des Originals.
- **Duplikate:** Werden über den Hash erkannt; es wird keine zweite Belegzeile
  angelegt.

## 4. Unveränderbarkeit (GoBD)
- Originale werden **write-once** im Object Storage abgelegt; kein Update/Delete.
- Integrität durch SHA-256 nachweisbar.
- Sämtliche zustandsverändernden Aktionen werden **append-only** im `audit_log`
  protokolliert (Zeitpunkt, Akteur, Aktion, Nutzdaten).

## 5. Verarbeitung / Klassifikation
- OCR-Extraktion: Betrag, Datum, Händler, Rohtext.
- LLM-Klassifikation: Belegtyp und Kategorie-**Vorschlag** inkl. Confidence.
- Plausibilitätsprüfungen (z. B. Zukunftsdatum, nicht-positiver Betrag) erfolgen
  deterministisch im Code.

## 6. Freigabe / Verbuchung (4-Augen-/Bestätigungsprinzip)
- Eine Buchung entsteht ausschließlich nach **expliziter Bestätigung** durch den
  Mandanten (WhatsApp-Button oder Dashboard).
- Korrekturen erfolgen ohne Overwrite: Storno der alten Buchung + Neuanlage,
  jeweils mit Audit-Eintrag.

## 7. Aufbewahrung
- **Aufbewahrungsfrist:** ____ Jahre (gemäß gesetzlicher Vorgaben).
- **Speicherort:** PostgreSQL (Geschäftsdaten) + Object Storage (Originale/Exporte).
- **Hosting-Region:** EU/DE.

## 8. Berechtigungen und Zugriff
- **Zugriffsberechtigte:** _________________________
- **Authentifizierung:** _________________________
- **Protokollierung:** `audit_log` (append-only).

## 9. Notfall / Wiederanlauf
- **Backups:** _________________________
- **Wiederherstellung:** _________________________

## 10. Änderungshistorie
| Datum | Version | Änderung | Autor |
|-------|---------|----------|-------|
|       |         |          |       |
