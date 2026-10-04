import smtplib
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings as dj_settings
from django.core import mail
from django.core.mail.backends.base import BaseEmailBackend
from django.core.management import call_command
from django.urls import reverse

from apps.catalog.models import Product
from apps.core.crypto import decrypt_json, encrypt_json
from apps.customers.models import Contact, ContactChannel, Customer
from apps.messaging import services
from apps.messaging.models import ChannelKind, Delivery, DeliveryStatus, QuotationReview, SendingChannelConfig
from apps.quotations import services as qservices
from apps.quotations.models import QuotationStatus

SEED = Path(dj_settings.BASE_DIR) / "docs" / "plan" / "seed" / "products.csv"


# --- fake SMTP backends ------------------------------------------------------------------


class AuthFailBackend(BaseEmailBackend):
    def send_messages(self, messages):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")


class FlakyBackend(BaseEmailBackend):
    def send_messages(self, messages):
        raise smtplib.SMTPServerDisconnected("connection dropped")


@pytest.fixture(autouse=True)
def fast_pdf(monkeypatch, settings, tmp_path):
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {"location": tmp_path}},
    }
    monkeypatch.setattr("apps.documents.pdf.render_pdf", lambda q, draft: b"%PDF-1.4 fake")


@pytest.fixture
def world(org_a, make_member):
    call_command("import_products", str(SEED), org="albarq")
    sales = make_member(org_a, "sales", email="sales@albarq.example")
    owner = make_member(org_a, "owner")
    hotel = Customer.objects.create(organization=org_a, name="ريجينا", code="RG")
    contact = Contact.objects.create(
        organization=org_a, customer=hotel, name="محمد", preferred_channel="both", is_primary=True
    )
    ContactChannel.objects.create(organization=org_a, contact=contact, type="email", value="m@regina.example")
    ContactChannel.objects.create(organization=org_a, contact=contact, type="whatsapp", value="01001779214")
    q = qservices.new_draft(org_a, sales, hotel, contact)
    item, _ = qservices.add_product(q, Product.objects.for_org(org_a).first())
    item.unit_price = Decimal("65")
    item.save()
    q = qservices.finalize(q, sales)
    cfg = SendingChannelConfig.for_org(org_a, ChannelKind.EMAIL)
    cfg.config = {
        "host": "smtp.example.com", "port": 587, "security": "tls",
        "username": "elbaark.company@gmail.com", "password": "app-pass", "from_email": "elbaark.company@gmail.com",
    }  # fmt: skip
    cfg.is_active = True
    cfg.display_name = "البرق"
    cfg.save()
    email_ch = contact.channels.get(type="email")
    return {"org": org_a, "sales": sales, "owner": owner, "q": q, "contact": contact, "email_ch": email_ch}


def _post_send(client, w, **extra):
    data = {
        "subject": "عرض أسعار",
        "body": "مرفق العرض",
        "recipients": [f"email:{w['email_ch'].pk}"],
        "confirm": "on",
        **extra,
    }
    return client.post(reverse("messaging:send_submit", args=[w["q"].pk]), data)


def _review(client, w):
    r = client.get(reverse("quotations:pdf", args=[w["q"].pk]), {"review": "1"})
    assert r.status_code == 200
    assert r["X-Frame-Options"] == "SAMEORIGIN"  # the send page embeds it


# --- crypto & settings ---------------------------------------------------------------------


def test_encrypt_round_trip():
    token = encrypt_json({"password": "secret"})
    assert "secret" not in token
    assert decrypt_json(token) == {"password": "secret"}
    assert decrypt_json("garbage") == {}


def test_email_settings_permissions(client, world):
    client.force_login(world["sales"])
    assert client.get(reverse("messaging:email_settings")).status_code == 403
    assert client.post(reverse("messaging:email_test")).status_code == 403
    assert client.get(reverse("messaging:templates")).status_code == 403


def test_email_settings_save_keeps_password_encrypted(client, world):
    client.force_login(world["owner"])
    html = client.get(reverse("messaging:email_settings")).content.decode()
    assert "app-pass" not in html  # never rendered back
    data = {
        "is_active": "on", "display_name": "البرق", "from_email": "new@example.com", "host": "smtp.gmail.com",
        "port": "587", "security": "tls", "smtp_user": "new@example.com", "smtp_secret": "",
    }  # fmt: skip
    assert client.post(reverse("messaging:email_settings"), data).status_code == 302
    cfg = SendingChannelConfig.objects.get(organization=world["org"], kind="email")
    assert cfg.config["password"] == "app-pass"  # blank = keep
    assert cfg.config["username"] == "new@example.com"
    assert "app-pass" not in cfg.config_encrypted


