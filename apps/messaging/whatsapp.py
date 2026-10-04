"""WhatsApp through the local gateway (whatsapp-gateway/server.js, whatsapp-web.js engine)."""

import base64
import hashlib
import hmac
import logging

import httpx
from django.conf import settings

from .providers import ProviderResult

logger = logging.getLogger(__name__)

STATE_LABELS = {
    "ready": "متصل",
    "qr": "مستني مسح الـQR",
    "starting": "بيفتح...",
    "connecting": "بيتصل...",
    "disconnected": "مش متصل",
    "error": "فيه مشكلة",
    "offline": "بوابة الواتساب مش شغالة",
}

ERRORS = {
    "not_on_whatsapp": "الرقم ده مش عليه واتساب.",
    "not_ready": "الواتساب مش متصل. اربطه من الإعدادات.",
    "bad_number": "رقم الموبايل مش صحيح.",
    "send_unconfirmed": "واتساب مأكدش الإرسال. بص على المحادثة قبل ما تعيد، عشان متتبعتش مرتين.",
}


def _client(timeout=15.0) -> httpx.Client:
    return httpx.Client(
        base_url=settings.WA_GATEWAY_URL,
        headers={"X-Api-Key": settings.WA_GATEWAY_KEY},
        timeout=timeout,
        transport=getattr(settings, "WA_GATEWAY_TRANSPORT", None),  # tests inject httpx.MockTransport
    )


def session_id(org) -> str:
    return str(org.pk)


def status(org) -> dict:
    """{'state': ..., 'qr': data-uri or None, 'me': '2010...' or None}; state 'offline' if the gateway is down."""
    if not settings.WA_GATEWAY_KEY:
        return {"state": "offline", "qr": None, "me": None}
    try:
        with _client(timeout=3.0) as c:
            r = c.get(f"/sessions/{session_id(org)}")
            r.raise_for_status()
            return r.json()
    except (httpx.HTTPError, ValueError):
        return {"state": "offline", "qr": None, "me": None}


def start(org) -> dict:
    with _client() as c:
        return c.post(f"/sessions/{session_id(org)}/start").json()


def logout(org) -> dict:
    with _client(timeout=30.0) as c:
        return c.post(f"/sessions/{session_id(org)}/logout").json()


def to_wa_number(e164: str) -> str:
    return "".join(ch for ch in e164 if ch.isdigit())


class WhatsAppProvider:
    def __init__(self, org):
        self.org = org

    def _post_send(self, payload) -> ProviderResult:
        try:
            with _client(timeout=90.0) as c:
                r = c.post(f"/sessions/{session_id(self.org)}/send", json=payload)
        except httpx.HTTPError as e:
            return ProviderResult(ok=False, error=f"بوابة الواتساب مش بترد: {e}", temporary=True)
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code == 200:
            # id can be null when WhatsApp accepted the message but didn't expose its key (no ✓✓ tracking).
            return ProviderResult(ok=True, provider_message_id=data.get("id") or "")
        code = data.get("error", "")
        temporary = code in ("send_failed", "not_ready") or r.status_code >= 500
        detail = data.get("detail", "")
        return ProviderResult(
            ok=False, error=ERRORS.get(code, f"فشل الإرسال بالواتساب: {code} {detail}".strip()), temporary=temporary
        )

    def send(self, delivery, pdf_bytes: bytes, pdf_name: str) -> ProviderResult:
        return self._post_send(
            {
                "to": to_wa_number(delivery.recipient),
                "caption": delivery.message_text,
                "filename": pdf_name,
                "pdf_base64": base64.b64encode(pdf_bytes).decode(),
            }
        )

    def send_text(self, to_e164: str, text: str) -> ProviderResult:
        return self._post_send({"to": to_wa_number(to_e164), "caption": text})


def is_registered(org, e164: str) -> bool | None:
    """True/False from the gateway's `isRegisteredUser`; None when the gateway can't tell (down / not linked)."""
    try:
        with _client(timeout=20.0) as c:
            r = c.get(f"/sessions/{session_id(org)}/check/{to_wa_number(e164)}")
        if r.status_code != 200:
            return None
        return bool(r.json().get("registered"))
    except (httpx.HTTPError, ValueError):
        return None


def verify_signature(body: bytes, signature: str) -> bool:
    secret = (getattr(settings, "WA_WEBHOOK_SECRET", "") or settings.WA_GATEWAY_KEY).encode()
    if not secret or not signature:
        return False
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
