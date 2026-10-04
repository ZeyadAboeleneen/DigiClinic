import hashlib
import hmac
import json

import httpx
import pytest
from django.urls import reverse

from apps.notifications import services
from apps.notifications.management.commands.seed_notifications import seed_org_notifications
from apps.notifications.models import (
    Event,
    InboundMessage,
    MessageStatus,
    NotificationSettings,
    NotificationTemplate,
    ScheduledMessage,
)
from apps.patients.models import Gender, Patient


@pytest.fixture
def sent(settings):
    sent = []

    def handler(request):
        if "/check/" in request.url.path:
            return httpx.Response(200, json={"registered": True})
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "MSG"})

    settings.WA_GATEWAY_TRANSPORT = httpx.MockTransport(handler)
    return sent


@pytest.fixture
def world(org_a, org_b, make_member):
    seed_org_notifications(org_a)
    seed_org_notifications(org_b)
    return {
        "org": org_a,
        "owner": make_member(org_a, "owner"),
        "reception": make_member(org_a, "reception"),
        "viewer": make_member(org_a, "viewer"),
        "other_owner": make_member(org_b, "owner"),
    }


def _tpl(org, event=Event.BOOKING_CONFIRMED):
    return NotificationTemplate.objects.get(organization=org, event=event)


def test_settings_screens_need_settings_manage(client, world):
    tpl = _tpl(world["org"])
    client.force_login(world["reception"])
    assert client.get(reverse("notifications:settings")).status_code == 403
    assert client.get(reverse("notifications:template_edit", args=[tpl.pk])).status_code == 403
    assert client.post(reverse("notifications:template_preview"), {"body": "x"}).status_code == 403
    client.force_login(world["owner"])
    assert client.get(reverse("notifications:settings")).status_code == 200
    assert client.get(reverse("notifications:template_edit", args=[tpl.pk])).status_code == 200


def test_templates_are_tenant_scoped(client, world):
    client.force_login(world["other_owner"])
    assert client.get(reverse("notifications:template_edit", args=[_tpl(world["org"]).pk])).status_code == 404


def test_save_settings(client, world):
    client.force_login(world["owner"])
    resp = client.post(reverse("notifications:settings"), {
        "is_enabled": "on", "default_channel": "whatsapp", "quiet_start": "23:00", "quiet_end": "08:00",
        "quiet_policy": "skip", "urgent_until": "23:30", "wa_min_gap_seconds": 20,
    })  # fmt: skip
    assert resp.status_code == 302
    ns = NotificationSettings.objects.get(organization=world["org"])
    assert (ns.quiet_policy, ns.wa_min_gap_seconds, ns.email_fallback) == ("skip", 20, False)


def test_template_with_unknown_variable_is_rejected_at_save(client, world):
    tpl = _tpl(world["org"])
    client.force_login(world["owner"])
    resp = client.post(reverse("notifications:template_edit", args=[tpl.pk]), {
        "name_ar": "x", "channel": "default", "applies_to_mode": "all", "min_lead_minutes": 0,
        "body": "أهلًا {patient}", "is_enabled": "on",
    })  # fmt: skip
    assert resp.status_code == 200
    assert "{patient}" in resp.content.decode()
    tpl.refresh_from_db()
    assert "{patient}" not in tpl.body


def test_live_preview_renders_sample_data(client, world):
    client.force_login(world["owner"])
    resp = client.post(reverse("notifications:template_preview"), {"body": "أهلًا {first_name} — {visit_type}"})
    assert "أهلًا محمد — كشف" in resp.content.decode()
    resp = client.post(reverse("notifications:template_preview"), {"body": "{oops}"})
    assert "متغيرات مش معروفة" in resp.content.decode()


