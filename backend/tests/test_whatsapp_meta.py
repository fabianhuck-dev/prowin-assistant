"""Tests für den Meta WhatsApp Cloud API Provider und Webhook.

Kein echter Netzwerk-Call: Meta-HTTP-Calls werden via unittest.mock gemockt.
Die Tests nutzen denselben In-Memory-SQLite wie die übrigen Tests (kein Docker).

Getestete Anforderungen:
  - Webhook-Verifizierung (GET): korrekter Token → Challenge+200; falscher → 403.
  - Signaturprüfung (POST): gültige HMAC → verarbeitet; ungültige → 403.
  - Idempotenz: dieselbe wamid zweimal → nur einmal verarbeitet.
  - Inbound Bild: Beleg im Storage, OCR+Klassifikation laufen, Vorschlags-Nachricht
    gesendet, KEINE Buchung (Compliance Regel 1).
  - Button-Bestätigung: button_reply.id = confirm:<beleg_id> → genau eine Buchung.
  - Confirm-Gate: kein Button → keine Buchung (T10-Äquivalent auf Webhook-Ebene).
  - Sofort-200: Webhook antwortet 200 unabhängig von der Verarbeitungsdauer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from app.db.models import Beleg, Buchung, WebhookEvent
from app.providers.llm.stub import StubLlmProvider
from app.providers.ocr.stub import StubOcrProvider
from app.providers.whatsapp.stub import StubWhatsAppProvider
from sqlalchemy import func, select

# ---------------------------------------------------------------------------
# Konstanten & Hilfsfunktionen
# ---------------------------------------------------------------------------

APP_SECRET = "test-app-secret-1234"
VERIFY_TOKEN = "test-verify-token-xyz"


def _sign(body: bytes, secret: str = APP_SECRET) -> str:
    """Erzeugt eine gültige X-Hub-Signature-256 wie Meta es täte."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _meta_headers(body: bytes) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": _sign(body),
    }


def _image_payload(wamid: str, from_phone: str, media_id: str = "media999") -> bytes:
    data = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "from": from_phone,
                                    "id": wamid,
                                    "timestamp": "1700000000",
                                    "type": "image",
                                    "image": {"id": media_id, "mime_type": "image/jpeg"},
                                }
                            ],
                            "contacts": [{"wa_id": from_phone, "profile": {"name": "Test User"}}],
                        }
                    }
                ]
            }
        ],
    }
    return json.dumps(data).encode()


def _button_payload(wamid: str, from_phone: str, btn_id: str) -> bytes:
    data = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "from": from_phone,
                                    "id": wamid,
                                    "timestamp": "1700000001",
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {"id": btn_id, "title": "Ja, buchen"},
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }
    return json.dumps(data).encode()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def inject_session_factory(session_factory):
    """Injiziert die Test-SessionFactory in den Webhook-Handler,
    damit Hintergrundaufgaben denselben In-Memory-SQLite nutzen."""
    import app.api.webhooks as wh

    original = wh._test_session_factory
    wh._test_session_factory = session_factory
    yield
    wh._test_session_factory = original


@pytest.fixture(autouse=True)
def patch_meta_settings(monkeypatch):
    """Setzt Meta-Einstellungen für alle Tests in dieser Datei."""
    monkeypatch.setattr("app.api.webhooks.settings.whatsapp_provider", "meta")
    monkeypatch.setattr("app.api.webhooks.settings.whatsapp_app_secret", APP_SECRET)
    monkeypatch.setattr("app.api.webhooks.settings.whatsapp_verify_token", VERIFY_TOKEN)
    # Provider-Factory auch patchen, damit get_whatsapp_provider() "meta" zurückgibt.
    monkeypatch.setattr("app.config.settings.whatsapp_provider", "meta")


@pytest.fixture
def stub_wa():
    """StubWhatsAppProvider als Ersatz für den echten Meta-Provider."""
    wa = StubWhatsAppProvider()
    with patch("app.api.webhooks.get_whatsapp_provider", return_value=wa):
        yield wa


# ---------------------------------------------------------------------------
# Webhook-Verifizierung (GET)
# ---------------------------------------------------------------------------


async def test_hub_challenge_correct_token(client):
    resp = await client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "987654321",
        },
    )
    assert resp.status_code == 200
    assert resp.text == "987654321"


async def test_hub_challenge_wrong_token(client):
    resp = await client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "falscher-token",
            "hub.challenge": "987654321",
        },
    )
    assert resp.status_code == 403


async def test_hub_challenge_missing_params(client):
    resp = await client.get("/webhooks/whatsapp")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# HMAC-Signaturprüfung (POST)
