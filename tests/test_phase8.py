from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings as dj_settings
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.catalog.models import Category, Product, ProductGroup
from apps.customers.models import Contact, ContactChannel, Customer
from apps.quotations import services
from apps.quotations.models import Quotation, QuotationStatus

SEED = Path(dj_settings.BASE_DIR) / "docs" / "plan" / "seed" / "products.csv"


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
    sales = make_member(org_a, "sales")
    manager = make_member(org_a, "manager")
    hotel = Customer.objects.create(organization=org_a, name="ريجينا", code="RG")
    contact = Contact.objects.create(organization=org_a, customer=hotel, name="محمد", is_primary=True)
    ContactChannel.objects.create(organization=org_a, contact=contact, type="email", value="m@r.example")
    return {"org": org_a, "sales": sales, "manager": manager, "hotel": hotel, "contact": contact}


def _issued(w, days_valid=10):
    q = services.new_draft(w["org"], w["sales"], w["hotel"], w["contact"])
    item, _ = services.add_product(q, Product.objects.for_org(w["org"]).first())
    item.unit_price = Decimal("65")
    item.save()
    q.valid_until = timezone.localdate() + timedelta(days=days_valid)
    q.save()
    return services.finalize(q, w["sales"])


# --- audit log -----------------------------------------------------------------------------


def test_login_and_failed_login_are_logged(client, world):
    client.post(reverse("accounts:login"), {"username": world["sales"].email, "password": "wrong"})
    client.post(reverse("accounts:login"), {"username": world["sales"].email, "password": "Strong-Pass-2026!"})
    actions = list(AuditEvent.objects.values_list("action", flat=True))
    assert "auth.login_failed" in actions and "auth.login" in actions
    failed = AuditEvent.objects.get(action="auth.login_failed")
    assert failed.organization == world["org"] and "wrong" not in failed.summary + str(failed.metadata)


def test_dod_who_sent_what_to_whom_and_when(client, world, settings):
    """Phase 8 DoD: the admin can answer 'who sent this quotation, to whom, when' from the UI."""
    from apps.messaging.models import ChannelKind, SendingChannelConfig

    cfg = SendingChannelConfig.for_org(world["org"], ChannelKind.EMAIL)
    cfg.config = {"host": "smtp.example.com", "port": 587, "security": "tls", "username": "a@b.c",
                  "password": "x", "from_email": "a@b.c"}  # fmt: skip
    cfg.is_active = True
    cfg.save()
    q = _issued(world)
    client.force_login(world["sales"])
    client.get(reverse("quotations:pdf", args=[q.pk]), {"review": "1"})
    ch = world["contact"].channels.get(type="email")
    client.post(
        reverse("messaging:send_submit", args=[q.pk]),
        {"subject": "s", "body": "b", "recipients": [f"email:{ch.pk}"], "confirm": "on"},
    )
    sent = AuditEvent.objects.get(action="delivery.sent")
    assert sent.actor == world["sales"] and sent.target_id == q.pk
    assert "m@r.example" in sent.summary and "محمد" in sent.summary

    client.force_login(world["manager"])
    html = client.get(reverse("audit:activity")).content.decode()
    assert "اتبعت" in html and "m@r.example" in html and world["sales"].full_name in html
    detail = client.get(reverse("quotations:detail", args=[q.pk])).content.decode()
    assert "سجل العرض" in detail and "m@r.example" in detail


def test_activity_filters_and_permissions(client, world, make_member):
    _issued(world)
    client.force_login(world["sales"])
    assert client.get(reverse("audit:activity")).status_code == 403
    client.force_login(world["manager"])
    r = client.get(reverse("audit:activity"), {"group": "quote"})
    assert "إصدار عرض" in r.content.decode()
    r = client.get(reverse("audit:activity"), {"group": "auth"})
    assert "إصدار عرض" not in r.content.decode()


def test_audit_is_append_only(world):
    e = AuditEvent.objects.create(organization=world["org"], action="auth.login")
    e.summary = "x"
    with pytest.raises(PermissionError):
        e.save()
    with pytest.raises(PermissionError):
        e.delete()


def test_activity_is_tenant_scoped(client, world, org_b, make_member):
    AuditEvent.objects.create(organization=world["org"], action="auth.login", summary="secret-a")
    client.force_login(make_member(org_b, "owner"))
    assert "secret-a" not in client.get(reverse("audit:activity")).content.decode()


# --- expiry --------------------------------------------------------------------------------


def test_overdue_quotations_expire(client, world):
    q = _issued(world)
    Quotation.objects.filter(pk=q.pk).update(valid_until=timezone.localdate() - timedelta(days=1))
    fresh = _issued(world)
    call_command("expire_quotations")
    q.refresh_from_db()
    fresh.refresh_from_db()
    assert q.status == QuotationStatus.EXPIRED and fresh.status == QuotationStatus.FINALIZED
    assert AuditEvent.objects.filter(action="quote.expired", target_id=q.pk).exists()


