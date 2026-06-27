"""Tests für den Intent-Service (Frage-Agent).

Kein echter Netzwerk-Call: Mistral-HTTP wird durch einen injizierten
Mock-httpx-Client simuliert. DB nutzt In-Memory-SQLite.

Abgedeckte Anforderungen:
  - Tool-Auswahl (mehrere Formulierungen → richtiges Tool + Parameter)
  - Zahlen-aus-Code: Antwort enthält DB-Zahlen, nie LLM-erdachte Werte
  - Mandanten-Isolation: LLM-geliefertes mandant_id wird ignoriert
  - query_finanzen-Sicherheit: Whitelist, limit-Deckelung, stornierte ausgeschlossen,
    unbekannte Kategorie → Rückfrage
  - Steuer-Guard: steuerberatung_grenze → SAFE_STEUER_HINWEIS
  - Kein Weltwissen: kein Tool-Call → neutraler Hinweis
  - Kein Buchungs-Seiteneffekt: nur create_export schreibt additiv
  - Unklare Frage (kein Zeitraum) → Rückfrage statt Zahlen
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from app.db.models import Buchung, Kategorie, Kundenrechnung, Mandant
from app.services.intent import (
    _MAX_LISTE_LIMIT,
    SAFE_STEUER_HINWEIS,
    IntentService,
    _fmt_betrag,
    _zeitraum_zu_range,
)
from sqlalchemy import func, select

# ---------------------------------------------------------------------------
# Mock-Helfer
# ---------------------------------------------------------------------------


def _mock_tool_response(tool_name: str, args: dict) -> dict:
    """Simuliert eine Mistral-Antwort mit einem Tool-Call."""
    return {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-test",
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(args),
                            },
                        }
                    ],
                },
            }
        ]
    }


def _mock_text_response(text: str = "Das weiß ich leider nicht.") -> dict:
    """Simuliert eine Mistral-Antwort ohne Tool-Call."""
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": text},
            }
        ]
    }


def _make_client(response: dict) -> AsyncMock:
    """Erstellt einen Mock-httpx-Client, der immer die gegebene response zurückgibt."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = response
    mock_resp.raise_for_status = MagicMock()
    client = AsyncMock()
    client.post = AsyncMock(return_value=mock_resp)
    return client


def _service(response: dict) -> IntentService:
    """Bequeme Fabrik: IntentService mit vorgegebener Mock-Antwort."""
    return IntentService(http_client=_make_client(response))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def mandant_a(session) -> Mandant:
    m = Mandant(
        id=uuid.uuid4(),
        name="Anna Muster",
        whatsapp_phone="+4915100000001",
        is_kleinunternehmer=True,
    )
    session.add(m)
    await session.commit()
    await session.refresh(m)
    return m


@pytest_asyncio.fixture
async def mandant_b(session) -> Mandant:
    m = Mandant(
        id=uuid.uuid4(),
        name="Bernd Beispiel",
        whatsapp_phone="+4915100000002",
        is_kleinunternehmer=True,
    )
    session.add(m)
    await session.commit()
    await session.refresh(m)
    return m


@pytest_asyncio.fixture
async def kat_fahrt(session) -> Kategorie:
    k = Kategorie(name="Fahrtkosten", typ="ausgabe", is_system_default=True)
    session.add(k)
    await session.commit()
    await session.refresh(k)
    return k


@pytest_asyncio.fixture
async def kat_provision(session) -> Kategorie:
    k = Kategorie(name="Provision ProWin", typ="einnahme", is_system_default=True)
    session.add(k)
    await session.commit()
    await session.refresh(k)
    return k


async def _buchung(session, mandant_id, typ, betrag, datum, kat=None, storniert=False) -> Buchung:
    b = Buchung(
        mandant_id=mandant_id,
        typ=typ,
        betrag=betrag,
        datum=datum,
        kategorie_id=kat.id if kat else None,
        storniert=storniert,
        bestaetigt_via="test",
    )
    session.add(b)
    await session.flush()
    return b