# ---------------------------------------------------------------------------


async def test_invalid_hmac_returns_403(client, mandant):
    body = _image_payload("wamid.hmac1", mandant.whatsapp_phone)
    resp = await client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=deadbeef",
        },
    )
    assert resp.status_code == 403


async def test_missing_hmac_returns_403(client, mandant):
    body = _image_payload("wamid.hmac2", mandant.whatsapp_phone)
    resp = await client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 403


async def test_valid_hmac_returns_200(client, mandant, stub_wa):
    body = _image_payload("wamid.hmac3", mandant.whatsapp_phone, media_id="tankquittung")
    with (
        patch("app.api.webhooks.get_ocr_provider", return_value=StubOcrProvider()),
        patch("app.api.webhooks.get_llm_provider", return_value=StubLlmProvider()),
    ):
        resp = await client.post(
            "/webhooks/whatsapp",
            content=body,
            headers=_meta_headers(body),
        )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Sofort-200 (Response VOR Verarbeitung)
# ---------------------------------------------------------------------------


async def test_sofort_200_unabhaengig_von_verarbeitungsdauer(client, mandant, stub_wa):
    """Webhook antwortet immer 200, auch wenn die Verarbeitung länger dauert.

    Im Test läuft der BackgroundTask synchron mit dem Request ab (ASGI-Transport),
    aber das Wichtige ist: der Response-Status ist 200 (nicht 202, nicht 503).
    """
    body = _image_payload("wamid.fast1", mandant.whatsapp_phone, media_id="tankquittung")
    with (
        patch("app.api.webhooks.get_ocr_provider", return_value=StubOcrProvider()),
        patch("app.api.webhooks.get_llm_provider", return_value=StubLlmProvider()),
    ):
        resp = await client.post(
            "/webhooks/whatsapp",
            content=body,
            headers=_meta_headers(body),
        )
    assert resp.status_code == 200
    assert resp.content == b""  # kein Body — Meta erwartet leere 200-Antwort


# ---------------------------------------------------------------------------
# Idempotenz (doppelte wamid)
# ---------------------------------------------------------------------------


async def test_duplicate_wamid_only_one_beleg(client, mandant, stub_wa, session):
    body = _image_payload("wamid.dup1", mandant.whatsapp_phone, media_id="tankquittung")
    headers = _meta_headers(body)

    with (
        patch("app.api.webhooks.get_ocr_provider", return_value=StubOcrProvider()),
        patch("app.api.webhooks.get_llm_provider", return_value=StubLlmProvider()),
    ):
        resp1 = await client.post("/webhooks/whatsapp", content=body, headers=headers)
        resp2 = await client.post("/webhooks/whatsapp", content=body, headers=headers)

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    # Nur ein Beleg, obwohl dieselbe wamid zweimal empfangen wurde.
    beleg_count = (await session.execute(select(func.count()).select_from(Beleg))).scalar_one()
    assert beleg_count == 1

    # Nur ein WebhookEvent-Eintrag für diese wamid.
    event_count = (
        await session.execute(
            select(func.count()).select_from(WebhookEvent).where(WebhookEvent.wamid == "wamid.dup1")
        )
    ).scalar_one()
    assert event_count == 1


# ---------------------------------------------------------------------------
# Inbound Bild → Beleg + kein Auto-Buchen (Compliance Regel 1)
# ---------------------------------------------------------------------------


async def test_inbound_image_creates_beleg_no_buchung(
    client, mandant, stub_wa, session, seed_kategorien
):
    """Eingehendes Bild: Beleg wird gespeichert, OCR+Klassifikation laufen,
    Vorschlags-Nachricht mit Buttons wird gesendet — aber KEINE Buchung."""
    body = _image_payload("wamid.img1", mandant.whatsapp_phone, media_id="tankquittung")

    with (
        patch("app.api.webhooks.get_ocr_provider", return_value=StubOcrProvider()),
        patch("app.api.webhooks.get_llm_provider", return_value=StubLlmProvider()),
    ):
        resp = await client.post(
            "/webhooks/whatsapp",
            content=body,
            headers=_meta_headers(body),
        )

    assert resp.status_code == 200

    # Beleg ist im Storage + DB.
    beleg_count = (await session.execute(select(func.count()).select_from(Beleg))).scalar_one()
    assert beleg_count == 1

    # Compliance Regel 1: KEINE Buchung durch Ingest/OCR/LLM.
    buchung_count = (await session.execute(select(func.count()).select_from(Buchung))).scalar_one()
    assert buchung_count == 0

    # Vorschlags-Nachricht mit Buttons wurde gesendet.
    assert len(stub_wa.sent) >= 1
    last_msg = stub_wa.sent[-1]
    assert last_msg.buttons is not None
    assert any("confirm:" in btn["id"] for btn in last_msg.buttons)


