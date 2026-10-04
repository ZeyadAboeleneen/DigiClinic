import smtplib

import pytest
from django.core import mail
from django.core.mail.backends.base import BaseEmailBackend
from django.urls import reverse

from apps.core.crypto import decrypt_json, encrypt_json
from apps.messaging.models import SendingChannelConfig


class AuthFailBackend(BaseEmailBackend):
    def send_messages(self, messages):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")


@pytest.fixture
def world(org_a, make_member):
    owner = make_member(org_a, "owner")
    reception = make_member(org_a, "reception")
    cfg = SendingChannelConfig.for_org(org_a, "email")
    cfg.config = {"host": "smtp.example.com", "port": 587, "security": "tls", "username": "a@b.c", "password": "x",
                  "from_email": "a@b.c"}  # fmt: skip
    cfg.is_active = True
    cfg.save()
    return {"org": org_a, "owner": owner, "reception": reception}


def test_encrypt_round_trip():
    token = encrypt_json({"password": "secret"})
    assert "secret" not in token
    assert decrypt_json(token) == {"password": "secret"}
    assert decrypt_json("garbage") == {}


def test_email_settings_permissions(client, world):
    client.force_login(world["reception"])
    assert client.get(reverse("messaging:email_settings")).status_code == 403
    assert client.post(reverse("messaging:email_test")).status_code == 403


def test_email_settings_save_keeps_password_encrypted(client, world):
    client.force_login(world["owner"])
    html = client.get(reverse("messaging:email_settings")).content.decode()
    assert "app-pass" not in html
    data = {
        "is_active": "on", "display_name": "العيادة", "from_email": "new@example.com", "host": "smtp.gmail.com",
        "port": "587", "security": "tls", "smtp_user": "new@example.com", "smtp_secret": "abcd efgh ijkl mnop",
    }  # fmt: skip
    assert client.post(reverse("messaging:email_settings"), data).status_code == 302
    cfg = SendingChannelConfig.objects.get(organization=world["org"], kind="email")
    assert cfg.config["password"] == "abcdefghijklmnop"
    assert cfg.config["username"] == "new@example.com"
    assert "abcdefghijklmnop" not in cfg.config_encrypted


def test_gmail_app_password_is_validated(client, world):
    client.force_login(world["owner"])
    base = {
        "is_active": "on", "display_name": "العيادة", "from_email": "clinic@gmail.com",
        "host": "smtp.gmail.com", "port": "587", "security": "tls", "smtp_user": "clinic@gmail.com",
    }  # fmt: skip
    r = client.post(reverse("messaging:email_settings"), {**base, "smtp_secret": "short"})
    assert r.status_code == 200 and "16 حرف" in r.content.decode()
    r = client.post(reverse("messaging:email_settings"), {**base, "smtp_user": ".....", "smtp_secret": ""})
    assert "الإيميل كامل" in r.content.decode()


def test_send_test_email(client, world):
    client.force_login(world["owner"])
    client.post(reverse("messaging:email_test"), {"to": "me@example.com"})
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["me@example.com"]
    assert SendingChannelConfig.objects.get(organization=world["org"], kind="email").status == "connected"


def test_email_auth_failure_marks_channel_error(client, world, settings):
    settings.MESSAGING_EMAIL_BACKEND = f"{__name__}.AuthFailBackend"
    client.force_login(world["owner"])
    client.post(reverse("messaging:email_test"), {"to": "me@example.com"})
    assert SendingChannelConfig.objects.get(organization=world["org"], kind="email").status == "error"


def test_cross_tenant_settings_isolated(client, world, org_b, make_member):
    cfg = SendingChannelConfig.for_org(world["org"], "email")
    cfg.config = {"host": "smtp.example.com", "username": "secret@example.com"}
    cfg.save()
    client.force_login(make_member(org_b, "owner"))
    html = client.get(reverse("messaging:email_settings")).content.decode()
    assert "secret@example.com" not in html
