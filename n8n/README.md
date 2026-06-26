# n8n-Workflows

n8n orchestriert die ereignisgesteuerten Abläufe rund um die FastAPI-Backend-API.
PostgreSQL bleibt die einzige Source of Truth — n8n hält **keinen** verbindlichen
Zustand. Die folgenden 8 Workflows sind hier **dokumentiert**, nicht implementiert.

## 1. Inbound-Beleg-Webhook
- **Trigger:** WhatsApp Meta Cloud API → n8n Webhook.
- **Aktion:** Normalisiert die eingehende Nachricht und ruft
  `POST /webhooks/whatsapp` auf.
- **Wichtig:** Erzeugt **nie** eine Buchung.

## 2. OCR-Trigger
- **Trigger:** Neuer Beleg (Status `eingegangen`).
- **Aktion:** Stößt die OCR-Extraktion an (im MVP Teil von `/webhooks/whatsapp`;
  als separater Schritt für Retry/Skalierung auslagerbar).
- **Fehler:** `ocr_status=failed` → Workflow 8.

## 3. LLM-Klassifikation
- **Trigger:** OCR erfolgreich.
- **Aktion:** Ruft die Klassifikation auf und erhält einen **Vorschlag**
  (Belegtyp, Kategorie, Confidence). Bucht nicht.

## 4. Bestätigungs-Button-Handler
- **Trigger:** WhatsApp-Button-Antwort „Ja, buchen".
- **Aktion:** Ruft `POST /webhooks/whatsapp/confirm` auf — der **einzige**
  Buchungspfad. Sendet anschließend die Buchungsbestätigung.

## 5. Rückfrage-Handler
- **Trigger:** Offene `rueckfrage` (z. B. niedrige Confidence / fehlende Felder).
- **Aktion:** Stellt die Frage per WhatsApp, nimmt die Antwort entgegen und
  aktualisiert den Vorschlag/Beleg. Führt erneut zur Bestätigungsfrage.

## 6. Zahlungserinnerung (cron)
- **Trigger:** Zeitplan (z. B. täglich).
- **Aktion:** Sucht fällige, unbezahlte `kundenrechnung`-Einträge und versendet
  gestufte Zahlungserinnerungen; protokolliert in `zahlungserinnerung`.

## 7. Monats-Export-Trigger
- **Trigger:** Monatswechsel (cron) oder manuelle Auslösung.
- **Aktion:** Ruft `POST /exports` (DATEV-CSV und/oder PDF) für den Vormonat auf
  und stellt das Ergebnis (Signed-URL) bereit.

## 8. Fehlerbehandlung
- **Trigger:** Fehler in den Workflows 1–7.
- **Aktion:** Zentrales Logging/Alerting, Retry mit Backoff, ggf. Benachrichtigung
  des Mandanten/Betreibers. Keine stillen Datenverluste.

## Hinweise
- Basis-Auth ist in `docker-compose.yml` aktiviert (Demo-Zugang).
- Secrets/Tokens werden ausschließlich über n8n-Credentials bzw.
  Umgebungsvariablen verwaltet — niemals im Repo.
