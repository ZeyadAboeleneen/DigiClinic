from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.urls import reverse

from apps.catalog.models import Category, Product, ProductGroup, Unit

SEED = Path(settings.BASE_DIR) / "docs" / "plan" / "seed" / "products.csv"


@pytest.fixture
def seeded(org_a):
    call_command("import_products", str(SEED), org="albarq")
    return org_a


@pytest.fixture
def manager(org_a, make_member):
    return make_member(org_a, "manager")


@pytest.fixture
def sales(org_a, make_member):
    return make_member(org_a, "sales")


# --- import -------------------------------------------------------------------------------


def test_import_counts_and_order(seeded):
    assert ProductGroup.objects.for_org(seeded).count() == 6
    assert Category.objects.for_org(seeded).count() == 20
    assert Product.objects.for_org(seeded).count() == 72
    first = Product.objects.for_org(seeded).first()
    assert (first.category.group.name, first.category.name, first.name) == (
        "أدوات المائدة والتقديم",
        "أدوات المائدة الخشبية",
        "سكينة خشب ×100",
    )
    assert first.unit.name == "باكت"
    names = list(Category.objects.for_org(seeded).values_list("name", flat=True)[:3])
    assert names == ["أدوات المائدة الخشبية", "أدوات المائدة البلاستيك", "الشاليموات (سن العصير)"]


def test_import_twice_does_not_duplicate_or_undo_ui_changes(seeded):
    p = Product.objects.for_org(seeded).get(name="سكينة خشب ×100")
    p.sort_order = 99
    p.save()
    call_command("import_products", str(SEED), org="albarq")
    assert Product.objects.for_org(seeded).count() == 72
    p.refresh_from_db()
    assert p.sort_order == 99


def test_search_normalizes_arabic(seeded):
    qs = Product.objects.for_org(seeded)
    assert "معلقة أكل خشب ×100" in qs.search("معلقه").values_list("name", flat=True)
    assert list(qs.search("اسفنج").values_list("name", flat=True)) == []  # إسبونشة, not إسفنج
    assert qs.search("اسبونشه").count() == 1
    assert qs.search("كوب ايس").count() == 2  # multi-word, أ/آ unified


# --- pages & permissions ------------------------------------------------------------------


def test_catalog_page_lists_everything(client, seeded, sales):
    client.force_login(sales)
    html = client.get(reverse("catalog:index")).content.decode()
    assert "72 صنف" in html
    assert "أدوات المائدة الخشبية" in html
    assert 'data-sortable="product"' not in html  # sales can't reorder


def test_catalog_search_via_htmx(client, seeded, sales):
    client.force_login(sales)
    r = client.get(reverse("catalog:index"), {"q": "معلقه"}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="catalog-body")
    html = r.content.decode()
    assert "<html" not in html
    assert "معلقة أكل خشب" in html
    assert "ديتول" not in html


@pytest.mark.parametrize("role", ["viewer", "sales"])
def test_editing_requires_manager(client, seeded, make_member, role):
    client.force_login(make_member(seeded, role))
    p = Product.objects.for_org(seeded).first()
    assert client.post(reverse("catalog:product_edit", args=[p.pk]), {"name": "x"}).status_code == 403
    assert client.post(reverse("catalog:product_toggle", args=[p.pk])).status_code == 403
    assert client.post(reverse("catalog:group_create"), {"name": "x"}).status_code == 403
    assert client.post(reverse("catalog:reorder"), {"kind": "product", "ids": [p.pk]}).status_code == 403


def test_manager_adds_product_with_new_unit(client, seeded, manager):
    client.force_login(manager)
    cat = Category.objects.for_org(seeded).get(name="الفويل")
    r = client.post(
        reverse("catalog:product_create", args=[cat.pk]),
        {"name": "فويل 30 متر", "unit_name": "كرتونة صغيرة", "category": cat.pk},
        HTTP_HX_REQUEST="true",
    )
    assert r.status_code == 200 and r["HX-Retarget"] == "#catalog-body"
    p = Product.objects.get(name="فويل 30 متر")
    assert p.unit.name == "كرتونة صغيرة"
    assert p.sort_order == 3  # appended after the 2 existing foil items


def test_duplicate_product_in_category_rejected(client, seeded, manager):
    client.force_login(manager)
    cat = Category.objects.for_org(seeded).get(name="الفويل")
    r = client.post(
        reverse("catalog:product_create", args=[cat.pk]),
        {"name": "فويل 45-50 متر", "unit_name": "رول", "category": cat.pk},
    )
    assert "موجود بالفعل" in r.content.decode()
    assert Product.objects.filter(name="فويل 45-50 متر").count() == 1


def test_edit_product_name_unit_and_move_category(client, seeded, manager):
    client.force_login(manager)
    p = Product.objects.for_org(seeded).get(name="شيش كباب ×40")
    target = Category.objects.for_org(seeded).get(name="الفوط والتجهيزات")
    r = client.post(
        reverse("catalog:product_edit", args=[p.pk]),
        {"name": "شيش كباب ×50", "unit_name": "باكت", "category": target.pk},
        HTTP_HX_REQUEST="true",
    )
    assert r.status_code == 200
    p.refresh_from_db()
    assert (p.name, p.category) == ("شيش كباب ×50", target)
    assert p.history.count() == 2


