from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.urls import reverse

from apps.catalog.models import Category, Product
from apps.customers.models import Contact, ContactChannel, Customer
from apps.quotations import services
from apps.quotations.models import Quotation, QuotationStatus

SEED = Path(settings.BASE_DIR) / "docs" / "plan" / "seed" / "products.csv"
HX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def world(org_a, make_member):
    call_command("import_products", str(SEED), org="albarq")
    sales = make_member(org_a, "sales")
    regina = Customer.objects.create(organization=org_a, name="ريجينا", code="RG")
    contact = Contact.objects.create(organization=org_a, customer=regina, name="محمد", is_primary=True)
    ContactChannel.objects.create(organization=org_a, contact=contact, type="whatsapp", value="01001779214")
    return org_a, sales, regina, contact


def _new(client, **data):
    r = client.post(reverse("quotations:new"), data)
    assert r.status_code == 302
    return Quotation.objects.latest("id")


def test_dod_fifteen_items_end_to_end(client, world):
    """Phase 4 DoD: a 15-item quotation for an existing customer, priced and issued."""
    org, sales, regina, contact = world
    client.force_login(sales)
    q = _new(client, customer=regina.pk)
    assert (q.customer, q.contact) == (regina, contact)  # primary contact preselected
    assert client.get(reverse("quotations:edit", args=[q.pk])).status_code == 200

    products = list(Product.objects.for_org(org)[:15])
    for p in products:
        r = client.post(reverse("quotations:add_item", args=[q.pk]), {"product": p.pk}, **HX)
        assert r.status_code == 200 and r["HX-Retarget"] == "#items"
    for i, item in enumerate(q.items.all(), start=1):
        r = client.post(reverse("quotations:update_item", args=[q.pk, item.pk]), {"unit_price": f"{i}٫5"}, **HX)
        assert r.status_code == 200
    assert q.items.filter(unit_price__isnull=True).count() == 0
    assert q.items.get(product=products[0]).unit_price == Decimal("1.50")

    r = client.post(reverse("quotations:finalize", args=[q.pk]))
    q.refresh_from_db()
    assert r.status_code == 302 and r.url == reverse("quotations:detail", args=[q.pk])
    assert q.status == QuotationStatus.FINALIZED
    assert q.number.startswith("RG-") and q.items_count == 15
    html = client.get(reverse("quotations:detail", args=[q.pk])).content.decode()
    assert q.number in html and "أدوات المائدة الخشبية" in html


def test_issued_quotation_is_read_only(client, world):
    org, sales, regina, _contact = world
    client.force_login(sales)
    q = _new(client, customer=regina.pk)
    p = Product.objects.for_org(org).first()
    client.post(reverse("quotations:add_item", args=[q.pk]), {"product": p.pk})
    item = q.items.get()
    client.post(reverse("quotations:update_item", args=[q.pk, item.pk]), {"unit_price": "65"})
    client.post(reverse("quotations:finalize", args=[q.pk]))
    q.refresh_from_db()
    assert q.status == QuotationStatus.FINALIZED

    assert client.get(reverse("quotations:edit", args=[q.pk])).status_code == 302  # → detail
    for name, data in [
        ("quotations:add_item", {"product": p.pk}),
        ("quotations:meta", {"issue_date": "2026-01-01", "valid_until": "2026-01-31"}),
        ("quotations:set_customer", {"customer": ""}),
        ("quotations:finalize", {}),
        ("quotations:delete", {}),
    ]:
        assert client.post(reverse(name, args=[q.pk]), data).status_code == 403, name
    assert client.post(reverse("quotations:update_item", args=[q.pk, item.pk]), {"unit_price": "1"}).status_code == 403
    item.refresh_from_db()
    assert item.unit_price == Decimal("65")


def test_bad_price_is_reported_inline(client, world):
    org, sales, regina, _ = world
    client.force_login(sales)
    q = _new(client, customer=regina.pk)
    client.post(reverse("quotations:add_item", args=[q.pk]), {"product": Product.objects.for_org(org).first().pk})
    item = q.items.get()
    r = client.post(reverse("quotations:update_item", args=[q.pk, item.pk]), {"unit_price": "abc"}, **HX)
    assert "رقم" in r.content.decode()
    r = client.post(reverse("quotations:update_item", args=[q.pk, item.pk]), {"unit_price": "0"}, **HX)
    assert "أكبر من صفر" in r.content.decode()
    item.refresh_from_db()
    assert item.unit_price is None


def test_finalize_blocked_with_missing_prices(client, world):
    org, sales, regina, _ = world
    client.force_login(sales)
    q = _new(client, customer=regina.pk)
    client.post(reverse("quotations:add_item", args=[q.pk]), {"product": Product.objects.for_org(org).first().pk})
    r = client.post(reverse("quotations:finalize", args=[q.pk]))
    assert r.url == reverse("quotations:edit", args=[q.pk])
    q.refresh_from_db()
    assert q.status == QuotationStatus.DRAFT and not q.number
    assert "سعر" in client.get(reverse("quotations:summary", args=[q.pk])).content.decode()


