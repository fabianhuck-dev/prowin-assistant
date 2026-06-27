"""Intent-Service: Frage-Agent für Buchhaltungsdaten.

Verarbeitet Freitext-Fragen und beantwortet sie mit eigenen Daten des Mandanten.

Compliance-Regeln dieses Moduls:
1. Zahlen kommen IMMER aus Code (DB-Queries), nie aus dem LLM.
   Das LLM wählt nur Tool + Parameter; alle konkreten Zahlen in der Antwort
   werden durch Code aus der DB berechnet und per Template eingesetzt.
2. Read-only. Einzige Ausnahme: create_export (legt Export-Datei an, kein Buchungspfad).
3. mandant_id kommt IMMER aus dem Server-Kontext — LLM-gelieferte mandant_id-Args
   werden vor dem Handler-Aufruf entfernt.
4. Steuerberatung, Rechts-/Finanzempfehlungen und Weltwissen → steuerberatung_grenze.
"""

from __future__ import annotations

import calendar
import json
import logging
import uuid
from collections import defaultdict
from datetime import date, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Buchung, Kategorie, Kundenrechnung
from app.services.audit import append_audit
from app.services.euer import berechne_euer
from app.services.export import erstelle_export

logger = logging.getLogger("prowin.intent")

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------

SAFE_STEUER_HINWEIS = (
    "Das ist eine Frage für deinen Steuerberater — die darf und will ich dir nicht "
    "verbindlich beantworten. Was ich dir sofort zeigen kann, sind deine eigenen Zahlen: "
    "z.B. dein Gewinn, deine Ausgaben in einer Kategorie oder ein Monatsvergleich."
)

SYSTEM_PROMPT = (
    "Du bist Beli, ein freundlicher Buchhaltungs-Assistent für eine selbstständige "
    "ProWin-Vertriebspartnerin in Deutschland. Du beantwortest ausschließlich Fragen zu "
    "IHREN eigenen Buchhaltungs-Daten, indem du das passende Tool aufrufst. "
    "Regeln: "
    "(1) Erfinde NIEMALS Zahlen — jede Zahl stammt aus einem Tool-Ergebnis, du rechnest nie selbst. "
    "(2) Gib KEINE steuerrechtliche Bewertung, KEINE Geschäfts- oder Finanzempfehlung und "
    "KEIN allgemeines Wissen — für solche Fragen rufst du `steuerberatung_grenze` auf. "
    "(3) Verstehe auch umgangssprachliche oder ungenaue Fragen und ordne sie dem richtigen "
    "Tool zu. "
    "(4) Ist unklar, welcher Zeitraum oder welche Kategorie gemeint ist, frag kurz und "
    "freundlich nach, statt zu raten. "
    "(5) Antworte knapp, freundlich, auf Deutsch mit 'du'. "
    "(6) Wenn keine Daten vorhanden sind, sag das ehrlich. "
    "(7) Für Fragen zu Steuer, was absetzbar ist, wie viel Steuer zu zahlen ist, ob sich "
    "etwas lohnt oder andere Empfehlungen rufst du immer `steuerberatung_grenze` auf."
)

_MAX_LISTE_LIMIT = 20

# ---------------------------------------------------------------------------
# Tool-Definitionen (Mistral Function Calling Format)
# ---------------------------------------------------------------------------