def test_send_test_goes_through_the_outbox(client, world, sent):
    tpl = _tpl(world["org"])
    client.force_login(world["owner"])
    resp = client.post(reverse("notifications:template_test", args=[tpl.pk]),
                       {"channel": "whatsapp", "to": "01001234567", "body": "تجربة {first_name}"})  # fmt: skip
    assert "اتبعتت" in resp.content.decode()
    assert sent == [{"to": "201001234567", "caption": "تجربة محمد"}]
    row = ScheduledMessage.objects.get(event=Event.TEST)
    assert row.status == MessageStatus.SENT and row.deliveries.count() == 1


def test_add_and_delete_extra_reminder(client, world):
    client.force_login(world["owner"])
    resp = client.post(reverse("notifications:template_add_reminder"))
    tpl = NotificationTemplate.objects.filter(organization=world["org"], event=Event.REMINDER).latest("id")
    assert resp.url == reverse("notifications:template_edit", args=[tpl.pk]) and not tpl.is_enabled
    client.post(reverse("notifications:template_delete", args=[tpl.pk]))
    assert not NotificationTemplate.objects.filter(pk=tpl.pk).exists()
    core = _tpl(world["org"])
    client.post(reverse("notifications:template_delete", args=[core.pk]))
    assert NotificationTemplate.objects.filter(pk=core.pk).exists()


def test_message_log_permissions_and_actions(client, world):
    org = world["org"]
    row = services.create_test_message(org, channel="whatsapp", recipient="+201001234567", body="x")
    client.force_login(world["viewer"])
    assert client.get(reverse("notifications:log")).status_code == 403
    client.force_login(world["reception"])
    assert client.get(reverse("notifications:log")).status_code == 200
    client.post(reverse("notifications:cancel", args=[row.pk]), HTTP_HX_REQUEST="true")
    row.refresh_from_db()
    assert row.status == MessageStatus.CANCELLED

    patient = Patient.objects.create(organization=org, full_name="س", phone="+201001234567", gender=Gender.MALE,
                                     file_number=9)  # fmt: skip
    failed = ScheduledMessage.objects.create(organization=org, patient=patient, event=Event.REMINDER,
                                             channel="whatsapp", recipient="+201001234567", send_at=row.send_at,
                                             status=MessageStatus.FAILED, attempts=4, dedupe_key="f1")  # fmt: skip
    client.post(reverse("notifications:retry", args=[failed.pk]))
    failed.refresh_from_db()
    assert (failed.status, failed.attempts) == (MessageStatus.PENDING, 0)


def test_message_log_is_tenant_scoped(client, world):
    row = services.create_test_message(world["org"], channel="whatsapp", recipient="+201001234567", body="secret")
    client.force_login(world["other_owner"])
    assert client.post(reverse("notifications:cancel", args=[row.pk])).status_code == 404
    assert client.post(reverse("notifications:retry", args=[row.pk])).status_code == 404
    resp = client.get(reverse("notifications:log"))
    assert f'id="msg-{row.pk}"' not in resp.content.decode()


def test_inbound_message_is_stored_and_linked_to_patient(client, world):
    org = world["org"]
    patient = Patient.objects.create(organization=org, full_name="س", phone="+201001234567", gender=Gender.MALE,
                                     file_number=9)  # fmt: skip
    payload = {"event": "message", "session": str(org.pk), "id": "IN1", "from": "201001234567", "body": "1"}
    body = json.dumps(payload).encode()
    sig = hmac.new(b"test-gateway-key", body, hashlib.sha256).hexdigest()
    for _i in range(2):  # gateway retries must not duplicate
        resp = client.post(reverse("messaging:whatsapp_webhook"), body, content_type="application/json",
                           HTTP_X_SIGNATURE=sig)  # fmt: skip
        assert resp.status_code == 200
    msg = InboundMessage.objects.get()
    assert (msg.patient, msg.from_number, msg.body) == (patient, "+201001234567", "1")


def test_dashboard_warns_when_scheduler_is_down(client, world):
    client.force_login(world["reception"])
    assert "محرك الرسايل واقف" in client.get(reverse("dashboard:home")).content.decode()