def test_deactivate_product_hides_it(client, seeded, manager, sales):
    p = Product.objects.for_org(seeded).get(name="ديتول 500 ملي")
    client.force_login(manager)
    client.post(reverse("catalog:product_toggle", args=[p.pk]))
    p.refresh_from_db()
    assert not p.is_active
    client.force_login(sales)
    assert "ديتول" not in client.get(reverse("catalog:index")).content.decode()
    client.force_login(manager)
    assert "ديتول" in client.get(reverse("catalog:index"), {"show": "all"}).content.decode()


def test_group_and_category_management(client, seeded, manager):
    client.force_login(manager)
    client.post(reverse("catalog:group_create"), {"name": "  مستلزمات   المكاتب ", "description": ""})
    g = ProductGroup.objects.get(name="مستلزمات المكاتب")
    assert g.sort_order == 7
    client.post(reverse("catalog:category_create", args=[g.pk]), {"name": "أدوات مكتبية", "group": g.pk})
    c = Category.objects.get(name="أدوات مكتبية")
    assert c.group == g
    # Move an existing category into the new group
    paper = Category.objects.for_org(seeded).get(name="الورقيات")
    client.post(reverse("catalog:category_edit", args=[paper.pk]), {"name": "الورقيات", "group": g.pk})
    paper.refresh_from_db()
    assert paper.group == g and paper.sort_order == 2
    # Duplicate names rejected
    r = client.post(reverse("catalog:category_create", args=[g.pk]), {"name": "الفويل", "group": g.pk})
    assert "بنفس الاسم" in r.content.decode()
    # Toggle
    client.post(reverse("catalog:category_toggle", args=[c.pk]))
    c.refresh_from_db()
    assert not c.is_active


def test_reorder_products(client, seeded, manager):
    client.force_login(manager)
    cat = Category.objects.for_org(seeded).get(name="الفويل")
    a, b = cat.products.order_by("sort_order")
    r = client.post(reverse("catalog:reorder"), {"kind": "product", "ids": [b.pk, a.pk]})
    assert r.status_code == 204
    assert list(cat.products.order_by("sort_order")) == [b, a]


def test_reorder_rejects_mixed_parents_and_bad_input(client, seeded, manager):
    client.force_login(manager)
    p1 = Product.objects.for_org(seeded).get(name="فويل 45-50 متر")
    p2 = Product.objects.for_org(seeded).get(name="بيروسول")
    assert client.post(reverse("catalog:reorder"), {"kind": "product", "ids": [p1.pk, p2.pk]}).status_code == 400
    assert client.post(reverse("catalog:reorder"), {"kind": "nope", "ids": [p1.pk]}).status_code == 400
    assert client.post(reverse("catalog:reorder"), {"kind": "product", "ids": ["x"]}).status_code == 400


def test_reorder_groups_and_categories(client, seeded, manager):
    client.force_login(manager)
    groups = list(ProductGroup.objects.for_org(seeded))
    new = [groups[-1].pk] + [g.pk for g in groups[:-1]]
    assert client.post(reverse("catalog:reorder"), {"kind": "group", "ids": new}).status_code == 204
    assert ProductGroup.objects.for_org(seeded).first().pk == groups[-1].pk
    # PDF/section order follows: first product now comes from the moved group
    assert Product.objects.for_org(seeded).first().category.group_id == groups[-1].pk


# --- tenant isolation ---------------------------------------------------------------------


@pytest.fixture
def foreign(seeded, org_b, make_member):
    return make_member(org_b, "owner")


@pytest.mark.parametrize(
    ("name", "method", "model"),
    [
        ("catalog:product_row", "get", Product),
        ("catalog:product_edit", "get", Product),
        ("catalog:product_edit", "post", Product),
        ("catalog:product_toggle", "post", Product),
        ("catalog:product_create", "post", Category),
        ("catalog:category_edit", "post", Category),
        ("catalog:category_toggle", "post", Category),
        ("catalog:category_create", "post", ProductGroup),
        ("catalog:group_edit", "post", ProductGroup),
        ("catalog:group_toggle", "post", ProductGroup),
    ],
)
def test_cross_tenant_404(client, seeded, foreign, name, method, model):
    obj = model.objects.for_org(seeded).first()
    client.force_login(foreign)
    resp = getattr(client, method)(reverse(name, args=[obj.pk]), {"name": "hacked", "unit_name": "x"})
    assert resp.status_code == 404
    obj.refresh_from_db()
    assert obj.name != "hacked" and obj.is_active


def test_cross_tenant_reorder_rejected(client, seeded, foreign):
    cat = Category.objects.for_org(seeded).get(name="الفويل")
    ids = list(cat.products.values_list("pk", flat=True))
    client.force_login(foreign)
    assert client.post(reverse("catalog:reorder"), {"kind": "product", "ids": ids[::-1]}).status_code == 400


def test_foreign_catalog_page_is_empty(client, seeded, foreign):
    client.force_login(foreign)
    html = client.get(reverse("catalog:index")).content.decode()
    assert "سكينة خشب" not in html
    assert "0 صنف" in html


def test_cannot_move_product_into_foreign_category(client, seeded, org_b, make_member):
    other_group = ProductGroup.objects.create(organization=org_b, name="G")
    other_cat = Category.objects.create(organization=org_b, group=other_group, name="C")
    Unit.get_for(org_b, "x")
    manager_a = make_member(seeded, "manager")
    client.force_login(manager_a)
    p = Product.objects.for_org(seeded).first()
    client.post(
        reverse("catalog:product_edit", args=[p.pk]), {"name": p.name, "unit_name": "باكت", "category": other_cat.pk}
    )
    p.refresh_from_db()
    assert p.category.organization == seeded