async def test_inbound_image_beleg_in_memory_storage(
    client, mandant, stub_wa, session, memory_storage
):
    """Prüft, dass das Bild tatsächlich im write-once Storage landet."""
    body = _image_payload("wamid.store1", mandant.whatsapp_phone, media_id="wareneinkauf")

    with (
        patch("app.api.webhooks.get_ocr_provider", return_value=StubOcrProvider()),
        patch("app.api.webhooks.get_llm_provider", return_value=StubLlmProvider()),
    ):
        await client.post("/webhooks/whatsapp", content=body, headers=_meta_headers(body))

    # Beleg-Datei existiert im Storage.
    beleg = (await session.execute(select(Beleg))).scalar_one_or_none()
    assert beleg is not None
    stored = await memory_storage.get(beleg.storage_key)
    assert stored == b"STUB-IMAGE:wareneinkauf"


# ---------------------------------------------------------------------------
# Button-Bestätigung → Buchung (Compliance Regel 1 positiv)
# ---------------------------------------------------------------------------


async def test_button_confirm_creates_exactly_one_buchung(
    client, mandant, stub_wa, session, seed_kategorien
):
    """button_reply.id = confirm:<beleg_id> → genau eine Buchung (Confirm-Gate)."""
    from app.services.classification import ingest_beleg, klassifiziere_beleg, run_ocr

    # Beleg vorbereiten (ohne Webhook, direkt via Service).
    data = b"STUB-IMAGE:tankquittung"
    beleg, _ = await ingest_beleg(
        session,
        mandant_id=mandant.id,
        data=data,
        filename="tank.jpg",
        mime_type="image/jpeg",
        quelle="whatsapp",
    )
    ocr = await run_ocr(session, beleg, data, StubOcrProvider())
    await klassifiziere_beleg(session, beleg.id, ocr, StubLlmProvider())
    await session.commit()

    # Button-Bestätigung via Meta-Webhook.
    btn_id = f"confirm:{beleg.id}"
    body = _button_payload("wamid.btn1", mandant.whatsapp_phone, btn_id)

    resp = await client.post("/webhooks/whatsapp", content=body, headers=_meta_headers(body))
    assert resp.status_code == 200

    buchung_count = (await session.execute(select(func.count()).select_from(Buchung))).scalar_one()
    assert buchung_count == 1

    # Bestätigungs-Nachricht gesendet.
    assert any("Gebucht" in msg.text for msg in stub_wa.sent)


# ---------------------------------------------------------------------------
# Confirm-Gate: ohne Button keine Buchung (T10-Äquivalent auf Webhook-Ebene)
# ---------------------------------------------------------------------------


async def test_confirm_gate_no_buchung_without_button(
    client, mandant, stub_wa, session, seed_kategorien
):
    """Compliance Regel 1 auf Webhook-Ebene: Nur ein Button-Klick erzeugt eine Buchung.
    Ein eingehendes Bild erzeugt NIEMALS automatisch eine Buchung — egal wie hoch
    die Confidence ist."""
    body = _image_payload("wamid.gate1", mandant.whatsapp_phone, media_id="tankquittung")

    with (
        patch("app.api.webhooks.get_ocr_provider", return_value=StubOcrProvider()),
        patch("app.api.webhooks.get_llm_provider", return_value=StubLlmProvider()),
    ):
        resp = await client.post(
            "/webhooks/whatsapp",
            content=body,
            headers=_meta_headers(body),
        )

    assert resp.status_code == 200

    buchung_count = (await session.execute(select(func.count()).select_from(Buchung))).scalar_one()
    assert buchung_count == 0


# ---------------------------------------------------------------------------
# MetaWhatsAppProvider Unit-Tests (kein Netzwerk-Call)
# ---------------------------------------------------------------------------


async def test_meta_provider_send_text(monkeypatch):
    """send_message baut korrekten Text-Payload und gibt wamid zurück."""
    from app.providers.whatsapp.base import OutboundMessage
    from app.providers.whatsapp.meta import MetaWhatsAppProvider

    monkeypatch.setattr("app.providers.whatsapp.meta.settings.whatsapp_access_token", "tok")
    monkeypatch.setattr("app.providers.whatsapp.meta.settings.whatsapp_phone_number_id", "pid")
    monkeypatch.setattr("app.providers.whatsapp.meta.settings.whatsapp_graph_version", "v21.0")

    sent_payloads: list[dict] = []

    async def _mock_request_with_retry(client, method, url, **kwargs):
        sent_payloads.append(kwargs.get("json", {}))
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"messages": [{"id": "wamid.sent1"}]}
        return mock_resp

    with patch("app.providers.whatsapp.meta._request_with_retry", _mock_request_with_retry):
        provider = MetaWhatsAppProvider()
        wamid = await provider.send_message(OutboundMessage(to="+4915100000000", text="Hallo!"))

    assert wamid == "wamid.sent1"
    assert sent_payloads[0]["type"] == "text"
    assert sent_payloads[0]["text"]["body"] == "Hallo!"