# ---------------------------------------------------------------------------
# Hilfsfunktions-Tests
# ---------------------------------------------------------------------------


def test_zeitraum_jahr():
    von, bis = _zeitraum_zu_range({"typ": "jahr", "wert": "2026"})
    assert von == date(2026, 1, 1)
    assert bis == date(2026, 12, 31)


def test_zeitraum_monat():
    von, bis = _zeitraum_zu_range({"typ": "monat", "wert": "2026-10"})
    assert von == date(2026, 10, 1)
    assert bis == date(2026, 10, 31)


def test_zeitraum_range():
    von, bis = _zeitraum_zu_range({"typ": "range", "von": "2026-03-01", "bis": "2026-03-31"})
    assert von == date(2026, 3, 1)
    assert bis == date(2026, 3, 31)


def test_zeitraum_relativ_dieses_jahr():
    von, bis = _zeitraum_zu_range({"typ": "relativ", "wert": "dieses_jahr"})
    today = date.today()
    assert von == date(today.year, 1, 1)
    assert bis == date(today.year, 12, 31)


def test_fmt_betrag():
    assert _fmt_betrag(1234.56) == "1.234,56 €"
    assert _fmt_betrag(0.0) == "0,00 €"
    assert _fmt_betrag(789.0) == "789,00 €"


# ---------------------------------------------------------------------------
# Tool-Auswahl-Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_get_euer_gewinn_frage(session, mandant_a):
    """'Was ist mein Gewinn?' → get_euer mit aktuellem Jahr."""
    svc = _service(
        _mock_tool_response("get_euer", {"zeitraum": {"typ": "relativ", "wert": "dieses_jahr"}})
    )
    antwort = await svc.handle("Was ist mein Gewinn?", mandant_a.id, session)
    assert "Gewinn" in antwort
    assert "Einnahmen" in antwort
    assert "Ausgaben" in antwort


@pytest.mark.asyncio
async def test_tool_query_finanzen_ausgaben_monat(session, mandant_a):
    """'Ausgaben Oktober' → query_finanzen mit Monat-Parameter."""
    svc = _service(
        _mock_tool_response(
            "query_finanzen",
            {
                "art": "ausgabe",
                "zeitraum": {"typ": "monat", "wert": "2026-10"},
                "aggregation": "summe",
            },
        )
    )
    antwort = await svc.handle("Ausgaben Oktober", mandant_a.id, session)
    assert "Ausgaben" in antwort


@pytest.mark.asyncio
async def test_tool_query_finanzen_vergleich(session, mandant_a):
    """'Vergleich Oktober vs September' → query_finanzen aggregation=vergleich."""
    svc = _service(
        _mock_tool_response(
            "query_finanzen",
            {
                "art": "ausgabe",
                "zeitraum": {"typ": "monat", "wert": "2026-10"},
                "aggregation": "vergleich",
                "vergleich_zeitraum": {"typ": "monat", "wert": "2026-09"},
            },
        )
    )
    antwort = await svc.handle("Vergleich Oktober mit September", mandant_a.id, session)
    assert "Vergleich" in antwort


@pytest.mark.asyncio
async def test_tool_offene_rechnungen(session, mandant_a):
    """'Wer hat noch nicht bezahlt?' → get_offene_rechnungen."""
    # Offene Rechnung anlegen
    r = Kundenrechnung(
        mandant_id=mandant_a.id,
        rechnungsnummer="RE-001",
        kunde_name="Kunde XY",
        betrag=500.00,
        datum=date(2026, 6, 1),
        bezahlt=False,
    )
    session.add(r)
    await session.commit()

    svc = _service(_mock_tool_response("get_offene_rechnungen", {}))
    antwort = await svc.handle("Wer hat noch nicht bezahlt?", mandant_a.id, session)
    assert "Kunde XY" in antwort
    assert "500,00 €" in antwort


