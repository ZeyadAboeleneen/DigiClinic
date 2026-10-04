from datetime import timedelta

import httpx
import pytest
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time

from apps.messaging.models import ChannelStatus, SendingChannelConfig
from apps.notifications.management.commands.run_scheduler import beat


@pytest.fixture
def gateway_down(settings):
    settings.WA_GATEWAY_TRANSPORT = httpx.MockTransport(lambda r: httpx.Response(200, json={"state": "disconnected"}))


def test_healthz_is_public_and_minimal(client, db):
    resp = client.get(reverse("healthz"))
    assert resp.status_code == 200 and resp.json() == {"db": True, "scheduler": False}
    beat()
    assert client.get(reverse("healthz")).json()["scheduler"] is True


@override_settings(SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=[r"^healthz/$"])
def test_healthz_not_redirected_to_https(client, db):
    assert client.get(reverse("healthz")).status_code == 200


def test_owner_alerted_once_per_problem(org_a, make_member, gateway_down, mailoutbox, capsys):
    owner = make_member(org_a, "owner")
    make_member(org_a, "reception")
    cfg = SendingChannelConfig.for_org(org_a, "whatsapp")
    cfg.is_active, cfg.status = True, ChannelStatus.DISCONNECTED
    cfg.last_checked_at = timezone.now() - timedelta(minutes=30)
    cfg.save()
    call_command("check_health", "--alert")
    assert sorted(m.subject for m in mailoutbox) == ["DigiClinic — تنبيه: عيادة ديمو"] * 2  # scheduler + whatsapp
    assert all(m.to == [owner.email] for m in mailoutbox)
    call_command("check_health", "--alert")
    assert len(mailoutbox) == 2  # not repeated within 6 hours
    with freeze_time(timezone.now() + timedelta(hours=7)):
        call_command("check_health", "--alert")
    assert len(mailoutbox) == 4


def test_no_alert_when_healthy(org_a, make_member, mailoutbox, capsys):
    make_member(org_a, "owner")
    beat()
    call_command("check_health", "--alert")
    assert mailoutbox == [] and "OK" in capsys.readouterr().out