_ZEITRAUM_SCHEMA: dict = {
    "type": "object",
    "description": "Zeitraum für die Abfrage",
    "properties": {
        "typ": {
            "type": "string",
            "enum": ["jahr", "monat", "relativ", "range"],
        },
        "wert": {
            "type": "string",
            "description": (
                "Für typ=jahr: '2026'; typ=monat: '2026-10'; "
                "typ=relativ: 'dieses_jahr'|'letzter_monat'|'letzte_30_tage'"
            ),
        },
        "von": {"type": "string", "description": "ISO-Datum YYYY-MM-DD (nur für typ=range)"},
        "bis": {"type": "string", "description": "ISO-Datum YYYY-MM-DD (nur für typ=range)"},
    },
    "required": ["typ"],
}

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "query_finanzen",
            "description": (
                "Aggregiert Einnahmen und/oder Ausgaben für einen Zeitraum. "
                "Für allgemeine Datenfragen: Summen, Listen, Kategorieübersichten, "
                "Monatsverläufe, Vergleiche, Betragsfilter. "
                "Beispiele: 'Ausgaben Oktober', 'teuerste Ausgaben dieses Jahr', "
                "'Fahrtkosten letzten Monat', 'alle Belege über 50 EUR', "
                "'Ausgaben pro Kategorie', 'Vergleich Oktober mit September'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "art": {
                        "type": "string",
                        "enum": ["ausgabe", "einnahme", "beides"],
                        "description": "Welche Art von Buchungen abfragen",
                    },
                    "zeitraum": _ZEITRAUM_SCHEMA,
                    "kategorie": {
                        "type": "string",
                        "description": "Optionaler Kategoriename (z.B. 'Fahrtkosten', 'Bürobedarf')",
                    },
                    "betrag_min": {
                        "type": "number",
                        "description": "Minimaler Betrag in EUR",
                    },
                    "betrag_max": {
                        "type": "number",
                        "description": "Maximaler Betrag in EUR",
                    },
                    "aggregation": {
                        "type": "string",
                        "enum": [
                            "summe",
                            "liste",
                            "anzahl",
                            "pro_kategorie",
                            "pro_monat",
                            "vergleich",
                        ],
                        "description": "Art der Aggregation",
                    },
                    "vergleich_zeitraum": {
                        **_ZEITRAUM_SCHEMA,
                        "description": "Zweiter Zeitraum (nur bei aggregation=vergleich)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": f"Max. Einträge bei aggregation=liste (max {_MAX_LISTE_LIMIT})",
                    },
                },
                "required": ["art"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_euer",
            "description": (
                "Gibt Einnahmen, Ausgaben und Gewinn nach EÜR-Logik zurück. "
                "Für Fragen wie 'Was ist mein Gewinn?', 'bin ich im Plus?', "
                "'Wie läuft mein Jahr?', 'Was hab ich dieses Jahr verdient?'"
            ),
            "parameters": {
                "type": "object",
                "properties": {"zeitraum": _ZEITRAUM_SCHEMA},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_offene_rechnungen",
            "description": (
                "Gibt offene Kundenrechnungen zurück. "
                "Für Fragen wie 'Wer hat noch nicht bezahlt?', "
                "'offene Rechnungen?', 'Was steht noch aus?'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "nur_faellig": {
                        "type": "boolean",
                        "description": "Nur bereits überfällige Rechnungen anzeigen",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_export",
            "description": (
                "Erstellt DATEV-CSV und PDF-Bundle für den Steuerberater. "
                "Für Fragen wie 'Export für meinen Steuerberater', "
                "'DATEV-Datei erstellen', 'Unterlagen für den Steuerberater'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "zeitraum": _ZEITRAUM_SCHEMA,
                    "empfaenger_email": {
                        "type": "string",
                        "description": "Optionale E-Mail-Adresse für den Versand",
                    },
                },
                "required": ["zeitraum"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "steuerberatung_grenze",
            "description": (
                "Setzt eine Grenze bei Steuer-/Rechtsfragen und Empfehlungen. "
                "Aufrufen wenn gefragt wird: ob etwas absetzbar ist, wie viel Steuer "
                "zu zahlen ist, ob sich etwas lohnt, Lohntipps, rechtliche Einschätzungen, "
                "ob mehr investiert werden soll, allgemeines Finanz-/Weltwissen."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------


def _zeitraum_zu_range(zeitraum: dict) -> tuple[date, date]:
    """Löst einen strukturierten Zeitraum in (von, bis) auf."""
    typ = zeitraum.get("typ", "")
    today = date.today()

    if typ == "jahr":
        try:
            jahr = int(zeitraum.get("wert", today.year))
        except (ValueError, TypeError):
            jahr = today.year
        return date(jahr, 1, 1), date(jahr, 12, 31)

    if typ == "monat":
        try:
            teile = str(zeitraum.get("wert", "")).split("-")
            jahr, monat = int(teile[0]), int(teile[1])
        except (ValueError, TypeError, IndexError):
            return date(today.year, today.month, 1), today
        letzter = calendar.monthrange(jahr, monat)[1]
        return date(jahr, monat, 1), date(jahr, monat, letzter)

    if typ == "relativ":
        wert = zeitraum.get("wert", "dieses_jahr")
        if wert == "letzter_monat":
            if today.month == 1:
                monat, jahr = 12, today.year - 1
            else:
                monat, jahr = today.month - 1, today.year
            letzter = calendar.monthrange(jahr, monat)[1]
            return date(jahr, monat, 1), date(jahr, monat, letzter)
        if wert == "letzte_30_tage":
            return today - timedelta(days=30), today
        # dieses_jahr + Fallback
        return date(today.year, 1, 1), date(today.year, 12, 31)

    if typ == "range":
        try:
            return date.fromisoformat(zeitraum["von"]), date.fromisoformat(zeitraum["bis"])
        except (KeyError, ValueError):
            return date(today.year, 1, 1), date(today.year, 12, 31)

    return date(today.year, 1, 1), date(today.year, 12, 31)


def _fmt_betrag(value: float) -> str:
    """Formatiert einen Betrag als deutschen Währungsstring (1.234,56 €)."""
    formatted = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{formatted} €"


# ---------------------------------------------------------------------------
# DB-Hilfsfunktionen (alle mandant_id-strikt)
# ---------------------------------------------------------------------------


async def _resolve_kategorie(
    session: AsyncSession,
    mandant_id: uuid.UUID,
    kategorie_name: str,
) -> uuid.UUID | None:
    return await session.scalar(
        select(Kategorie.id).where(
            Kategorie.name == kategorie_name,
            (Kategorie.mandant_id == mandant_id) | Kategorie.is_system_default.is_(True),
        )
    )


async def _kategorien_liste(
    session: AsyncSession,
    mandant_id: uuid.UUID,
) -> list[str]:
    rows = await session.execute(
        select(Kategorie.name)
        .where((Kategorie.mandant_id == mandant_id) | Kategorie.is_system_default.is_(True))
        .order_by(Kategorie.name)
    )
    return list(rows.scalars().all())


async def _fetch_buchungen(
    session: AsyncSession,
    mandant_id: uuid.UUID,
    art: str,
    von: date,
    bis: date,
    kategorie_id: uuid.UUID | None,
    betrag_min: float | None,
    betrag_max: float | None,
) -> list[tuple[Buchung, str | None]]:
    """Holt nicht-stornierte Buchungen mit Kategoriename. Immer mandant_id-strikt."""
    stmt = (
        select(Buchung, Kategorie.name)
        .join(Kategorie, Kategorie.id == Buchung.kategorie_id, isouter=True)
        .where(
            Buchung.mandant_id == mandant_id,
            Buchung.storniert.is_(False),
            Buchung.datum >= von,
            Buchung.datum <= bis,
        )
        .order_by(Buchung.betrag.desc())
    )
    if art in ("ausgabe", "einnahme"):
        stmt = stmt.where(Buchung.typ == art)
    if kategorie_id is not None:
        stmt = stmt.where(Buchung.kategorie_id == kategorie_id)
    if betrag_min is not None:
        stmt = stmt.where(Buchung.betrag >= betrag_min)
    if betrag_max is not None:
        stmt = stmt.where(Buchung.betrag <= betrag_max)
    return [(b, name) for b, name in (await session.execute(stmt)).all()]


# ---------------------------------------------------------------------------
# Tool-Handler
# ---------------------------------------------------------------------------


async def _handle_query_finanzen(
    args: dict,
    mandant_id: uuid.UUID,
    session: AsyncSession,
) -> dict:
    """Flexibles Haupt-Tool für Finanzdaten. Nur Whitelist-Operationen, kein LLM-SQL."""
    art = args.get("art", "beides")
    if art not in ("ausgabe", "einnahme", "beides"):
        art = "beides"

    aggregation = args.get("aggregation", "summe")
    if aggregation not in ("summe", "liste", "anzahl", "pro_kategorie", "pro_monat", "vergleich"):
        aggregation = "summe"

    limit = min(int(args.get("limit") or 10), _MAX_LISTE_LIMIT)

    zeitraum_raw = args.get("zeitraum")
    if not zeitraum_raw:
        return {
            "fehler": "zeitraum_fehlt",
            "frage": "Für welchen Zeitraum möchtest du die Zahlen?",
        }

    von, bis = _zeitraum_zu_range(zeitraum_raw)
    betrag_min = args.get("betrag_min")
    betrag_max = args.get("betrag_max")

    # Kategorie-Validierung gegen bekannte Kategorien (keine LLM-generierten Feldnamen)
    kategorie_id = None
    kategorie_name = args.get("kategorie")
    if kategorie_name:
        kategorie_id = await _resolve_kategorie(session, mandant_id, kategorie_name)
        if kategorie_id is None:
            bekannte = await _kategorien_liste(session, mandant_id)
            return {
                "fehler": "unbekannte_kategorie",
                "kategorie_gesucht": kategorie_name,
                "bekannte_kategorien": bekannte,
                "frage": (
                    f"Ich kenne die Kategorie '{kategorie_name}' nicht. "
                    f"Meinst du eine dieser Kategorien? {', '.join(bekannte[:5])}"
                ),
            }

    if aggregation == "vergleich":
        vergleich_raw = args.get("vergleich_zeitraum")
        if not vergleich_raw:
            return {
                "fehler": "vergleich_zeitraum_fehlt",
                "frage": "Mit welchem Zeitraum soll ich vergleichen?",
            }
        von2, bis2 = _zeitraum_zu_range(vergleich_raw)
        rows1 = await _fetch_buchungen(
            session, mandant_id, art, von, bis, kategorie_id, betrag_min, betrag_max
        )
        rows2 = await _fetch_buchungen(
            session, mandant_id, art, von2, bis2, kategorie_id, betrag_min, betrag_max
        )
        summe1 = round(sum(float(b.betrag) for b, _ in rows1), 2)
        summe2 = round(sum(float(b.betrag) for b, _ in rows2), 2)
        return {
            "vergleich": True,
            "zeitraum_1": {
                "von": von.isoformat(),
                "bis": bis.isoformat(),
                "summe": summe1,
                "anzahl": len(rows1),
            },
            "zeitraum_2": {
                "von": von2.isoformat(),
                "bis": bis2.isoformat(),
                "summe": summe2,
                "anzahl": len(rows2),
            },
            "differenz": round(summe1 - summe2, 2),
        }

    rows = await _fetch_buchungen(
        session, mandant_id, art, von, bis, kategorie_id, betrag_min, betrag_max
    )

    if aggregation == "summe":
        return {
            "aggregation": "summe",
            "art": art,
            "von": von.isoformat(),
            "bis": bis.isoformat(),
            "summe": round(sum(float(b.betrag) for b, _ in rows), 2),
            "anzahl": len(rows),
        }

    if aggregation == "anzahl":
        return {
            "aggregation": "anzahl",
            "art": art,
            "von": von.isoformat(),
            "bis": bis.isoformat(),
            "anzahl": len(rows),
        }

    if aggregation == "liste":
        eintraege = [
            {
                "datum": b.datum.isoformat(),
                "betrag": float(b.betrag),
                "haendler": b.haendler or b.buchungstext or "—",
                "kategorie": kat_name or "Ohne Kategorie",
                "typ": b.typ,
            }
            for b, kat_name in rows[:limit]
        ]
        return {
            "aggregation": "liste",
            "art": art,
            "von": von.isoformat(),
            "bis": bis.isoformat(),
            "eintraege": eintraege,
            "gesamt_gefunden": len(rows),
            "gezeigt": len(eintraege),
        }

    if aggregation == "pro_kategorie":
        kat_summen: dict[str, float] = defaultdict(float)
        kat_anzahl: dict[str, int] = defaultdict(int)
        for b, kat_name in rows:
            key = kat_name or "Ohne Kategorie"
            kat_summen[key] += float(b.betrag)
            kat_anzahl[key] += 1
        kategorien = [
            {"kategorie": k, "summe": round(v, 2), "anzahl": kat_anzahl[k]}
            for k, v in sorted(kat_summen.items(), key=lambda x: -x[1])
        ]
        return {
            "aggregation": "pro_kategorie",
            "art": art,
            "von": von.isoformat(),
            "bis": bis.isoformat(),
            "kategorien": kategorien,
            "gesamt": round(sum(kat_summen.values()), 2),
        }

    if aggregation == "pro_monat":
        monat_summen: dict[str, float] = defaultdict(float)
        monat_anzahl: dict[str, int] = defaultdict(int)
        for b, _ in rows:
            key = b.datum.strftime("%Y-%m")
            monat_summen[key] += float(b.betrag)
            monat_anzahl[key] += 1
        monate = [
            {"monat": k, "summe": round(v, 2), "anzahl": monat_anzahl[k]}
            for k, v in sorted(monat_summen.items())
        ]
        return {
            "aggregation": "pro_monat",
            "art": art,
            "von": von.isoformat(),
            "bis": bis.isoformat(),
            "monate": monate,
            "gesamt": round(sum(monat_summen.values()), 2),
        }

    return {"fehler": "unbekannte_aggregation"}


async def _handle_get_euer(
    args: dict,
    mandant_id: uuid.UUID,
    session: AsyncSession,
) -> dict:
    zeitraum_raw = args.get("zeitraum")
    if zeitraum_raw:
        von, _ = _zeitraum_zu_range(zeitraum_raw)
        jahr = von.year
    else:
        jahr = date.today().year

    euer = await berechne_euer(session, mandant_id, jahr)
    return {
        "jahr": euer.jahr,
        "einnahmen": euer.einnahmen_gesamt,
        "ausgaben": euer.ausgaben_gesamt,
        "gewinn": euer.gewinn,
        "anzahl_buchungen": euer.anzahl_buchungen,
    }


async def _handle_get_offene_rechnungen(
    args: dict,
    mandant_id: uuid.UUID,
    session: AsyncSession,
) -> dict:
    nur_faellig = bool(args.get("nur_faellig", False))
    today = date.today()

    stmt = select(Kundenrechnung).where(
        Kundenrechnung.mandant_id == mandant_id,
        Kundenrechnung.bezahlt.is_(False),
    )
    if nur_faellig:
        stmt = stmt.where(Kundenrechnung.faellig_am <= today)
    stmt = stmt.order_by(Kundenrechnung.faellig_am)

    rechnungen = list((await session.execute(stmt)).scalars().all())
    items = [
        {
            "rechnungsnummer": r.rechnungsnummer,
            "kunde": r.kunde_name,
            "betrag": float(r.betrag),
            "datum": r.datum.isoformat(),
            "faellig_am": r.faellig_am.isoformat() if r.faellig_am else None,
            "ueberfaellig": r.faellig_am is not None and r.faellig_am < today,
        }
        for r in rechnungen
    ]
    return {
        "offene_rechnungen": items,
        "anzahl": len(items),
        "gesamt_ausstehend": round(sum(float(r.betrag) for r in rechnungen), 2),
    }


async def _handle_create_export(
    args: dict,
    mandant_id: uuid.UUID,
    session: AsyncSession,
) -> dict:
    zeitraum_raw = args.get("zeitraum")
    if not zeitraum_raw:
        return {
            "fehler": "zeitraum_fehlt",
            "frage": "Für welchen Zeitraum soll ich den Export erstellen?",
        }

    von, bis = _zeitraum_zu_range(zeitraum_raw)
    csv_export = await erstelle_export(session, mandant_id, von, bis, "datev_csv")
    await erstelle_export(session, mandant_id, von, bis, "pdf")

    await append_audit(
        session,
        mandant_id=mandant_id,
        entity_type="export",
        entity_id=csv_export.id,
        action="export.intent_requested",
        actor="intent_agent",
        payload={
            "von": von.isoformat(),
            "bis": bis.isoformat(),
            "empfaenger_email": args.get("empfaenger_email"),
        },
    )
    return {
        "erstellt": True,
        "von": von.isoformat(),
        "bis": bis.isoformat(),
        "anzahl_buchungen": csv_export.anzahl_buchungen,
    }


# ---------------------------------------------------------------------------
# Router-Dict — kein Framework, einfaches Dict
# ---------------------------------------------------------------------------

TOOL_HANDLERS: dict = {
    "query_finanzen": _handle_query_finanzen,
    "get_euer": _handle_get_euer,
    "get_offene_rechnungen": _handle_get_offene_rechnungen,
    "create_export": _handle_create_export,
    # steuerberatung_grenze hat keinen DB-Handler — wird im handle()-Aufruf direkt abgefangen
}

# ---------------------------------------------------------------------------
# Antwort-Formulierung (Code-basiert — KEIN zweiter LLM-Call)
# ---------------------------------------------------------------------------


def _format_query_antwort(result: dict) -> str:
    von = result.get("von", "")
    bis = result.get("bis", "")
    zeitraum_text = f"{von} – {bis}" if von and bis else ""

    if result.get("vergleich"):
        z1 = result["zeitraum_1"]
        z2 = result["zeitraum_2"]
        diff = abs(result.get("differenz", 0))
        richtung = (
            "mehr"
            if result["differenz"] > 0
            else ("weniger" if result["differenz"] < 0 else "gleich viel")
        )
        return (
            f"Vergleich:\n"
            f"• {z1['von']} – {z1['bis']}: {_fmt_betrag(z1['summe'])} ({z1['anzahl']} Buchungen)\n"
            f"• {z2['von']} – {z2['bis']}: {_fmt_betrag(z2['summe'])} ({z2['anzahl']} Buchungen)\n"
            f"Differenz: {_fmt_betrag(diff)} {richtung} im ersten Zeitraum."
        )

    art = result.get("art", "")
    art_text = {"ausgabe": "Ausgaben", "einnahme": "Einnahmen", "beides": "Buchungen"}.get(art, art)
    aggregation = result.get("aggregation", "summe")

    if aggregation == "summe":
        return (
            f"{art_text} {zeitraum_text}: {_fmt_betrag(result['summe'])} "
            f"({result['anzahl']} Buchungen)"
        )

    if aggregation == "anzahl":
        return f"{result['anzahl']} {art_text.lower()} im Zeitraum {zeitraum_text}."

    if aggregation == "liste":
        if not result["eintraege"]:
            return f"Keine {art_text.lower()} im Zeitraum {zeitraum_text} gefunden."
        zeilen = [
            f"{art_text} {zeitraum_text} "
            f"(Top {len(result['eintraege'])} von {result['gesamt_gefunden']}):"
        ]
        for e in result["eintraege"]:
            zeilen.append(
                f"• {e['datum']} {e['haendler']}: {_fmt_betrag(e['betrag'])} ({e['kategorie']})"
            )
        return "\n".join(zeilen)

    if aggregation == "pro_kategorie":
        if not result["kategorien"]:
            return f"Keine {art_text.lower()} im Zeitraum {zeitraum_text} gefunden."
        zeilen = [f"{art_text} nach Kategorie ({zeitraum_text}):"]
        for k in result["kategorien"]:
            zeilen.append(f"• {k['kategorie']}: {_fmt_betrag(k['summe'])} ({k['anzahl']}×)")
        zeilen.append(f"Gesamt: {_fmt_betrag(result['gesamt'])}")
        return "\n".join(zeilen)

    if aggregation == "pro_monat":
        if not result["monate"]:
            return f"Keine {art_text.lower()} im Zeitraum {zeitraum_text} gefunden."
        zeilen = [f"{art_text} pro Monat ({zeitraum_text}):"]
        for m in result["monate"]:
            zeilen.append(f"• {m['monat']}: {_fmt_betrag(m['summe'])} ({m['anzahl']}×)")
        zeilen.append(f"Gesamt: {_fmt_betrag(result['gesamt'])}")
        return "\n".join(zeilen)

    return f"{art_text} {zeitraum_text}: keine Details verfügbar."


def _format_antwort(tool_name: str, result: dict) -> str:
    """Baut die Antwort-Nachricht aus dem Code-berechneten Ergebnis.

    Zahlen kommen AUS DEM result-DICT (DB-berechnet), nie aus dem LLM.
    """
    if "fehler" in result:
        frage = result.get("frage", "Kannst du deine Frage genauer stellen?")
        if result["fehler"] == "unbekannte_kategorie":
            return f"Die Kategorie '{result.get('kategorie_gesucht')}' kenne ich nicht. {frage}"
        return frage

    if tool_name == "get_euer":
        gewinn = result["gewinn"]
        vorzeichen = "+" if gewinn >= 0 else ""
        return (
            f"Dein Ergebnis {result['jahr']}:\n"
            f"Einnahmen:  {_fmt_betrag(result['einnahmen'])}\n"
            f"Ausgaben:   {_fmt_betrag(result['ausgaben'])}\n"
            f"Gewinn:     {vorzeichen}{_fmt_betrag(gewinn)}\n"
            f"({result['anzahl_buchungen']} Buchungen)"
        )

    if tool_name == "get_offene_rechnungen":
        if not result["offene_rechnungen"]:
            return "Keine offenen Rechnungen — alles beglichen! ✅"
        zeilen = [
            f"Offene Rechnungen ({result['anzahl']}), "
            f"Gesamt: {_fmt_betrag(result['gesamt_ausstehend'])}:"
        ]
        for r in result["offene_rechnungen"][:10]:
            status = " ⚠️ überfällig" if r.get("ueberfaellig") else ""
            faellig = f", fällig {r['faellig_am']}" if r.get("faellig_am") else ""
            zeilen.append(f"• {r['kunde']}: {_fmt_betrag(r['betrag'])}{faellig}{status}")
        return "\n".join(zeilen)

    if tool_name == "create_export":
        if result.get("erstellt"):
            return (
                f"Export für {result['von']} bis {result['bis']} wurde erstellt "
                f"({result['anzahl_buchungen']} Buchungen). "
                "Dein Steuerberater kann die Dateien abrufen."
            )
        return result.get("frage", "Für welchen Zeitraum soll ich exportieren?")

    if tool_name == "query_finanzen":
        return _format_query_antwort(result)

    return "Ich habe deine Frage verarbeitet."


# ---------------------------------------------------------------------------
# IntentService
# ---------------------------------------------------------------------------

_KEIN_TOOL_HINWEIS = (
    "Das kann ich dir leider nicht beantworten — ich beantworte ausschließlich "
    "Fragen zu deinen eigenen Buchhaltungsdaten. Zum Beispiel:\n"
    "• 'Was ist mein Gewinn dieses Jahr?'\n"
    "• 'Zeige meine Ausgaben im Oktober'\n"
    "• 'Offene Rechnungen?'\n"
    "• 'Export für meinen Steuerberater'"
)


class IntentService:
    """Verarbeitet Freitext-Fragen mit Mistral Function Calling.

    Das LLM wählt Tool + Parameter.
    Zahlen kommen IMMER aus Code (DB-Queries), nicht vom LLM.
    mandant_id kommt IMMER aus dem Server-Kontext, nie aus LLM-Args.
    Kein Framework — Tool-Router ist ein einfaches Dict.
    """

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        # Injizierbar für Tests (kein echter Netzwerk-Call)
        self._http_client = http_client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.mistral_api_key}",
            "Content-Type": "application/json",
        }

    async def _chat_complete(self, messages: list[dict]) -> dict:
        payload = {
            "model": settings.intent_model or settings.llm_model,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "temperature": 0.0,
            "max_tokens": 512,
        }
        if self._http_client is not None:
            resp = await self._http_client.post(
                f"{settings.mistral_api_base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{settings.mistral_api_base_url}/chat/completions",
                    json=payload,
                    headers=self._headers(),
                )
        resp.raise_for_status()
        return resp.json()

    async def handle(
        self,
        text: str,
        mandant_id: uuid.UUID,
        session: AsyncSession,
    ) -> str:
        """Verarbeitet eine Freitext-Frage und gibt eine auf Deutsch formulierte Antwort zurück.

        mandant_id kommt aus dem Aufruf-Kontext (Telefonnummer-Lookup im Webhook).
        LLM-gelieferte mandant_id-Argumente werden immer ignoriert.
        """
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]

        try:
            response = await self._chat_complete(messages)
        except Exception as exc:
            logger.error("Intent-LLM-Fehler: %s", exc)
            return (
                "Ich kann gerade leider keine Fragen beantworten. Bitte versuche es später nochmal."
            )

        choice = response.get("choices", [{}])[0]
        message = choice.get("message", {})
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            # Kein Tool-Call → themenfremde oder unklare Frage
            logger.debug("Intent: kein Tool-Call für Frage '%.50s'", text)
            return _KEIN_TOOL_HINWEIS

        tool_call = tool_calls[0]
        tool_name = tool_call.get("function", {}).get("name", "")
        args_raw = tool_call.get("function", {}).get("arguments", "{}")

        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
        except (ValueError, TypeError):
            args = {}

        # Mandanten-Isolation: LLM-geliefertes mandant_id IMMER entfernen
        args.pop("mandant_id", None)

        if tool_name == "steuerberatung_grenze":
            await append_audit(
                session,
                mandant_id=mandant_id,
                entity_type="intent",
                entity_id=None,
                action="intent.steuergrenze",
                actor="intent_agent",
                payload={"frage_laenge": len(text)},
            )
            return SAFE_STEUER_HINWEIS

        handler = TOOL_HANDLERS.get(tool_name)
        if handler is None:
            logger.warning("Intent: unbekanntes Tool '%s'", tool_name)
            return _KEIN_TOOL_HINWEIS

        try:
            result = await handler(args, mandant_id, session)
            antwort = _format_antwort(tool_name, result)
        except Exception as exc:
            logger.error("Intent-Handler-Fehler (%s): %s", tool_name, exc)
            return "Beim Abrufen deiner Daten ist ein Fehler aufgetreten."

        await append_audit(
            session,
            mandant_id=mandant_id,
            entity_type="intent",
            entity_id=None,
            action="intent.answered",
            actor="intent_agent",
            payload={"tool": tool_name, "frage_laenge": len(text)},
        )

        return antwort