def test_customer_search_select_and_quick_contact(client, world):
    _org, sales, regina, _ = world
    client.force_login(sales)
    q = _new(client)
    r = client.get(reverse("quotations:customer_search", args=[q.pk]), {"cq": "ريجينه"[:5]})
    assert "ريجينا" in r.content.decode()
    client.post(reverse("quotations:set_customer", args=[q.pk]), {"customer": regina.pk}, **HX)
    q.refresh_from_db()
    assert q.customer == regina and q.contact.name == "محمد"
    r = client.post(
        reverse("quotations:quick_contact", args=[q.pk]),
        {"name": "Sara", "email": "sara@example.com", "preferred_channel": "email"},
        **HX,
    )
    q.refresh_from_db()
    assert r["HX-Retarget"] == "#party"
    assert q.contact.name == "Sara"


def test_quick_customer(client, world):
    _org, sales, *_ = world
    client.force_login(sales)
    q = _new(client)
    client.post(reverse("quotations:quick_customer", args=[q.pk]), {"name": "فندق جديد", "kind": "hotel"}, **HX)
    q.refresh_from_db()
    assert q.customer.name == "فندق جديد" and q.customer.code


def test_add_category_and_custom_item(client, world, make_member):
    org, sales, regina, _ = world
    client.force_login(sales)
    q = _new(client, customer=regina.pk)
    cat = Category.objects.for_org(org).get(name="الفويل")
    client.post(reverse("quotations:add_category", args=[q.pk]), {"category": cat.pk}, **HX)
    assert q.items.count() == 2
    r = client.post(
        reverse("quotations:add_custom", args=[q.pk]),
        {"description": "صنف خاص", "unit_name": "قطعة", "unit_price": "12.5", "category": ""},
        **HX,
    )
    assert 'id="custom-slot" hx-swap-oob' in r.content.decode()
    custom = q.items.get(product=None)
    assert (custom.unit_price, custom.category_name) == (Decimal("12.50"), "أصناف أخرى")
    # Sales may not grow the catalog
    r = client.post(
        reverse("quotations:add_custom", args=[q.pk]),
        {"description": "جديد", "unit_name": "قطعة", "category": cat.pk, "add_to_catalog": "on"},
        **HX,
    )
    assert "للمدير" in r.content.decode()
    assert not Product.objects.filter(name="جديد").exists()
    # A manager may
    client.force_login(make_member(org, "manager"))
    client.post(
        reverse("quotations:add_custom", args=[q.pk]),
        {"description": "جديد", "unit_name": "قطعة", "category": cat.pk, "add_to_catalog": "on"},
        **HX,
    )
    assert Product.objects.filter(name="جديد", category=cat).exists()


def test_meta_update(client, world):
    _org, sales, regina, _ = world
    client.force_login(sales)
    q = _new(client, customer=regina.pk)
    client.post(
        reverse("quotations:meta", args=[q.pk]),
        {
            "issue_date": "2026-10-05",
            "valid_until": "2026-10-31",
            "intro_text": "x",
            "price_note": "",
            "terms_text": "شرط 1\n\n شرط 2 ",
            "internal_notes": "",
        },
        **HX,
    )
    q.refresh_from_db()
    assert q.issue_date == date(2026, 10, 5) and q.terms == ["شرط 1", "شرط 2"]
    r = client.post(
        reverse("quotations:meta", args=[q.pk]),
        {"issue_date": "2026-10-05", "valid_until": "2026-10-01", "terms_text": ""},
        **HX,
    )
    assert "بعد تاريخ العرض" in r.content.decode()


def test_revise_duplicate_and_status(client, world, make_member):
    org, sales, regina, contact = world
    q = services.new_draft(org, sales, regina, contact)
    item, _ = services.add_product(q, Product.objects.for_org(org).first())
    item.unit_price = Decimal("10")
    item.save()
    q = services.finalize(q, sales)
    client.force_login(sales)

    r = client.post(reverse("quotations:revise", args=[q.pk]))
    rev = Quotation.objects.latest("id")
    assert r.url == reverse("quotations:edit", args=[rev.pk]) and rev.parent_quotation == q
    assert client.post(reverse("quotations:set_customer", args=[rev.pk]), {"customer": ""}).status_code == 403

    client.post(reverse("quotations:duplicate", args=[q.pk]))
    assert Quotation.objects.latest("id").parent_quotation is None

    # sales can only change status of own quotations
    other = make_member(org, "sales", email="other@example.com")
    client.force_login(other)
    assert client.post(reverse("quotations:status", args=[q.pk]), {"status": "accepted"}).status_code == 403
    client.force_login(sales)
    client.post(reverse("quotations:status", args=[q.pk]), {"status": "accepted"})
    q.refresh_from_db()
    assert q.status == QuotationStatus.ACCEPTED


