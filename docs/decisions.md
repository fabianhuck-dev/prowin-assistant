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