def test_gmail_app_password_is_validated(client, world):
    client.force_login(world["owner"])
    base = {
        "is_active": "on", "display_name": "البرق", "from_email": "elbaark.company@gmail.com",
        "host": "smtp.gmail.com", "port": "587", "security": "tls", "smtp_user": "elbaark.company@gmail.com",
    }  # fmt: skip
    r = client.post(reverse("messaging:email_settings"), {**base, "smtp_secret": "albarq5"})
    assert r.status_code == 200 and "16 حرف" in r.content.decode()
    r = client.post(reverse("messaging:email_settings"), {**base, "smtp_user": ".....", "smtp_secret": ""})
    assert "الإيميل كامل" in r.content.decode()
    r = client.post(reverse("messaging:email_settings"), {**base, "smtp_secret": "abcd efgh ijkl mnop"})
    assert r.status_code == 302
    cfg = SendingChannelConfig.objects.get(organization=world["org"], kind="email")
    assert cfg.config["password"] == "abcdefghijklmnop"  # spaces removed
    html = client.get(reverse("messaging:email_settings")).content.decode()
    assert 'autocomplete="off"' in html and 'name="password"' not in html


def test_send_test_email(client, world):
    client.force_login(world["owner"])
    client.post(reverse("messaging:email_test"), {"to": "me@example.com"})
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["me@example.com"]
    assert SendingChannelConfig.objects.get(organization=world["org"], kind="email").status == "connected"


def test_templates_settings(client, world):
    client.force_login(world["owner"])
    html = client.get(reverse("messaging:templates")).content.decode()
    assert "{quote_number}" in html
    client.post(
        reverse("messaging:templates"),
        {"default_email_subject": "عرض {quote_number}", "default_email_body": "أهلًا {contact_name}",
         "default_whatsapp_message": "x"},
    )  # fmt: skip
    world["org"].settings.refresh_from_db()
    assert world["org"].settings.default_email_subject == "عرض {quote_number}"


def test_render_template_variables(world):
    q, contact = world["q"], world["contact"]
    v = services.variables_for(q, contact, world["sales"])
    out = services.render_template("{contact_name} / {quote_number} / {unknown} / {items_count}", v)
    assert out == f"محمد / {q.display_number} / {{unknown}} / 1"


# --- the review gate (Phase 6 DoD) --------------------------------------------------------


def test_direct_post_without_review_is_rejected(client, world):
    client.force_login(world["sales"])
    r = _post_send(client, world)
    assert r.status_code == 302
    assert not Delivery.objects.exists()
    assert len(mail.outbox) == 0


def test_review_without_checkbox_is_rejected(client, world):
    client.force_login(world["sales"])
    _review(client, world)
    _post_send(client, world, confirm="")
    assert not Delivery.objects.exists()


def test_reviewing_is_per_user(client, world):
    client.force_login(world["owner"])
    _review(client, world)
    client.force_login(world["sales"])
    _post_send(client, world)
    assert not Delivery.objects.exists()


def test_send_email_end_to_end(client, world):
    client.force_login(world["sales"])
    page = client.get(reverse("messaging:send", args=[world["q"].pk])).content.decode()
    assert "m@regina.example" in page and world["q"].display_number in page
    _review(client, world)
    assert QuotationReview.objects.filter(quotation=world["q"], user=world["sales"]).exists()

    _post_send(client, world)
    d = Delivery.objects.get()
    assert (d.status, d.review_confirmed, d.recipient, d.attempts) == (DeliveryStatus.SENT, True, "m@regina.example", 1)
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert msg.to == ["m@regina.example"] and msg.reply_to == ["sales@albarq.example"]
    assert "elbaark.company@gmail.com" in msg.from_email
    name, content, mime = msg.attachments[0]
    assert name.endswith(".pdf") and mime == "application/pdf" and content.startswith(b"%PDF")
    assert msg.alternatives and "text/html" == msg.alternatives[0][1]
    q = world["q"]
    q.refresh_from_db()
    assert q.status == QuotationStatus.SENT and q.sent_at


