import hashlib
import hmac
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from django.conf import settings as dj_settings
from django.core import mail
from django.core.management import call_command
from django.urls import reverse

from apps.catalog.models import Product
from apps.customers.models import Contact, ContactChannel, Customer
from apps.messaging import services, whatsapp
from apps.messaging.models import ChannelKind, Delivery, DeliveryStatus, SendingChannelConfig
from apps.quotations import services as qservices
from apps.quotations.models import QuotationStatus

SEED = Path(dj_settings.BASE_DIR) / "docs" / "plan" / "seed" / "products.csv"


class FakeGateway:
    """In-memory stand-in for whatsapp-gateway/server.js."""

    def __init__(self):
        self.state = "ready"
        self.me = "201000967017"
        self.sent = []
        self.fail_with = None  # e.g. ("not_on_whatsapp", 422)
        self.untracked = False  # gateway accepted the send but couldn't read the message id
        self.started = self.logged_out = 0

    def handler(self, request: httpx.Request):
        assert request.headers["X-Api-Key"] == "test-gateway-key"
        path = request.url.path
        if request.method == "GET" and path.startswith("/sessions/"):
            return httpx.Response(200, json={"state": self.state, "qr": "data:image/png;base64,QR", "me": self.me})
        if path.endswith("/start"):
            self.started += 1
            self.state = "qr"
            return httpx.Response(200, json={"state": "starting"})
        if path.endswith("/logout"):
            self.logged_out += 1
            self.state = "disconnected"
            return httpx.Response(200, json={"state": "disconnected"})
        if path.endswith("/send"):
            if self.fail_with:
                code, status = self.fail_with
                return httpx.Response(status, json={"error": code})
            body = json.loads(request.content)
            self.sent.append(body)
            if self.untracked:
                return httpx.Response(200, json={"id": None, "tracked": False})
            return httpx.Response(200, json={"id": f"true_{body['to']}@c.us_MSG{len(self.sent)}"})
        return httpx.Response(404)


@pytest.fixture
def gateway(settings):
    gw = FakeGateway()
    settings.WA_GATEWAY_TRANSPORT = httpx.MockTransport(gw.handler)
    return gw


@pytest.fixture(autouse=True)
def fast_pdf(monkeypatch, settings, tmp_path):
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {"location": tmp_path}},
    }
    monkeypatch.setattr("apps.documents.pdf.render_pdf", lambda q, draft: b"%PDF-1.4 fake")


@pytest.fixture
def world(org_a, make_member, gateway):
    call_command("import_products", str(SEED), org="albarq")
    sales = make_member(org_a, "sales")
    owner = make_member(org_a, "owner")
    hotel = Customer.objects.create(organization=org_a, name="ريجينا", code="RG")
    contact = Contact.objects.create(
        organization=org_a, customer=hotel, name="محمد", preferred_channel="whatsapp", is_primary=True
    )
    wa = ContactChannel.objects.create(
        organization=org_a, contact=contact, type="whatsapp", value="01001779214", is_primary=True
    )
    em = ContactChannel.objects.create(
        organization=org_a, contact=contact, type="email", value="m@regina.example", is_primary=True
    )
    q = qservices.new_draft(org_a, sales, hotel, contact)
    item, _ = qservices.add_product(q, Product.objects.for_org(org_a).first())
    item.unit_price = Decimal("65")
    item.save()
    q = qservices.finalize(q, sales)
    cfg = SendingChannelConfig.for_org(org_a, ChannelKind.WHATSAPP)
    cfg.is_active = True
    cfg.save()
    email_cfg = SendingChannelConfig.for_org(org_a, ChannelKind.EMAIL)
    email_cfg.config = {"host": "smtp.example.com", "port": 587, "security": "tls", "username": "a@b.c",
                        "password": "x", "from_email": "a@b.c"}  # fmt: skip
    email_cfg.is_active = True
    email_cfg.save()
    return {"org": org_a, "sales": sales, "owner": owner, "q": q, "contact": contact, "wa": wa, "em": em}


def _review_and_send(client, w, recipients, **extra):
    client.get(reverse("quotations:pdf", args=[w["q"].pk]), {"review": "1"})
    data = {"subject": "عرض", "body": "نص الإيميل", "wa_body": "نص الواتساب", "recipients": recipients,
            "confirm": "on", **extra}  # fmt: skip
    return client.post(reverse("messaging:send_submit", args=[w["q"].pk]), data)


def _sign(body: bytes) -> str:
    return hmac.new(b"test-gateway-key", body, hashlib.sha256).hexdigest()


# --- sending -------------------------------------------------------------------------------


def test_whatsapp_preselected_on_send_page(client, world):
    client.force_login(world["sales"])
    html = client.get(reverse("messaging:send", args=[world["q"].pk])).content.decode()
    assert f'value="whatsapp:{world["wa"].pk}" class="accent-brand" checked' in html
    assert "مرفق عرض أسعار رقم" in html  # default WhatsApp caption prefilled