@pytest.mark.asyncio
async def test_tool_create_export(session, mandant_a):
    """'Export für meinen Steuerberater' → create_export."""
    svc = _service(
        _mock_tool_response(
            "create_export",
            {"zeitraum": {"typ": "jahr", "wert": "2026"}},
        )
    )
    antwort = await svc.handle("Export für meinen Steuerberater", mandant_a.id, session)
    assert "Export" in antwort
    assert "erstellt" in antwort


# ---------------------------------------------------------------------------
# Zahlen-aus-Code-Test (Kernregel)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zahlen_aus_code_nicht_vom_llm(session, mandant_a, kat_fahrt):
    """Die Zahlen in der Antwort stammen aus der DB, nicht vom LLM.

    Die DB hat genau 789,00 € Ausgaben im Oktober 2026.
    Das LLM-Mock gibt keine Zahlen zurück (nur Tool-Call mit Parametern).
    Die Antwort muss exakt '789,00 €' enthalten — aus Code berechnet.
    """
    await _buchung(session, mandant_a.id, "ausgabe", 789.00, date(2026, 10, 15), kat_fahrt)
    await session.commit()

    svc = _service(
        _mock_tool_response(
            "query_finanzen",
            {
                "art": "ausgabe",
                "zeitraum": {"typ": "monat", "wert": "2026-10"},
                "aggregation": "summe",
            },
        )
    )
    antwort = await svc.handle("Ausgaben Oktober 2026", mandant_a.id, session)
    assert "789,00 €" in antwort, f"Erwartet '789,00 €' in Antwort, bekam: {antwort!r}"


@pytest.mark.asyncio
async def test_zahlen_aus_code_euer_gewinn(session, mandant_a, kat_fahrt, kat_provision):
    """Gewinn = Einnahmen - Ausgaben, berechnet aus DB — nicht vom LLM."""
    await _buchung(session, mandant_a.id, "einnahme", 1234.56, date(2026, 5, 1), kat_provision)
    await _buchung(session, mandant_a.id, "ausgabe", 200.00, date(2026, 5, 5), kat_fahrt)
    await session.commit()

    svc = _service(_mock_tool_response("get_euer", {"zeitraum": {"typ": "jahr", "wert": "2026"}}))
    antwort = await svc.handle("Was ist mein Gewinn?", mandant_a.id, session)

    assert "1.234,56 €" in antwort, f"Einnahmen fehlen: {antwort!r}"
    assert "200,00 €" in antwort, f"Ausgaben fehlen: {antwort!r}"
    assert "1.034,56 €" in antwort, f"Gewinn fehlt: {antwort!r}"


# ---------------------------------------------------------------------------
# Mandanten-Isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mandanten_isolation_llm_arg_ignoriert(session, mandant_a, mandant_b, kat_fahrt):
    """LLM-geliefertes mandant_id=B in den Tool-Args wird ignoriert.

    Mandant A hat keine Buchungen; B hat 999 €. Anfrage läuft als A.
    Das Mock gibt mandant_id=B in den Args mit — muss trotzdem 0,00 € zurückgeben.
    """
    await _buchung(session, mandant_b.id, "ausgabe", 999.00, date(2026, 10, 1), kat_fahrt)
    await session.commit()

    # LLM "schmuggelt" mandant_id von B in die Args
    svc = _service(
        _mock_tool_response(
            "query_finanzen",
            {
                "art": "ausgabe",
                "zeitraum": {"typ": "monat", "wert": "2026-10"},
                "aggregation": "summe",
                "mandant_id": str(mandant_b.id),  # wird ignoriert
            },
        )
    )
    antwort = await svc.handle("Ausgaben Oktober", mandant_a.id, session)
    # A hat keine Buchungen → 0,00 €
    assert "0,00 €" in antwort, f"Isolation verletzt — B's Daten in Antwort: {antwort!r}"
    assert "999" not in antwort, f"Mandant B's Betrag darf nicht erscheinen: {antwort!r}"