def test_dashboard_expires_and_lists_expiring_soon(client, world):
    soon = _issued(world, days_valid=3)
    client.force_login(world["sales"])
    html = client.get(reverse("dashboard:home")).content.decode()
    assert "بتنتهي خلال أسبوع" in html and soon.display_number in html


# --- edit issued / delete ------------------------------------------------------------------


def test_edit_issued_quotation_keeps_number(client, world):
    q = _issued(world)
    number = q.display_number
    client.force_login(world["sales"])
    r = client.post(reverse("quotations:reopen", args=[q.pk]))
    assert r.url == reverse("quotations:edit", args=[q.pk])
    q.refresh_from_db()
    assert q.status == QuotationStatus.DRAFT and not q.pdf_file
    item = q.items.first()
    client.post(reverse("quotations:update_item", args=[q.pk, item.pk]), {"unit_price": "70"})
    client.post(reverse("quotations:finalize", args=[q.pk]))
    q.refresh_from_db()
    assert q.status == QuotationStatus.FINALIZED and q.display_number == number
    assert q.items.first().unit_price == Decimal("70")
    assert AuditEvent.objects.filter(action="quote.reopened", target_id=q.pk).exists()


def test_reopen_requires_new_review_before_sending(client, world):
    from apps.messaging.models import QuotationReview

    q = _issued(world)
    QuotationReview.objects.create(organization=world["org"], quotation=q, user=world["sales"])
    services.reopen(q, world["sales"])
    assert not QuotationReview.objects.filter(quotation=q).exists()


def test_delete_quotation_permissions(client, world):
    q = _issued(world)
    client.force_login(world["sales"])
    assert client.post(reverse("quotations:delete", args=[q.pk])).status_code == 403
    draft = services.new_draft(world["org"], world["sales"], world["hotel"], world["contact"])
    assert client.post(reverse("quotations:delete", args=[draft.pk])).status_code == 302  # own draft ok
    client.force_login(world["manager"])
    assert client.post(reverse("quotations:delete", args=[q.pk])).status_code == 302
    assert not Quotation.objects.filter(pk__in=[q.pk, draft.pk]).exists()


def test_delete_quotation_with_revision_keeps_revision(world):
    q = _issued(world)
    rev = services.create_revision(q, world["sales"])
    services.delete_quotation(q)
    rev.refresh_from_db()
    assert rev.parent_quotation is None


def test_delete_customer(client, world):
    client.force_login(world["sales"])
    assert client.post(reverse("customers:delete", args=[world["hotel"].pk])).status_code == 403
    client.force_login(world["manager"])
    _issued(world)
    client.post(reverse("customers:delete", args=[world["hotel"].pk]))
    assert Customer.objects.filter(pk=world["hotel"].pk).exists()  # has quotations → blocked
    empty = Customer.objects.create(organization=world["org"], name="فاضي")
    Contact.objects.create(organization=world["org"], customer=empty, name="x")
    client.post(reverse("customers:delete", args=[empty.pk]))
    assert not Customer.objects.filter(pk=empty.pk).exists()


def test_delete_contact(client, world):
    extra = Contact.objects.create(organization=world["org"], customer=world["hotel"], name="زيادة")
    client.force_login(world["sales"])
    client.post(reverse("customers:contact_delete", args=[extra.pk]))
    assert not Contact.objects.filter(pk=extra.pk).exists()
    _issued(world)
    client.post(reverse("customers:contact_delete", args=[world["contact"].pk]))
    assert Contact.objects.filter(pk=world["contact"].pk).exists()  # used on a quotation → blocked


def test_delete_product_keeps_old_quotations(client, world):
    q = _issued(world)
    product = q.items.first().product
    client.force_login(world["manager"])
    client.post(reverse("catalog:product_delete", args=[product.pk]))
    assert not Product.objects.filter(pk=product.pk).exists()
    item = q.items.first()
    assert item.product is None and item.description == product.name


def test_delete_empty_category_and_group_only(client, world):
    client.force_login(world["manager"])
    full = Category.objects.for_org(world["org"]).first()
    client.post(reverse("catalog:category_delete", args=[full.pk]))
    assert Category.objects.filter(pk=full.pk).exists()
    g = ProductGroup.objects.create(organization=world["org"], name="جديدة")
    c = Category.objects.create(organization=world["org"], group=g, name="قسم فاضي")
    client.post(reverse("catalog:category_delete", args=[c.pk]))
    client.post(reverse("catalog:group_delete", args=[g.pk]))
    assert not ProductGroup.objects.filter(pk=g.pk).exists()


def test_delete_is_tenant_scoped(client, world, org_b, make_member):
    q = _issued(world)
    client.force_login(make_member(org_b, "owner"))
    assert client.post(reverse("quotations:delete", args=[q.pk])).status_code == 404
    assert client.post(reverse("quotations:reopen", args=[q.pk])).status_code == 404
    assert client.post(reverse("customers:delete", args=[world["hotel"].pk])).status_code == 404
    assert client.post(reverse("customers:contact_delete", args=[world["contact"].pk])).status_code == 404
    p = Product.objects.for_org(world["org"]).first()
    assert client.post(reverse("catalog:product_delete", args=[p.pk])).status_code == 404
