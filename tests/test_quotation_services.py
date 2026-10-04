import threading
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.db import connection

from apps.catalog.models import Category, Product
from apps.customers.codes import suggest_code
from apps.customers.models import Contact, Customer
from apps.quotations import services
from apps.quotations.models import ImmutableQuotationError, Quotation, QuotationItem, QuotationStatus

SEED = Path(settings.BASE_DIR) / "docs" / "plan" / "seed" / "products.csv"


@pytest.fixture
def world(org_a, make_member):
    call_command("import_products", str(SEED), org="albarq")
    user = make_member(org_a, "sales")
    regina = Customer.objects.create(organization=org_a, name="ريجينا", code="RG")
    contact = Contact.objects.create(organization=org_a, customer=regina, name="محمد")
    return org_a, user, regina, contact


def _ready_draft(org, user, customer, contact, day=date(2026, 9, 24), n=2):
    q = services.new_draft(org, user, customer, contact)
    q.issue_date = day
    q.valid_until = day.replace(day=30)
    q.save()
    for p in Product.objects.for_org(org)[:n]:
        item, _ = services.add_product(q, p)
        item.unit_price = Decimal("65")
        item.save()
    return q


# --- codes ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "code"),
    [("ريجينا", "RI"), ("Jaz Hotels", "JA"), ("بالم بيتش", "BA"), ("السندباد", "SN"), ("شتايجن بورجر", "SH")],
)
def test_suggest_code(name, code):
    assert suggest_code(name) == code


def test_suggest_code_avoids_taken():
    assert suggest_code("مورين", {"MO"}) != "MO"


def test_customer_gets_code_automatically(org_a):
    a = Customer.objects.create(organization=org_a, name="مورين")
    b = Customer.objects.create(organization=org_a, name="موفمبيك القصير")
    assert a.code == "MO" and b.code and b.code != "MO"


# --- defaults ------------------------------------------------------------------------------


def test_new_draft_defaults(world, settings):
    org, user, *_ = world
    q = services.new_draft(org, user)
    assert q.status == QuotationStatus.DRAFT
    assert q.valid_until.month == q.issue_date.month
    assert q.valid_until.day >= 28  # last day of the month
    assert len(q.terms) == 0 or isinstance(q.terms, list)


def test_valid_until_end_of_month_and_days(org_a):
    s = org_a.settings
    assert s.default_valid_until(date(2026, 2, 10)) == date(2026, 2, 28)
    assert s.default_valid_until(date(2026, 9, 24)) == date(2026, 9, 30)
    s.validity_mode = "days"
    s.default_validity_days = 15
    assert s.default_valid_until(date(2026, 9, 24)) == date(2026, 10, 9)


# --- items ---------------------------------------------------------------------------------


def test_add_product_is_idempotent_and_ordered(world):
    org, user, regina, contact = world
    q = services.new_draft(org, user, regina, contact)
    detol = Product.objects.for_org(org).get(name="ديتول 500 ملي")
    knife = Product.objects.for_org(org).get(name="سكينة خشب ×100")
    services.add_product(q, detol)
    services.add_product(q, knife)
    _, created = services.add_product(q, knife)
    assert not created
    assert [i.description for i in q.items.all()] == ["سكينة خشب ×100", "ديتول 500 ملي"]  # catalog/PDF order


def test_add_whole_category(world):
    org, user, regina, contact = world
    q = services.new_draft(org, user, regina, contact)
    cat = Category.objects.for_org(org).get(name="أدوات المائدة الخشبية")
    assert services.add_category(q, cat) == 8
    assert services.add_category(q, cat) == 0


def test_custom_item_with_and_without_catalog(world):
    org, user, regina, contact = world
    q = services.new_draft(org, user, regina, contact)
    foil = Category.objects.for_org(org).get(name="الفويل")
    services.add_custom(q, description="فويل 10 متر", unit_name="رول", category=foil, add_to_catalog=True)
    assert Product.objects.filter(name="فويل 10 متر", category=foil).exists()
    one_off = services.add_custom(q, description="صنف مخصوص", unit_name="قطعة")
    assert one_off.product is None and one_off.category_name == "أصناف أخرى"
    assert list(q.items.all())[-1] == one_off  # "other" section prints last


# --- numbering -----------------------------------------------------------------------------


def test_number_format_and_same_day_suffix(world):
    org, user, regina, contact = world
    q1 = services.finalize(_ready_draft(org, user, regina, contact), user)
    q2 = services.finalize(_ready_draft(org, user, regina, contact), user)
    q3 = services.finalize(_ready_draft(org, user, regina, contact, day=date(2026, 10, 1)), user)
    assert q1.number == "RG-2026/09/24"
    assert q2.number == "RG-2026/09/24-2"
    assert q3.number == "RG-2026/10/01"