@pytest.mark.asyncio
async def test_mandanten_isolation_euer(session, mandant_a, mandant_b, kat_provision):
    """get_euer gibt nur A's Daten zurück, auch wenn B Buchungen hat."""
    await _buchung(session, mandant_b.id, "einnahme", 5000.00, date(2026, 3, 1), kat_provision)
    await session.commit()

    svc = _service(_mock_tool_response("get_euer", {"zeitraum": {"typ": "jahr", "wert": "2026"}}))
    antwort = await svc.handle("Gewinn?", mandant_a.id, session)
    assert "5.000" not in antwort, f"B's Daten dürfen nicht erscheinen: {antwort!r}"


# ---------------------------------------------------------------------------
# query_finanzen-Sicherheit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stornierte_buchungen_ausgeschlossen(session, mandant_a, kat_fahrt):
    """Stornierte Buchungen dürfen nicht in query_finanzen erscheinen."""
    await _buchung(
        session, mandant_a.id, "ausgabe", 100.00, date(2026, 10, 1), kat_fahrt, storniert=False
    )
    await _buchung(
        session, mandant_a.id, "ausgabe", 500.00, date(2026, 10, 2), kat_fahrt, storniert=True
    )
    await session.commit()

    svc = _service(
        _mock_tool_response(
            "query_finanzen",
            {
                "art": "ausgabe",
                "zeitraum": {"typ": "monat", "wert": "2026-10"},
                "aggregation": "summe",
            },
        )
    )
    antwort = await svc.handle("Ausgaben Oktober", mandant_a.id, session)
    assert "100,00 €" in antwort, f"Aktive Buchung fehlt: {antwort!r}"
    assert "500" not in antwort, f"Stornierte Buchung darf nicht erscheinen: {antwort!r}"


@pytest.mark.asyncio
async def test_limit_wird_gedeckelt(session, mandant_a, kat_fahrt):
    """limit-Parameter wird auf _MAX_LISTE_LIMIT gedeckelt."""
    for i in range(30):
        await _buchung(
            session, mandant_a.id, "ausgabe", float(10 + i), date(2026, 10, 1), kat_fahrt
        )
    await session.commit()

    # Direkt Handler testen — limit wird im Handler gedeckelt, nicht durch LLM
    from app.services.intent import _handle_query_finanzen

    result = await _handle_query_finanzen(
        {
            "art": "ausgabe",
            "zeitraum": {"typ": "monat", "wert": "2026-10"},
            "aggregation": "liste",
            "limit": 9999,
        },
        mandant_a.id,
        session,
    )
    assert result["gezeigt"] <= _MAX_LISTE_LIMIT, f"limit nicht gedeckelt: {result['gezeigt']}"


@pytest.mark.asyncio
async def test_unbekannte_kategorie_rueckfrage(session, mandant_a, kat_fahrt):
    """Unbekannte Kategorie → Fehlermeldung mit Rückfrage, keine Ausnahme."""
    svc = _service(
        _mock_tool_response(
            "query_finanzen",
            {
                "art": "ausgabe",
                "zeitraum": {"typ": "jahr", "wert": "2026"},
                "kategorie": "GibtEsNicht",
                "aggregation": "summe",
            },
        )
    )
    antwort = await svc.handle("Ausgaben für GibtEsNicht", mandant_a.id, session)
    assert "GibtEsNicht" in antwort
    # Muss bekannte Kategorien vorschlagen oder nachfragen
    assert "Fahrtkosten" in antwort or "kenne" in antwort


@pytest.mark.asyncio
async def test_kein_sql_injection_durch_feld_parameter(session, mandant_a):
    """LLM-gelieferte 'Felder' werden niemals als SQL ausgeführt.

    Auch wenn das LLM beliebige Strings als Kategorie mitschickt, läuft nur
    ein parametrisierter DB-Lookup — kein dynamisches SQL.
    """
    from app.services.intent import _handle_query_finanzen

    # SQL-Injection-Versuch als Kategorie
    result = await _handle_query_finanzen(
        {
            "art": "ausgabe",
            "zeitraum": {"typ": "jahr", "wert": "2026"},
            "kategorie": "'; DROP TABLE buchung; --",
        },
        mandant_a.id,
        session,
    )
    # Muss sauber mit unbekannte_kategorie-Fehler antworten, nie crashen
    assert result.get("fehler") == "unbekannte_kategorie"
    # buchung-Tabelle muss noch existieren
    count = await session.scalar(select(func.count()).select_from(Buchung))
    assert count is not None


