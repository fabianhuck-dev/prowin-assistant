# Architektur-Entscheidungen (ADR-Kurzform)

## ADR-001: uv statt Poetry
- **Kontext:** Dependency- und Environment-Management für ein Python-3.12-Backend.
- **Entscheidung:** `uv`.
- **Begründung:** Deutlich schnellere Auflösung/Installation, integriertes
  Venv-Handling, einfache `pyproject.toml`-Nutzung ohne separates Lock-Tooling,
  gute CI- und Docker-Eignung. Poetry brachte hier keinen Mehrwert bei höherem
  Overhead.

## ADR-002: Synchrone FastAPI-BackgroundTasks statt Celery (MVP)
- **Kontext:** OCR-/LLM-Verarbeitung nach Beleg-Eingang.
- **Entscheidung:** Verarbeitung inline bzw. über FastAPI-`BackgroundTasks`,
  Orchestrierung längerfristig über n8n. Kein Celery/Broker im MVP.
- **Begründung:** Geringere Betriebskomplexität (kein Redis/RabbitMQ, keine
  Worker-Flotte). Die Last ist niedrig und gut parallelisierbar; n8n übernimmt
  Retry/Scheduling. Ein Wechsel zu Celery ist später lokal in den Services
  kapselbar, ohne die Compliance-Pfade zu berühren.

## ADR-003: aioboto3 statt boto3
- **Kontext:** Zugriff auf S3-kompatiblen Object Storage in einer durchgehend
  asynchronen FastAPI-App.
- **Entscheidung:** `aioboto3`.
- **Begründung:** Non-blocking I/O im async Eventloop; kein Thread-Pool-Workaround
  für synchrones boto3 nötig. `boto3` bleibt als transitive/Tooling-Abhängigkeit
  verfügbar.

## ADR-004: Write-once über hash-basierten Storage-Key
- **Kontext:** GoBD-Unveränderbarkeit und Duplikat-Erkennung.
- **Entscheidung:** Storage-Key wird aus dem SHA-256-Hash des Inhalts abgeleitet.
- **Begründung:** Identischer Inhalt ⇒ identischer Key ⇒ natürliche Duplikat-
  Erkennung (T6) und idempotentes, write-once Hochladen. Zusätzlich `UNIQUE` auf
  `beleg.sha256_hash` in der DB.

## ADR-005: Append-only Audit-Log per DB-Trigger erzwungen
- **Kontext:** Regel 4 (append-only) soll nicht nur per Konvention gelten.
- **Entscheidung:** Applikationsseitig nur INSERT (`services/audit.py`); zusätzlich
  DB-Trigger gegen UPDATE/DELETE (PostgreSQL in der Migration, SQLite in den Tests).
- **Begründung:** Verteidigung in der Tiefe — auch versehentliche oder direkte
  Manipulationen scheitern auf DB-Ebene.

## ADR-006: Cross-DB-Modelle (PostgreSQL prod, SQLite test)
- **Kontext:** Tests sollen ohne Docker laufen.
- **Entscheidung:** Portabler `Uuid`-Typ, `BigInteger().with_variant(Integer, "sqlite")`
  für die Audit-PK, In-Memory-Storage-Backend.
- **Begründung:** Schnelle, hermetische Tests; identische Geschäftslogik gegen
  beide Backends.

## ADR-007: Kein LangChain/LangGraph — natives Mistral Function Calling
- **Kontext:** Intelligenter Frage-Agent (Schritt 4), der Freitext-Fragen zu
  Buchhaltungsdaten per WhatsApp beantwortet.
- **Entscheidung:** Natives Mistral Function Calling über `tools`-Parameter;
  Tool-Router als einfaches Python-Dict; kein Framework (kein LangChain, LangGraph,
  LlamaIndex o.ä.).
- **Begründung:** Der Ablauf ist linear (Frage → Tool-Auswahl → Code-Query → Antwort),
  nicht zyklisch oder autonom-mehrstufig. Framework-Abstraktionen würden die
  Compliance-kritische Logik (Zahlen aus Code, Mandanten-Isolation, Steuer-Guard)
  hinter opaken Schichten verstecken und wären schwerer zu testen.
  Dasselbe Prinzip aus dem n8n-Verwurf: testbarer Code schlägt Convenience.
  **Erweiterungs-Pfad:** Mehr Fähigkeiten = neue Einträge in `TOOL_HANDLERS` +
  neue Tool-Definitionen in `TOOLS`. Kein Umbau nötig.

## ADR-008: Ein flexibles `query_finanzen`-Tool + wenige spezifische Tools + Guard
- **Kontext:** Breite Masse der Datenfragen vs. Compliance bei sensiblen Zahlen.
- **Entscheidung:**
  1. `query_finanzen` — das flexible Herzstück: strukturierte Parameter (art,
     zeitraum, kategorie, betrag_filter, aggregation), Whitelist-basierte Code-
     Ausführung, kein LLM-generiertes SQL.
  2. `get_euer` — eigenes Tool, weil EÜR-Gewinn die wichtigste Zahl ist und
     garantiert exakt der EÜR-Logik (`euer.py`) folgen muss.
  3. `get_offene_rechnungen` / `create_export` — spezifische Tools für klar
     abgegrenzte Anwendungsfälle.
  4. `steuerberatung_grenze` — Guard ohne DB-Zugriff, fester sicherer Hinweis.
- **Begründung:** Schlauheit (fast jede Datenfrage beantwortbar) bei garantierter
  Zahlen-Korrektheit (Code macht die Aggregation, nicht das LLM).

## ADR-009: Antwort-Formulierung code-basiert (kein zweiter LLM-Call)
- **Kontext:** Wie wird die finale Antwort-Nachricht zusammengestellt?
- **Entscheidung:** Code-basierte Templates in `_format_antwort()` /
  `_format_query_antwort()`. Zahlen aus dem Tool-Ergebnis werden direkt eingesetzt.
  Kein zweiter LLM-Call zur sprachlichen Glättung.
- **Begründung:** Stärkste Garantie für Zahlen-aus-Code (Compliance Regel 1 dieses
  Moduls). Günstiger (ein API-Call statt zwei). Deterministisch und testbar.
  Der LLM-Aufruf bleibt auf die Tool-Auswahl beschränkt; die Antwort entsteht
  ausschließlich aus Code-berechneten Werten. Sprachliche Qualität ist ausreichend
  für strukturierte Finanzdaten; sie kann später durch einen optionalen zweiten
  Call verbessert werden, ohne die Zahlen-Garantie aufzugeben.