def test_viewer_can_list_but_not_create(client, world, make_member):
    org, *_ = world
    client.force_login(make_member(org, "viewer"))
    assert client.get(reverse("quotations:list")).status_code == 200
    assert client.post(reverse("quotations:new")).status_code == 403


def test_list_filters(client, world):
    org, sales, regina, contact = world
    q = services.new_draft(org, sales, regina, contact)
    item, _ = services.add_product(q, Product.objects.for_org(org).first())
    item.unit_price = Decimal("10")
    item.save()
    services.finalize(q, sales)
    services.new_draft(org, sales)
    client.force_login(sales)
    html = client.get(reverse("quotations:list"), {"status": "finalized"}).content.decode()
    assert "RG-" in html and "مسودة #" not in html
    html = client.get(reverse("quotations:list"), {"q": "RG-"}).content.decode()
    assert "RG-" in html


def test_dashboard_shows_drafts_and_recent(client, world):
    org, sales, regina, contact = world
    services.new_draft(org, sales, regina, contact)
    client.force_login(sales)
    html = client.get(reverse("dashboard:home")).content.decode()
    assert "كمّل من حيث وقفت" in html and "ريجينا" in html


# --- tenant isolation ---------------------------------------------------------------------


@pytest.fixture
def foreign_quote(world, org_b, make_member):
    org, sales, regina, contact = world
    q = services.new_draft(org, sales, regina, contact)
    item, _ = services.add_product(q, Product.objects.for_org(org).first())
    return q, item, make_member(org_b, "owner")


@pytest.mark.parametrize(
    ("name", "method"),
    [
        ("quotations:detail", "get"),
        ("quotations:edit", "get"),
        ("quotations:picker", "get"),
        ("quotations:customer_search", "get"),
        ("quotations:set_customer", "post"),
        ("quotations:set_contact", "post"),
        ("quotations:quick_contact", "post"),
        ("quotations:quick_customer", "post"),
        ("quotations:copy_last", "post"),
        ("quotations:add_item", "post"),
        ("quotations:add_category", "post"),
        ("quotations:add_custom", "post"),
        ("quotations:clear_items", "post"),
        ("quotations:summary", "get"),
        ("quotations:meta", "post"),
        ("quotations:finalize", "post"),
        ("quotations:delete", "post"),
        ("quotations:revise", "post"),
        ("quotations:duplicate", "post"),
        ("quotations:status", "post"),
    ],
)
def test_cross_tenant_404(client, foreign_quote, name, method):
    q, _item, outsider = foreign_quote
    client.force_login(outsider)
    assert getattr(client, method)(reverse(name, args=[q.pk])).status_code == 404
    assert Quotation.objects.filter(pk=q.pk, status="draft").exists()


def test_cross_tenant_item_urls_404(client, foreign_quote):
    q, item, outsider = foreign_quote
    client.force_login(outsider)
    assert client.post(reverse("quotations:update_item", args=[q.pk, item.pk]), {"unit_price": "1"}).status_code == 404
    assert client.post(reverse("quotations:delete_item", args=[q.pk, item.pk])).status_code == 404
    item.refresh_from_db()
    assert item.unit_price is None


def test_cannot_attach_foreign_customer_or_product(client, world, org_b, make_member):
    _org, sales, *_ = world
    other_customer = Customer.objects.create(organization=org_b, name="غريب")
    client.force_login(sales)
    q = _new(client)
    assert (
        client.post(reverse("quotations:set_customer", args=[q.pk]), {"customer": other_customer.pk}).status_code == 404
    )
    from apps.catalog.models import ProductGroup, Unit

    g = ProductGroup.objects.create(organization=org_b, name="G")
    c = Category.objects.create(organization=org_b, group=g, name="C")
    p = Product.objects.create(organization=org_b, category=c, name="P", unit=Unit.get_for(org_b, "x"))
    assert client.post(reverse("quotations:add_item", args=[q.pk]), {"product": p.pk}).status_code == 404
    assert client.post(reverse("quotations:add_category", args=[q.pk]), {"category": c.pk}).status_code == 404


def test_item_of_other_quote_same_org_404(client, world):
    org, sales, regina, contact = world
    a = services.new_draft(org, sales, regina, contact)
    b = services.new_draft(org, sales, regina, contact)
    item, _ = services.add_product(a, Product.objects.for_org(org).first())
    client.force_login(sales)
    assert client.post(reverse("quotations:delete_item", args=[b.pk, item.pk])).status_code == 404