def test_send_pdf_by_whatsapp(client, world, gateway):
    client.force_login(world["sales"])
    _review_and_send(client, world, [f"whatsapp:{world['wa'].pk}"])
    d = Delivery.objects.get()
    assert d.status == DeliveryStatus.SENT and d.channel == ChannelKind.WHATSAPP
    assert d.message_text == "نص الواتساب" and d.subject == ""
    sent = gateway.sent[0]
    assert sent["to"] == "201001779214"  # E.164 without "+"
    assert sent["caption"] == "نص الواتساب"
    assert sent["filename"].endswith(".pdf") and sent["pdf_base64"]
    assert d.provider_message_id.startswith("true_201001779214@c.us")
    world["q"].refresh_from_db()
    assert world["q"].status == QuotationStatus.SENT


def test_send_both_channels(client, world, gateway):
    client.force_login(world["sales"])
    _review_and_send(client, world, [f"whatsapp:{world['wa'].pk}", f"email:{world['em'].pk}"])
    assert Delivery.objects.filter(status=DeliveryStatus.SENT).count() == 2
    assert len(gateway.sent) == 1 and len(mail.outbox) == 1
    assert mail.outbox[0].body == "نص الإيميل"


def test_not_on_whatsapp_then_fallback_to_email(client, world, gateway):
    gateway.fail_with = ("not_on_whatsapp", 422)
    client.force_login(world["sales"])
    _review_and_send(client, world, [f"whatsapp:{world['wa'].pk}"])
    d = Delivery.objects.get()
    assert d.status == DeliveryStatus.FAILED and d.attempts == 1  # permanent → no retries
    assert "مش عليه واتساب" in d.error_message
    html = client.get(reverse("messaging:deliveries", args=[world["q"].pk])).content.decode()
    assert "ابعت بالإيميل بدلها" in html

    client.post(reverse("messaging:fallback_email", args=[d.pk]))
    email_d = Delivery.objects.get(channel=ChannelKind.EMAIL)
    assert email_d.status == DeliveryStatus.SENT and email_d.recipient == "m@regina.example"
    assert len(mail.outbox) == 1


def test_gateway_send_failure_is_retried(client, world, gateway):
    gateway.fail_with = ("send_failed", 502)
    client.force_login(world["sales"])
    _review_and_send(client, world, [f"whatsapp:{world['wa'].pk}"])
    assert Delivery.objects.get().attempts == 4


def test_whatsapp_blocked_when_disconnected(client, world, gateway):
    gateway.state = "disconnected"
    client.force_login(world["sales"])
    _review_and_send(client, world, [f"whatsapp:{world['wa'].pk}"])
    assert not Delivery.objects.exists()
    assert not gateway.sent


def test_whatsapp_blocked_when_gateway_offline(client, world, settings):
    def down(request):
        raise httpx.ConnectError("refused")

    settings.WA_GATEWAY_TRANSPORT = httpx.MockTransport(down)
    assert whatsapp.status(world["org"])["state"] == "offline"
    assert not services.whatsapp_ready(world["org"])


def test_review_gate_still_applies(client, world, gateway):
    client.force_login(world["sales"])
    data = {"subject": "s", "body": "b", "wa_body": "w", "recipients": [f"whatsapp:{world['wa'].pk}"], "confirm": "on"}
    client.post(reverse("messaging:send_submit", args=[world["q"].pk]), data)
    assert not Delivery.objects.exists() and not gateway.sent


def test_rate_limit_waits_between_messages(world, settings, monkeypatch):
    from django.utils import timezone

    settings.WA_RATE_LIMIT_SECONDS = 20
    Delivery.objects.create(
        organization=world["org"], quotation=world["q"], channel="whatsapp", recipient="+201001779214",
        status=DeliveryStatus.SENT, sent_at=timezone.now(),
    )  # fmt: skip
    waited = []
    monkeypatch.setattr("apps.messaging.services.time.sleep", lambda s: waited.append(s))
    services._wait_for_whatsapp_slot(world["org"])
    assert waited and 15 < waited[0] <= 20


# --- webhook -------------------------------------------------------------------------------


def _webhook(client, payload, signature=None):
    body = json.dumps(payload).encode()
    return client.post(
        reverse("messaging:whatsapp_webhook"), body, content_type="application/json",
        HTTP_X_SIGNATURE=signature if signature is not None else _sign(body),
    )  # fmt: skip