@pytest.mark.asyncio
async def test_kein_zeitraum_rueckfrage(session, mandant_a):
    """Fehlender Zeitraum → Rückfrage statt Crash oder Zahlen."""
    from app.services.intent import _handle_query_finanzen

    result = await _handle_query_finanzen(
        {"art": "ausgabe"},  # kein zeitraum
        mandant_a.id,
        session,
    )
    assert result.get("fehler") == "zeitraum_fehlt"
    assert "frage" in result


# ---------------------------------------------------------------------------
# Steuer-Guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_steuer_guard_auto_absetzen(session, mandant_a):
    """'Kann ich mein Auto absetzen?' → steuerberatung_grenze → SAFE_STEUER_HINWEIS."""
    svc = _service(_mock_tool_response("steuerberatung_grenze", {}))
    antwort = await svc.handle("Kann ich mein Auto absetzen?", mandant_a.id, session)
    assert antwort == SAFE_STEUER_HINWEIS


@pytest.mark.asyncio
async def test_steuer_guard_wie_viel_steuer(session, mandant_a):
    """'Wie viel Steuer zahle ich?' → SAFE_STEUER_HINWEIS."""
    svc = _service(_mock_tool_response("steuerberatung_grenze", {}))
    antwort = await svc.handle("Wie viel Steuer zahle ich?", mandant_a.id, session)
    assert antwort == SAFE_STEUER_HINWEIS


@pytest.mark.asyncio
async def test_steuer_guard_mehr_investieren(session, mandant_a):
    """'Soll ich mehr investieren?' → SAFE_STEUER_HINWEIS."""
    svc = _service(_mock_tool_response("steuerberatung_grenze", {}))
    antwort = await svc.handle("Soll ich mehr in ProWin investieren?", mandant_a.id, session)
    assert antwort == SAFE_STEUER_HINWEIS


@pytest.mark.asyncio
async def test_steuer_guard_keine_zahl_erfunden(session, mandant_a):
    """steuerberatung_grenze darf keine Zahlen/Empfehlungen enthalten."""
    svc = _service(_mock_tool_response("steuerberatung_grenze", {}))
    antwort = await svc.handle("Wie viel Steuer zahle ich?", mandant_a.id, session)
    # Keine prozentualen Angaben, keine Eurobeträge
    import re

    assert not re.search(r"\d+[,.]?\d*\s*€", antwort), f"Zahl in Guard-Antwort: {antwort!r}"
    assert not re.search(r"\d+\s*%", antwort), f"Prozent in Guard-Antwort: {antwort!r}"


# ---------------------------------------------------------------------------
# Kein Weltwissen / Themenfremde Fragen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kein_weltwissen_wetter(session, mandant_a):
    """Themenfremde Frage (Wetter) → kein Tool-Call → Hinweis auf Belis Zweck."""
    svc = _service(_mock_text_response("Das Wetter morgen wird sonnig."))
    antwort = await svc.handle("Wie wird das Wetter morgen?", mandant_a.id, session)
    # Keine Wetterinfo, nur Hinweis auf Buchhaltungszweck
    assert "Wetter" not in antwort or "Buchhaltung" in antwort or "Buchung" in antwort
    assert "sonnig" not in antwort, f"LLM-Weltwissen in Antwort: {antwort!r}"


@pytest.mark.asyncio
async def test_kein_weltwissen_rezept(session, mandant_a):
    """Kochrezept-Frage → kein Tool-Call → Hinweis, keine Rezeptantwort."""
    svc = _service(_mock_text_response("Für Spaghetti Bolognese braucht man..."))
    antwort = await svc.handle("Gib mir ein Rezept für Spaghetti.", mandant_a.id, session)
    assert "Spaghetti" not in antwort or "Buchung" in antwort