def test_default_message_is_prefilled(client, world):
    client.force_login(world["sales"])
    html = client.get(reverse("messaging:send", args=[world["q"].pk])).content.decode()
    assert "عناية أ/ محمد" in html and world["q"].display_number in html


def test_cannot_inject_foreign_recipient(client, world):
    other = Customer.objects.create(organization=world["org"], name="غيره", code="GH")
    oc = Contact.objects.create(organization=world["org"], customer=other, name="x")
    ch = ContactChannel.objects.create(organization=world["org"], contact=oc, type="email", value="x@x.example")
    client.force_login(world["sales"])
    _review(client, world)
    _post_send(client, world, recipients=[f"email:{ch.pk}"])
    assert not Delivery.objects.exists()


def test_whatsapp_not_yet_available(client, world):
    client.force_login(world["sales"])
    _review(client, world)
    wa = world["contact"].channels.get(type="whatsapp")
    _post_send(client, world, recipients=[f"whatsapp:{wa.pk}"])
    assert not Delivery.objects.exists()


def test_email_not_configured(client, world):
    SendingChannelConfig.objects.filter(organization=world["org"]).update(is_active=False)
    client.force_login(world["sales"])
    _review(client, world)
    _post_send(client, world)
    assert not Delivery.objects.exists()


def test_draft_cannot_be_sent(client, world):
    draft = qservices.new_draft(world["org"], world["sales"], world["q"].customer, world["contact"])
    client.force_login(world["sales"])
    assert client.get(reverse("messaging:send", args=[draft.pk])).status_code == 302
    with pytest.raises(services.SendError):
        services.create_deliveries(draft, world["sales"], [], subject="s", body="b", confirmed=True)


def test_viewer_cannot_send(client, world, make_member):
    viewer = make_member(world["org"], "viewer")
    client.force_login(viewer)
    assert client.get(reverse("messaging:send", args=[world["q"].pk])).status_code == 403
    assert _post_send(client, world).status_code == 403


# --- failures & retries ------------------------------------------------------------------


def test_auth_failure_then_retry(client, world, settings):
    settings.MESSAGING_EMAIL_BACKEND = f"{__name__}.AuthFailBackend"
    client.force_login(world["sales"])
    _review(client, world)
    _post_send(client, world)
    d = Delivery.objects.get()
    assert d.status == DeliveryStatus.FAILED and d.attempts == 1  # auth errors are not retried
    assert "كلمة مرور" in d.error_message
    assert "إعادة المحاولة" in client.get(reverse("messaging:deliveries", args=[world["q"].pk])).content.decode()
    world["q"].refresh_from_db()
    assert world["q"].status == QuotationStatus.FINALIZED

    settings.MESSAGING_EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    client.post(reverse("messaging:retry", args=[d.pk]))
    d.refresh_from_db()
    assert d.status == DeliveryStatus.SENT and d.attempts == 2


def test_temporary_errors_are_retried(client, world, settings):
    settings.MESSAGING_EMAIL_BACKEND = f"{__name__}.FlakyBackend"
    client.force_login(world["sales"])
    _review(client, world)
    _post_send(client, world)
    d = Delivery.objects.get()
    assert d.status == DeliveryStatus.FAILED and d.attempts == 4  # first try + 3 retries


def test_dashboard_lists_failed(client, world, settings):
    settings.MESSAGING_EMAIL_BACKEND = f"{__name__}.AuthFailBackend"
    client.force_login(world["sales"])
    _review(client, world)
    _post_send(client, world)
    assert "إرسال فشل" in client.get(reverse("dashboard:home")).content.decode()


# --- tenant isolation ----------------------------------------------------------------------


def test_cross_tenant(client, world, org_b, make_member):
    d = Delivery.objects.create(
        organization=world["org"], quotation=world["q"], channel="email", recipient="a@b.c",
        status=DeliveryStatus.FAILED,
    )  # fmt: skip
    client.force_login(make_member(org_b, "owner"))
    qpk = world["q"].pk
    assert client.get(reverse("messaging:send", args=[qpk])).status_code == 404
    assert client.post(reverse("messaging:send_submit", args=[qpk]), {"subject": "s", "body": "b"}).status_code == 404
    assert client.get(reverse("messaging:deliveries", args=[qpk])).status_code == 404
    assert client.post(reverse("messaging:retry", args=[d.pk])).status_code == 404
    # org B's settings page never shows org A's config
    assert "elbaark.company@gmail.com" not in client.get(reverse("messaging:email_settings")).content.decode()