def test_other_customer_same_day_has_own_series(world):
    org, user, regina, contact = world
    other = Customer.objects.create(organization=org, name="مورين", code="MO")
    oc = Contact.objects.create(organization=org, customer=other, name="x")
    services.finalize(_ready_draft(org, user, regina, contact), user)
    q = services.finalize(_ready_draft(org, user, other, oc), user)
    assert q.number == "MO-2026/09/24"


@pytest.mark.django_db(transaction=True)
def test_concurrent_finalize_never_duplicates(org_a, make_member):
    call_command("import_products", str(SEED), org="albarq")
    user = make_member(org_a, "sales")
    regina = Customer.objects.create(organization=org_a, name="ريجينا", code="RG")
    contact = Contact.objects.create(organization=org_a, customer=regina, name="محمد")
    drafts = [_ready_draft(org_a, user, regina, contact) for _ in range(6)]
    barrier = threading.Barrier(len(drafts))
    errors = []

    def worker(q):
        try:
            barrier.wait()
            services.finalize(q, user)
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(q,)) for q in drafts]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    numbers = sorted(Quotation.objects.values_list("number", flat=True))
    assert len(set(numbers)) == 6
    assert "RG-2026/09/24" in numbers and "RG-2026/09/24-6" in numbers


# --- finalize rules ------------------------------------------------------------------------


def test_finalize_problems(world):
    org, user, regina, contact = world
    q = services.new_draft(org, user)
    with pytest.raises(services.FinalizeError) as e:
        services.finalize(q, user)
    assert len(e.value.problems) >= 3  # customer, contact, items
    q.customer, q.contact = regina, contact
    q.save()
    _item, _ = services.add_product(q, Product.objects.for_org(org).first())
    with pytest.raises(services.FinalizeError) as e:
        services.finalize(q, user)
    assert any("سعر" in p for p in e.value.problems)


def test_finalize_snapshots_and_locks(world):
    org, user, regina, contact = world
    q = services.finalize(_ready_draft(org, user, regina, contact), user)
    assert q.status == QuotationStatus.FINALIZED
    assert q.customer_snapshot["recipient_lines"] == [["السادة / فندق", "ريجينا"]]
    assert q.contact_snapshot["name"] == "محمد"
    assert q.items_count == 2
    item = q.items.first()
    item.unit_price = Decimal("1")
    with pytest.raises(ImmutableQuotationError):
        item.save()
    with pytest.raises(ImmutableQuotationError):
        item.delete()
    with pytest.raises(services.FinalizeError):
        services.finalize(q, user)


def test_revision_keeps_number(world):
    org, user, regina, contact = world
    original = services.finalize(_ready_draft(org, user, regina, contact), user)
    rev = services.create_revision(original, user)
    assert rev.is_editable and rev.items.count() == 2
    it = rev.items.first()
    it.unit_price = Decimal("70")
    it.save()
    rev = services.finalize(rev, user)
    assert (rev.number, rev.revision, rev.display_number) == ("RG-2026/09/24", 1, "RG-2026/09/24-R1")
    original.refresh_from_db()
    assert original.status == QuotationStatus.SUPERSEDED
    rev2 = services.finalize(services.create_revision(rev, user), user)
    assert rev2.display_number == "RG-2026/09/24-R2"


def test_duplicate_gets_new_number(world):
    org, user, regina, contact = world
    original = services.finalize(_ready_draft(org, user, regina, contact), user)
    copy = services.duplicate(original, user)
    copy.issue_date = date(2026, 9, 24)
    copy.save()
    assert services.finalize(copy, user).number == "RG-2026/09/24-2"


# --- price memory --------------------------------------------------------------------------


def test_price_hints(world):
    org, user, regina, contact = world
    other = Customer.objects.create(organization=org, name="مورين", code="MO")
    oc = Contact.objects.create(organization=org, customer=other, name="x")
    p = Product.objects.for_org(org).first()

    q1 = _ready_draft(org, user, regina, contact, day=date(2026, 8, 1), n=0)
    services.add_product(q1, p)
    q1.items.update(unit_price=Decimal("60"))
    services.finalize(q1, user)

    q2 = _ready_draft(org, user, other, oc, day=date(2026, 9, 1), n=0)
    services.add_product(q2, p)
    q2.items.update(unit_price=Decimal("65"))
    services.finalize(q2, user)

    draft = services.new_draft(org, user, regina, contact)
    hint = services.price_hints(draft, [p.pk])[p.pk]
    assert hint.customer_price == Decimal("60") and hint.customer_date == date(2026, 8, 1)
    assert hint.last_price == Decimal("65")


def test_copy_last_for_customer(world):
    org, user, regina, contact = world
    services.finalize(_ready_draft(org, user, regina, contact, n=3), user)
    draft = services.new_draft(org, user, regina, contact)
    assert services.copy_last_for_customer(draft) is not None
    assert draft.items.count() == 3
    assert all(i.unit_price == Decimal("65") for i in draft.items.all())
    assert QuotationItem.objects.filter(quotation=draft).count() == 3