# ---------------------------------------------------------------------------
# Kein Buchungs-Seiteneffekt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_finanzen_kein_schreiben_in_buchung(session, mandant_a, kat_fahrt):
    """query_finanzen schreibt nicht in die buchung-Tabelle."""
    await _buchung(session, mandant_a.id, "ausgabe", 100.0, date(2026, 10, 1), kat_fahrt)
    await session.commit()

    vor = await session.scalar(select(func.count()).select_from(Buchung))

    svc = _service(
        _mock_tool_response(
            "query_finanzen",
            {
                "art": "ausgabe",
                "zeitraum": {"typ": "monat", "wert": "2026-10"},
                "aggregation": "summe",
            },
        )
    )
    await svc.handle("Ausgaben Oktober", mandant_a.id, session)
    await session.flush()

    nach = await session.scalar(select(func.count()).select_from(Buchung))
    assert vor == nach, f"query_finanzen hat Buchungen verändert: {vor} → {nach}"


@pytest.mark.asyncio
async def test_get_euer_kein_schreiben(session, mandant_a, kat_provision):
    """get_euer schreibt nicht in die buchung-Tabelle."""
    vor = await session.scalar(select(func.count()).select_from(Buchung))
    svc = _service(_mock_tool_response("get_euer", {"zeitraum": {"typ": "jahr", "wert": "2026"}}))
    await svc.handle("Gewinn?", mandant_a.id, session)
    await session.flush()
    nach = await session.scalar(select(func.count()).select_from(Buchung))
    assert vor == nach


@pytest.mark.asyncio
async def test_offene_rechnungen_kein_schreiben(session, mandant_a):
    """get_offene_rechnungen schreibt nicht in die buchung-Tabelle."""
    vor = await session.scalar(select(func.count()).select_from(Buchung))
    svc = _service(_mock_tool_response("get_offene_rechnungen", {}))
    await svc.handle("Offene Rechnungen?", mandant_a.id, session)
    await session.flush()
    nach = await session.scalar(select(func.count()).select_from(Buchung))
    assert vor == nach


# ---------------------------------------------------------------------------
# Aggregations-Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pro_kategorie_aggregation(session, mandant_a, kat_fahrt, kat_provision):
    """pro_kategorie summiert korrekt nach Kategorie."""
    kat_buero = Kategorie(name="Bürobedarf", typ="ausgabe", is_system_default=True)
    session.add(kat_buero)
    await session.flush()

    await _buchung(session, mandant_a.id, "ausgabe", 100.0, date(2026, 10, 1), kat_fahrt)
    await _buchung(session, mandant_a.id, "ausgabe", 200.0, date(2026, 10, 2), kat_fahrt)
    await _buchung(session, mandant_a.id, "ausgabe", 50.0, date(2026, 10, 3), kat_buero)
    await session.commit()

    from app.services.intent import _handle_query_finanzen

    result = await _handle_query_finanzen(
        {
            "art": "ausgabe",
            "zeitraum": {"typ": "monat", "wert": "2026-10"},
            "aggregation": "pro_kategorie",
        },
        mandant_a.id,
        session,
    )
    assert result["aggregation"] == "pro_kategorie"
    assert result["gesamt"] == pytest.approx(350.0, abs=0.01)
    kat_namen = {k["kategorie"] for k in result["kategorien"]}
    assert "Fahrtkosten" in kat_namen
    assert "Bürobedarf" in kat_namen