async def test_meta_provider_send_interactive_buttons(monkeypatch):
    """send_message baut korrekten Interactive-Button-Payload."""
    from app.providers.whatsapp.base import OutboundMessage
    from app.providers.whatsapp.meta import MetaWhatsAppProvider

    monkeypatch.setattr("app.providers.whatsapp.meta.settings.whatsapp_access_token", "tok")
    monkeypatch.setattr("app.providers.whatsapp.meta.settings.whatsapp_phone_number_id", "pid")
    monkeypatch.setattr("app.providers.whatsapp.meta.settings.whatsapp_graph_version", "v21.0")

    sent_payloads: list[dict] = []

    async def _mock_request_with_retry(client, method, url, **kwargs):
        sent_payloads.append(kwargs.get("json", {}))
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"messages": [{"id": "wamid.btn2"}]}
        return mock_resp

    with patch("app.providers.whatsapp.meta._request_with_retry", _mock_request_with_retry):
        provider = MetaWhatsAppProvider()
        await provider.send_message(
            OutboundMessage(
                to="+4915100000000",
                text="Buchen?",
                buttons=[
                    {"id": "confirm:abc", "title": "Ja, buchen"},
                    {"id": "reject:abc", "title": "Nein"},
                ],
            )
        )

    payload = sent_payloads[0]
    assert payload["type"] == "interactive"
    buttons = payload["interactive"]["action"]["buttons"]
    assert len(buttons) == 2
    assert buttons[0]["reply"]["id"] == "confirm:abc"


async def test_meta_provider_download_media_two_step(monkeypatch):
    """download_media führt zwei HTTP-Requests durch: media_id → URL → Bytes."""
    from app.providers.whatsapp.meta import MetaWhatsAppProvider

    monkeypatch.setattr("app.providers.whatsapp.meta.settings.whatsapp_access_token", "tok")
    monkeypatch.setattr("app.providers.whatsapp.meta.settings.whatsapp_phone_number_id", "pid")
    monkeypatch.setattr("app.providers.whatsapp.meta.settings.whatsapp_graph_version", "v21.0")

    call_count = 0
    media_url = "https://lookaside.fbsbx.com/whatsapp_business/attachments/?id=12345"

    async def _mock_request_with_retry(client, method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        if call_count == 1:
            # Schritt 1: media_id → URL
            mock_resp.json.return_value = {"url": media_url}
        else:
            # Schritt 2: URL → Bytes
            mock_resp.content = b"BILD-BYTES"
        return mock_resp

    with patch("app.providers.whatsapp.meta._request_with_retry", _mock_request_with_retry):
        provider = MetaWhatsAppProvider()
        result = await provider.download_media("media-id-xyz")

    assert result == b"BILD-BYTES"
    assert call_count == 2


# ---------------------------------------------------------------------------
# Button-Reject → Beleg verworfen, keine Buchung
# ---------------------------------------------------------------------------


async def test_button_reject_no_buchung(client, mandant, stub_wa, session, seed_kategorien):
    """reject:<beleg_id> → Beleg status=verworfen, keine Buchung."""
    from app.services.classification import ingest_beleg, klassifiziere_beleg, run_ocr

    data = b"STUB-IMAGE:tankquittung"
    beleg, _ = await ingest_beleg(
        session,
        mandant_id=mandant.id,
        data=data,
        filename="tank2.jpg",
        mime_type="image/jpeg",
        quelle="whatsapp",
    )
    ocr = await run_ocr(session, beleg, data, StubOcrProvider())
    await klassifiziere_beleg(session, beleg.id, ocr, StubLlmProvider())
    await session.commit()

    btn_id = f"reject:{beleg.id}"
    body = _button_payload("wamid.reject1", mandant.whatsapp_phone, btn_id)

    resp = await client.post("/webhooks/whatsapp", content=body, headers=_meta_headers(body))
    assert resp.status_code == 200

    buchung_count = (await session.execute(select(func.count()).select_from(Buchung))).scalar_one()
    assert buchung_count == 0

    await session.refresh(beleg)
    assert beleg.status == "verworfen"
