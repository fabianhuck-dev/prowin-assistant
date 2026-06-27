"""WhatsApp-Webhooks.

Compliance Regel 1: POST /webhooks/whatsapp erzeugt NIEMALS eine Buchung.
Der einzige Buchungspfad ist POST /webhooks/whatsapp/confirm (nach Button-Klick).

GET  /webhooks/whatsapp         → Meta Hub-Verifizierung (hub.challenge).
POST /webhooks/whatsapp         → Eingehende Nachrichten.
                                  WHATSAPP_PROVIDER=meta: Signaturprüfung + sofort 200
                                    + Hintergrundverarbeitung.
                                  WHATSAPP_PROVIDER=stub: Direkt-Verarbeitung (Tests).
POST /webhooks/whatsapp/confirm → EINZIGER Buchungspfad (nach expliziter Button-Bestätigung).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.db.base import SessionFactory, get_session
from app.db.models import Beleg, Mandant, WebhookEvent
from app.providers import get_llm_provider, get_ocr_provider, get_whatsapp_provider
from app.providers.whatsapp.base import OutboundMessage
from app.services.classification import ingest_beleg, klassifiziere_beleg, run_ocr
from app.services.confirmation import bestaetige_und_buche

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger("prowin.webhooks")

# Für Tests überschreibbar: Tests injizieren ihre In-Memory-SessionFactory hier,
# damit Hintergrundaufgaben nicht versuchen, PostgreSQL zu erreichen.
_test_session_factory: async_sessionmaker | None = None


def _active_session_factory() -> async_sessionmaker:
    return _test_session_factory or SessionFactory


# ---------------------------------------------------------------------------
# Pydantic-Schemas
# ---------------------------------------------------------------------------


class WhatsAppConfirm(BaseModel):
    beleg_id: uuid.UUID
    mandant_id: uuid.UUID
    bestaetigt_via: str = "whatsapp_button"
    korrekturen: dict | None = None


# ---------------------------------------------------------------------------
# HMAC-Signaturprüfung (Meta App Secret)
# ---------------------------------------------------------------------------


def _verify_hmac(raw_body: bytes, sig_header: str) -> bool:
    """Prüft X-Hub-Signature-256 gegen WHATSAPP_APP_SECRET.

    Gibt False zurück wenn kein App-Secret konfiguriert ist oder die Signatur
    ungültig ist. Timing-safe via hmac.compare_digest.
    """
    secret = settings.whatsapp_app_secret
    if not secret:
        return False
    if not sig_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    received = sig_header[7:]
    return hmac.compare_digest(expected, received)


# ---------------------------------------------------------------------------
# GET: Meta Hub-Verifizierung
# ---------------------------------------------------------------------------


@router.get("/whatsapp")
async def whatsapp_verify(request: Request) -> Response:
    """Webhook-Verifizierung durch Meta (hub.challenge Echo).

    Meta ruft diesen Endpoint auf, wenn du im Developer-Dashboard einen Webhook
    registrierst. Der korrekte Verify-Token muss mit WHATSAPP_VERIFY_TOKEN
    übereinstimmen; bei Erfolg wird hub.challenge als Plaintext zurückgegeben.
    """
    params = request.query_params
    hub_mode = params.get("hub.mode")
    hub_verify_token = params.get("hub.verify_token")
    hub_challenge = params.get("hub.challenge")
    if (
        hub_mode == "subscribe"
        and hub_verify_token
        and hub_verify_token == settings.whatsapp_verify_token
        and hub_challenge
    ):
        return PlainTextResponse(hub_challenge, status_code=200)
    return Response(status_code=403)


# ---------------------------------------------------------------------------
# POST: Eingehende Nachrichten
# ---------------------------------------------------------------------------


@router.post("/whatsapp")
async def whatsapp_inbound(request: Request, background_tasks: BackgroundTasks) -> Response:
    """Empfängt eingehende WhatsApp-Nachrichten.

    Meta-Modus (WHATSAPP_PROVIDER=meta):
      1. Signaturprüfung (X-Hub-Signature-256).
      2. Sofort HTTP 200 antworten — Meta retried sonst bis zu 7 Tage.
      3. Verarbeitung als BackgroundTask (OCR/LLM dauern länger als Meta's Timeout).

    Stub-Modus (WHATSAPP_PROVIDER=stub):
      Direkte synchrone Verarbeitung mit vereinfachtem Payload-Format (für Tests).
    """
    raw_body = await request.body()

    if settings.whatsapp_provider == "meta":
        sig = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_hmac(raw_body, sig):
            return Response(status_code=403)
        background_tasks.add_task(_process_meta_payload, raw_body)
        return Response(status_code=200)

    # Stub-Modus: vereinfachtes JSON-Format für Tests und lokale Entwicklung.
    try:
        payload_dict = json.loads(raw_body)
        from app.providers.whatsapp.base import InboundMessage

        inbound = InboundMessage(
            phone=payload_dict["phone"],
            message_type=payload_dict.get("message_type", "image"),
            media_url=payload_dict.get("media_url"),
            text=payload_dict.get("text"),
            message_id=payload_dict["message_id"],
        )
    except (KeyError, ValueError, TypeError):
        return Response(status_code=400)

    async with _active_session_factory()() as session:
        result = await _handle_stub_inbound(inbound, session)
        await session.commit()
    return result


# ---------------------------------------------------------------------------
# Meta-Webhook-Verarbeitung (Hintergrund)
# ---------------------------------------------------------------------------


async def _process_meta_payload(raw_body: bytes) -> None:
    """Parst einen Meta-Webhook-Payload und verteilt auf die Nachrichten-Handler."""
    try:
        data = json.loads(raw_body)
    except ValueError:
        logger.error("Ungültiger JSON-Payload vom Meta-Webhook")
        return

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for raw_msg in value.get("messages", []):
                try:
                    await _handle_meta_message(raw_msg)
                except Exception:
                    logger.exception("Unbehandelte Ausnahme bei wamid=%s", raw_msg.get("id", "?"))


async def _handle_meta_message(raw_msg: dict) -> None:
    """Verarbeitet eine einzelne eingehende Meta-Nachricht.

    Jede Nachricht läuft in ihrer eigenen Session. Der Commit erfolgt nach allen
    DB-Änderungen; WhatsApp-Antworten werden erst danach gesendet (best-effort).
    Belege im Object-Storage sind durch write-once-Semantik immer GoBD-sicher.
    """
    wamid = raw_msg.get("id", "")
    from_phone = raw_msg.get("from", "")
    msg_type = raw_msg.get("type", "")

    reply: OutboundMessage | None = None

    async with _active_session_factory()() as session:
        # Idempotenz: wamid schon gesehen → überspringen.
        existing = await session.scalar(select(WebhookEvent).where(WebhookEvent.wamid == wamid))
        if existing is not None:
            logger.debug("wamid %s bereits verarbeitet — skip", wamid)
            return

        session.add(WebhookEvent(wamid=wamid))

        # Mandant aus Telefonnummer auflösen.
        mandant = await session.scalar(select(Mandant).where(Mandant.whatsapp_phone == from_phone))
        if mandant is None:
            logger.info("Unbekannte Nummer %s — minimaler Onboarding-Hinweis", from_phone)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return
            wa = get_whatsapp_provider()
            await wa.send_message(
                OutboundMessage(
                    to=from_phone,
                    text=(
                        "Willkommen beim ProWin-Assistenten! "
                        "Bitte wenden Sie sich an Ihren Administrator, "
                        "um Ihren Account einzurichten."
                    ),
                )
            )
            return

        wa = get_whatsapp_provider()

        if msg_type in ("image", "document"):
            reply = await _handle_media_message(session, mandant, raw_msg, msg_type)
        elif msg_type == "interactive":
            reply = await _handle_button_reply(session, mandant, raw_msg)
        elif msg_type == "text":
            reply = await _handle_text_message(session, mandant, raw_msg)
        else:
            logger.debug("Unbekannter Nachrichtentyp: %s (wamid=%s)", msg_type, wamid)

        try:
            await session.commit()
        except IntegrityError:
            # Race condition: anderer Prozess hat dieselbe wamid parallel committed.
            await session.rollback()
            return

    # Antwort erst nach Commit senden (best-effort — Fehler hier verlieren keine Buchungsdaten).
    if reply is not None:
        try:
            await wa.send_message(reply)
        except Exception:
            logger.error(
                "WhatsApp-Antwort konnte nicht gesendet werden (wamid=%s, to=%s)",
                wamid,
                reply.to,
            )


async def _handle_media_message(
    session: AsyncSession, mandant: Mandant, raw_msg: dict, msg_type: str
) -> OutboundMessage | None:
    """Lädt Medium herunter, ingested Beleg, führt OCR+Klassifikation durch.

    Gibt die zu sendende Antwort-Nachricht zurück — sendet NICHT selbst.
    Erzeugt KEINE Buchung (Compliance Regel 1).
    """
    media_info = raw_msg.get(msg_type, {})
    media_id = media_info.get("id", "")
    mime_type = media_info.get("mime_type", "image/jpeg")
    filename = media_info.get("filename") or f"{raw_msg.get('id', 'beleg')}.jpg"

    wa = get_whatsapp_provider()

    try:
        data = await wa.download_media(media_id)
    except Exception as exc:
        logger.error("Media-Download fehlgeschlagen (media_id=%s): %s", media_id, exc)
        return OutboundMessage(
            to=mandant.whatsapp_phone,
            text="Ich konnte den Beleg nicht herunterladen. Bitte sende ihn erneut.",
        )

    beleg, is_duplicate = await ingest_beleg(
        session,
        mandant_id=mandant.id,
        data=data,
        filename=filename,
        mime_type=mime_type,
        quelle="whatsapp",
    )

    if is_duplicate:
        return OutboundMessage(
            to=mandant.whatsapp_phone,
            text="Diesen Beleg habe ich bereits erhalten.",
        )

    ocr_result = await run_ocr(session, beleg, data, get_ocr_provider())
    if ocr_result is None:
        return OutboundMessage(
            to=mandant.whatsapp_phone,
            text="Ich konnte den Beleg nicht lesen. Bitte sende ihn erneut oder schärfer.",
        )

    vorschlag = await klassifiziere_beleg(session, beleg.id, ocr_result, get_llm_provider())

    if vorschlag.rueckfrage_text:
        return OutboundMessage(to=mandant.whatsapp_phone, text=vorschlag.rueckfrage_text)

    # Vorschlags-Nachricht mit Reply-Buttons — KEINE Buchung (Compliance Regel 1).
    text = (
        f"Beleg erkannt: {vorschlag.belegtyp}, {vorschlag.betrag} EUR "
        f"({vorschlag.kategorie_vorschlag}). Buchen?"
    )
    if beleg.plausi_warnung:
        text += f"\n⚠ Hinweis: {beleg.plausi_warnung}"

    return OutboundMessage(
        to=mandant.whatsapp_phone,
        text=text,
        buttons=[
            {"id": f"confirm:{beleg.id}", "title": "Ja, buchen"},
            {"id": f"reject:{beleg.id}", "title": "Nein"},
        ],
    )


async def _handle_button_reply(
    session: AsyncSession, mandant: Mandant, raw_msg: dict
) -> OutboundMessage | None:
    """Verarbeitet einen interaktiven Button-Reply.

    confirm:<beleg_id> → bestaetige_und_buche() → EINZIGER Buchungspfad.
    reject:<beleg_id>  → Beleg auf status=verworfen setzen.
    """
    btn = raw_msg.get("interactive", {}).get("button_reply", {})
    btn_id = btn.get("id", "")

    if btn_id.startswith("confirm:"):
        beleg_id_str = btn_id[len("confirm:") :]
        try:
            beleg_id = uuid.UUID(beleg_id_str)
        except ValueError:
            return OutboundMessage(to=mandant.whatsapp_phone, text="Ungültige Beleg-ID.")

        try:
            buchung = await bestaetige_und_buche(
                session,
                beleg_id=beleg_id,
                mandant_id=mandant.id,
                bestaetigt_via="whatsapp_button",
                korrekturen=None,
            )
        except ValueError as exc:
            return OutboundMessage(to=mandant.whatsapp_phone, text=f"Buchung nicht möglich: {exc}")

        return OutboundMessage(
            to=mandant.whatsapp_phone,
            text=(
                f"Gebucht: {buchung.typ} {buchung.betrag} EUR am {buchung.datum.isoformat()}. ✅"
            ),
        )

    if btn_id.startswith("reject:"):
        beleg_id_str = btn_id[len("reject:") :]
        try:
            beleg_id = uuid.UUID(beleg_id_str)
            beleg = await session.get(Beleg, beleg_id)
            if beleg and beleg.mandant_id == mandant.id:
                beleg.status = "verworfen"
        except ValueError:
            pass
        return OutboundMessage(
            to=mandant.whatsapp_phone,
            text="Beleg verworfen. Sende jederzeit einen neuen Beleg.",
        )

    return None


async def _handle_text_message(
    session: AsyncSession, mandant: Mandant, raw_msg: dict
) -> OutboundMessage | None:
    """Verarbeitet eingehende Textnachricht.

    Offene Rückfragen zu einem Beleg haben Vorrang. Sonst → IntentService
    (Freitext-Fragen zu eigenen Buchhaltungsdaten).
    Ohne konfigurierten Mistral-Key fällt der Agent auf einen Hinweis zurück.
    """
    from app.db.models import Rueckfrage
    from app.services.intent import IntentService

    text = (raw_msg.get("text") or {}).get("body", "").strip()

    offene_rückfrage = await session.scalar(
        select(Rueckfrage).where(
            Rueckfrage.mandant_id == mandant.id,
            Rueckfrage.status == "offen",
        )
    )
    if offene_rückfrage is not None:
        # Rückfrage-Auflösung: Text als Antwort auf die offene Rückfrage interpretieren.
        # Der Intent-Layer beantwortet hier keine Datenfrage, sondern nimmt die Antwort
        # entgegen und könnte die Klassifikation verfeinern (Erweiterungspunkt).
        pass

    if not settings.mistral_api_key:
        return OutboundMessage(
            to=mandant.whatsapp_phone,
            text="Sende mir ein Foto deines Belegs, und ich kümmere mich darum!",
        )

    antwort = await IntentService().handle(text, mandant.id, session)
    return OutboundMessage(to=mandant.whatsapp_phone, text=antwort)


# ---------------------------------------------------------------------------
# Stub-Modus: vereinfachte Direktverarbeitung (kein Meta-Format)
# ---------------------------------------------------------------------------


async def _handle_stub_inbound(inbound, session: AsyncSession) -> Response:
    """Stub-Verarbeitung: gleiche Logik wie Meta, aber ohne Signaturprüfung/BackgroundTask.

    Wird nur bei WHATSAPP_PROVIDER=stub verwendet (lokale Entwicklung + Tests).
    """
    from app.providers.whatsapp.base import InboundMessage

    assert isinstance(inbound, InboundMessage)

    mandant = await session.scalar(select(Mandant).where(Mandant.whatsapp_phone == inbound.phone))
    if mandant is None:
        raise HTTPException(status_code=404, detail="Unbekannte Telefonnummer")

    wa = get_whatsapp_provider()

    if inbound.message_type not in ("image", "document") or not inbound.media_url:
        await wa.send_message(
            OutboundMessage(to=inbound.phone, text="Bitte sende ein Foto deines Belegs.")
        )
        return Response(
            content=json.dumps({"status": "ignored", "reason": "kein Beleg-Medium"}),
            media_type="application/json",
        )

    data = await wa.download_media(inbound.media_url)
    beleg, is_duplicate = await ingest_beleg(
        session,
        mandant_id=mandant.id,
        data=data,
        filename=f"{inbound.message_id}.jpg",
        mime_type="image/jpeg",
        quelle="whatsapp",
    )
    if is_duplicate:
        await wa.send_message(
            OutboundMessage(
                to=inbound.phone,
                text="Diesen Beleg habe ich bereits erhalten.",
            )
        )
        return Response(
            content=json.dumps({"status": "duplicate", "beleg_id": str(beleg.id)}),
            media_type="application/json",
        )

    ocr_result = await run_ocr(session, beleg, data, get_ocr_provider())
    if ocr_result is None:
        await wa.send_message(
            OutboundMessage(
                to=inbound.phone,
                text="Ich konnte den Beleg leider nicht lesen. Bitte sende ihn erneut.",
            )
        )
        return Response(
            content=json.dumps({"status": "ocr_failed", "beleg_id": str(beleg.id)}),
            media_type="application/json",
        )

    vorschlag = await klassifiziere_beleg(session, beleg.id, ocr_result, get_llm_provider())

    if vorschlag.rueckfrage_text:
        await wa.send_message(OutboundMessage(to=inbound.phone, text=vorschlag.rueckfrage_text))
        return Response(
            content=json.dumps(
                {
                    "status": "rueckfrage",
                    "beleg_id": str(beleg.id),
                    "vorschlag": vorschlag.model_dump(),
                }
            ),
            media_type="application/json",
        )

    text = (
        f"Beleg erkannt: {vorschlag.belegtyp}, {vorschlag.betrag} EUR "
        f"({vorschlag.kategorie_vorschlag}). Buchen?"
    )
    if beleg.plausi_warnung:
        text += f"\n⚠ Hinweis: {beleg.plausi_warnung}"

    await wa.send_message(
        OutboundMessage(
            to=inbound.phone,
            text=text,
            buttons=[
                {"id": f"confirm:{beleg.id}", "title": "Ja, buchen"},
                {"id": f"reject:{beleg.id}", "title": "Nein"},
            ],
        )
    )
    return Response(
        content=json.dumps(
            {
                "status": "vorschlag",
                "beleg_id": str(beleg.id),
                "vorschlag": vorschlag.model_dump(),
                "plausi_warnung": beleg.plausi_warnung,
            }
        ),
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# POST /confirm — EINZIGER Buchungspfad
# ---------------------------------------------------------------------------


@router.post("/whatsapp/confirm")
async def whatsapp_confirm(
    payload: WhatsAppConfirm, session: AsyncSession = Depends(get_session)
) -> dict:
    """EINZIGER Buchungspfad aus WhatsApp — nur nach expliziter Button-Bestätigung.

    Compliance Regel 1: Nur dieser Endpoint erzeugt Buchungen. Kein anderer
    Codepfad (Ingest, OCR, LLM, Webhook) schreibt jemals in die buchung-Tabelle.
    """
    try:
        buchung = await bestaetige_und_buche(
            session,
            beleg_id=payload.beleg_id,
            mandant_id=payload.mandant_id,
            bestaetigt_via=payload.bestaetigt_via,
            korrekturen=payload.korrekturen,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()

    mandant = await session.get(Mandant, payload.mandant_id)
    if mandant is not None:
        await get_whatsapp_provider().send_message(
            OutboundMessage(
                to=mandant.whatsapp_phone,
                text=(
                    f"Gebucht: {buchung.typ} {buchung.betrag} EUR "
                    f"am {buchung.datum.isoformat()}. ✅"
                ),
            )
        )
    return {"status": "gebucht", "buchung_id": str(buchung.id)}