@pytest.mark.asyncio
async def test_pro_monat_aggregation(session, mandant_a, kat_fahrt):
    """pro_monat gruppiert korrekt nach Monat."""
    await _buchung(session, mandant_a.id, "ausgabe", 100.0, date(2026, 9, 15), kat_fahrt)
    await _buchung(session, mandant_a.id, "ausgabe", 200.0, date(2026, 10, 15), kat_fahrt)
    await session.commit()

    from app.services.intent import _handle_query_finanzen

    result = await _handle_query_finanzen(
        {
            "art": "ausgabe",
            "zeitraum": {"typ": "range", "von": "2026-09-01", "bis": "2026-10-31"},
            "aggregation": "pro_monat",
        },
        mandant_a.id,
        session,
    )
    monate = {m["monat"]: m["summe"] for m in result["monate"]}
    assert "2026-09" in monate
    assert "2026-10" in monate
    assert monate["2026-09"] == pytest.approx(100.0, abs=0.01)
    assert monate["2026-10"] == pytest.approx(200.0, abs=0.01)


@pytest.mark.asyncio
async def test_vergleich_zwei_zeitraeume(session, mandant_a, kat_fahrt):
    """aggregation=vergleich liefert Daten für beide Zeiträume."""
    await _buchung(session, mandant_a.id, "ausgabe", 300.0, date(2026, 10, 1), kat_fahrt)
    await _buchung(session, mandant_a.id, "ausgabe", 150.0, date(2026, 9, 1), kat_fahrt)
    await session.commit()

    from app.services.intent import _handle_query_finanzen

    result = await _handle_query_finanzen(
        {
            "art": "ausgabe",
            "zeitraum": {"typ": "monat", "wert": "2026-10"},
            "aggregation": "vergleich",
            "vergleich_zeitraum": {"typ": "monat", "wert": "2026-09"},
        },
        mandant_a.id,
        session,
    )
    assert result["vergleich"] is True
    assert result["zeitraum_1"]["summe"] == pytest.approx(300.0, abs=0.01)
    assert result["zeitraum_2"]["summe"] == pytest.approx(150.0, abs=0.01)
    assert result["differenz"] == pytest.approx(150.0, abs=0.01)


@pytest.mark.asyncio
async def test_vergleich_in_antwort_text(session, mandant_a, kat_fahrt):
    """Vergleich-Antwort enthält DB-Zahlen in formatiertem Text."""
    await _buchung(session, mandant_a.id, "ausgabe", 300.0, date(2026, 10, 1), kat_fahrt)
    await _buchung(session, mandant_a.id, "ausgabe", 150.0, date(2026, 9, 1), kat_fahrt)
    await session.commit()

    svc = _service(
        _mock_tool_response(
            "query_finanzen",
            {
                "art": "ausgabe",
                "zeitraum": {"typ": "monat", "wert": "2026-10"},
                "aggregation": "vergleich",
                "vergleich_zeitraum": {"typ": "monat", "wert": "2026-09"},
            },
        )
    )
    antwort = await svc.handle("Vergleich Oktober mit September", mandant_a.id, session)
    assert "300,00 €" in antwort
    assert "150,00 €" in antwort


# ---------------------------------------------------------------------------
# create_export-Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_export_kein_buchungspfad(session, mandant_a, kat_fahrt):
    """create_export schreibt nicht in die buchung-Tabelle."""
    await _buchung(session, mandant_a.id, "ausgabe", 100.0, date(2026, 10, 1), kat_fahrt)
    await session.commit()

    vor = await session.scalar(select(func.count()).select_from(Buchung))

    svc = _service(
        _mock_tool_response(
            "create_export",
            {"zeitraum": {"typ": "monat", "wert": "2026-10"}},
        )
    )
    await svc.handle("Export für Steuerberater", mandant_a.id, session)
    await session.flush()

    nach = await session.scalar(select(func.count()).select_from(Buchung))
    assert vor == nach, "create_export hat Buchungen verändert"


# ---------------------------------------------------------------------------
# Offene Rechnungen: keine Daten
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_offene_rechnungen_leer(session, mandant_a):
    """Keine offenen Rechnungen → freundliche Meldung."""
    svc = _service(_mock_tool_response("get_offene_rechnungen", {}))
    antwort = await svc.handle("Offene Rechnungen?", mandant_a.id, session)
    assert "keine" in antwort.lower() or "alles" in antwort.lower()
