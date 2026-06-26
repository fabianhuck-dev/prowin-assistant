# Frontend (Platzhalter)

Das Dashboard wird später als **React + Vite** Single-Page-App umgesetzt.

## Geplanter Umfang
- Beleg-Posteingang mit Vorschlägen (Belegtyp, Kategorie, Confidence, Plausi-Warnung).
- **Bestätigen / Korrigieren / Verwerfen** je Beleg — die Bestätigung ist der
  einzige Weg zur Buchung (Confirm-Gate, siehe COMPLIANCE.md).
- Buchungsliste inkl. Storno-Historie (kein Overwrite).
- EÜR-Vorschau und Export-Download (DATEV-CSV / PDF).
- Audit-Log-Ansicht (read-only).

## Technik (geplant)
- React 18 + Vite + TypeScript
- Anbindung an die FastAPI-API (`http://localhost:8000`)
- Auth gegen das Backend

> Noch nicht implementiert. Build-Artefakte (`frontend/dist/`) sind in `.gitignore`.
