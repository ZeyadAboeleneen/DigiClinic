import hashlib
import hmac
import json

import httpx
import pytest
from django.urls import reverse

from apps.messaging import services, whatsapp
from apps.messaging.models import SendingChannelConfig


class FakeGateway:
    """In-memory stand-in for whatsapp-gateway/server.js."""

    def __init__(self):
        self.state = "disconnected"
        self.me = "201000967017"
        self.sent = []
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
            body = json.loads(request.content)
            self.sent.append(body)
            return httpx.Response(200, json={"id": f"true_{body['to']}@c.us_MSG{len(self.sent)}"})
        return httpx.Response(404)


@pytest.fixture
def gateway(settings):
    gw = FakeGateway()
    settings.WA_GATEWAY_TRANSPORT = httpx.MockTransport(gw.handler)
    return gw


@pytest.fixture
def world(org_a, make_member, gateway):
    owner = make_member(org_a, "owner")
    reception = make_member(org_a, "reception")
    return {"org": org_a, "owner": owner, "reception": reception}


def _sign(body: bytes) -> str:
    return hmac.new(b"test-gateway-key", body, hashlib.sha256).hexdigest()


def _webhook(client, payload, signature=None):
    body = json.dumps(payload).encode()
    return client.post(
        reverse("messaging:whatsapp_webhook"), body, content_type="application/json",
        HTTP_X_SIGNATURE=signature if signature is not None else _sign(body),
    )  # fmt: skip


def test_whatsapp_settings_permissions(client, world):
    client.force_login(world["reception"])
    for name in ("messaging:whatsapp", "messaging:whatsapp_status"):
        assert client.get(reverse(name)).status_code == 403
    for name in ("messaging:whatsapp_connect", "messaging:whatsapp_disconnect", "messaging:whatsapp_test"):
        assert client.post(reverse(name)).status_code == 403


def test_link_flow_shows_qr_and_disconnects(client, world, gateway):
    client.force_login(world["owner"])
    client.post(reverse("messaging:whatsapp_connect"))
    assert gateway.started == 1
    html = client.get(reverse("messaging:whatsapp_status")).content.decode()
    assert "data:image/png;base64,QR" in html

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
    gateway.state = "ready"
    client.force_login(world["owner"])
    assert "مختلف عن رقم الإرسال" in client.get(reverse("messaging:whatsapp")).content.decode()


def test_whatsapp_test_message(client, world, gateway):
    gateway.state = "ready"
    client.force_login(world["owner"])
    client.post(reverse("messaging:whatsapp_test"), {"to": "01060777030"})
    assert gateway.sent[-1] == {"to": "201060777030", "caption": "رسالة تجربة من DigiClinic ✓"}


def test_whatsapp_blocked_when_gateway_offline(world, settings):
    def down(request):
        raise httpx.ConnectError("refused")

    settings.WA_GATEWAY_TRANSPORT = httpx.MockTransport(down)
    assert whatsapp.status(world["org"])["state"] == "offline"
    assert not services.whatsapp_ready(world["org"])


def test_webhook_acks_update_status(client, world, gateway):
    from apps.messaging.models import ChannelKind, Delivery, DeliveryStatus

    d = Delivery.objects.create(
        organization=world["org"], channel=ChannelKind.WHATSAPP, recipient="+201001779214",
        status=DeliveryStatus.SENT, provider_message_id="true_201001779214@c.us_MSG1",
    )  # fmt: skip
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
    SendingChannelConfig.for_org(world["org"], "whatsapp")
    _webhook(client, {"event": "status", "session": str(world["org"].pk), "state": "ready", "me": "201000967017"})
    cfg = SendingChannelConfig.objects.get(organization=world["org"], kind="whatsapp")
    assert cfg.status == "connected" and cfg.sender_identity == "01000967017"
    _webhook(client, {"event": "status", "session": str(world["org"].pk), "state": "disconnected", "error": "LOGOUT"})
    cfg.refresh_from_db()
    assert cfg.status == "disconnected"


def test_cross_tenant(client, world, org_b, make_member):
    client.force_login(make_member(org_b, "owner"))
    client.get(reverse("messaging:whatsapp"))
    assert whatsapp.session_id(org_b) != whatsapp.session_id(world["org"])