def test_webhook_acks_update_status(client, world, gateway):
    client.force_login(world["sales"])
    _review_and_send(client, world, [f"whatsapp:{world['wa'].pk}"])
    d = Delivery.objects.get()
    client.logout()
    assert _webhook(client, {"event": "ack", "id": d.provider_message_id, "ack": 2}).status_code == 200
    d.refresh_from_db()
    assert d.status == DeliveryStatus.DELIVERED and d.delivered_at
    _webhook(client, {"event": "ack", "id": d.provider_message_id, "ack": 3})
    d.refresh_from_db()
    assert d.status == DeliveryStatus.READ and d.read_at
    _webhook(client, {"event": "ack", "id": d.provider_message_id, "ack": 2})  # late ack doesn't go backwards
    d.refresh_from_db()
    assert d.status == DeliveryStatus.READ


def test_webhook_rejects_bad_signature(client, world):
    assert _webhook(client, {"event": "ack", "id": "x", "ack": 3}, signature="nope").status_code == 403
    assert _webhook(client, {"event": "ack", "id": "x", "ack": 3}, signature="").status_code == 403


def test_webhook_status_events(client, world):
    _webhook(client, {"event": "status", "session": str(world["org"].pk), "state": "ready", "me": "201000967017"})
    cfg = SendingChannelConfig.objects.get(organization=world["org"], kind="whatsapp")
    assert cfg.status == "connected" and cfg.sender_identity == "01000967017"
    _webhook(client, {"event": "status", "session": str(world["org"].pk), "state": "disconnected", "error": "LOGOUT"})
    cfg.refresh_from_db()
    assert cfg.status == "disconnected"


# --- settings ------------------------------------------------------------------------------


def test_whatsapp_settings_permissions(client, world):
    client.force_login(world["sales"])
    for name in ("messaging:whatsapp", "messaging:whatsapp_status"):
        assert client.get(reverse(name)).status_code == 403
    for name in ("messaging:whatsapp_connect", "messaging:whatsapp_disconnect", "messaging:whatsapp_test"):
        assert client.post(reverse(name)).status_code == 403


def test_link_flow_shows_qr_and_disconnects(client, world, gateway):
    SendingChannelConfig.objects.filter(organization=world["org"], kind="whatsapp").update(is_active=False)
    gateway.state = "disconnected"
    client.force_login(world["owner"])
    assert "ربط رقم" in client.get(reverse("messaging:whatsapp")).content.decode()
    client.post(reverse("messaging:whatsapp_connect"))
    assert gateway.started == 1
    html = client.get(reverse("messaging:whatsapp_status")).content.decode()
    assert "data:image/png;base64,QR" in html and 'hx-trigger="every 3s"' in html

    gateway.state = "ready"
    html = client.get(reverse("messaging:whatsapp")).content.decode()
    assert "01000967017" in html and "متصل" in html

    client.post(reverse("messaging:whatsapp_disconnect"))
    assert gateway.logged_out == 1
    assert not SendingChannelConfig.objects.get(organization=world["org"], kind="whatsapp").is_active


def test_mismatched_number_warning(client, world, gateway):
    world["org"].settings.whatsapp_sender = "01000967017"
    world["org"].settings.save()
    gateway.me = "201111111111"
    client.force_login(world["owner"])
    assert "مختلف عن رقم الإرسال" in client.get(reverse("messaging:whatsapp")).content.decode()


def test_whatsapp_test_message(client, world, gateway):
    client.force_login(world["owner"])
    client.post(reverse("messaging:whatsapp_test"), {"to": "01060777030"})
    assert gateway.sent[-1] == {"to": "201060777030", "caption": "رسالة تجربة من مرسول البرق ✓"}


def test_dashboard_shows_whatsapp_state(client, world, gateway):
    client.force_login(world["sales"])
    assert "متصل" in client.get(reverse("dashboard:home")).content.decode()


# --- tenant isolation ----------------------------------------------------------------------


def test_cross_tenant(client, world, org_b, make_member, gateway):
    d = Delivery.objects.create(
        organization=world["org"], quotation=world["q"], channel="whatsapp", recipient="+201001779214",
        status=DeliveryStatus.FAILED, contact_channel=world["wa"],
    )  # fmt: skip
    client.force_login(make_member(org_b, "owner"))
    assert client.post(reverse("messaging:fallback_email", args=[d.pk])).status_code == 404
    # org B's WhatsApp page talks to its own session id, never org A's
    client.get(reverse("messaging:whatsapp"))
    assert whatsapp.session_id(org_b) != whatsapp.session_id(world["org"])


def test_untracked_send_counts_as_sent_and_is_not_retried(client, world, gateway):
    gateway.untracked = True
    client.force_login(world["sales"])
    _review_and_send(client, world, [f"whatsapp:{world['wa'].pk}"])
    d = Delivery.objects.get()
    assert d.status == DeliveryStatus.SENT and d.attempts == 1 and d.provider_message_id == ""
    assert len(gateway.sent) == 1  # never sent twice
