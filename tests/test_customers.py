import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse

from apps.core.phones import to_e164, to_local
from apps.customers.models import ChannelType, Contact, ContactChannel, Customer, CustomerKind


@pytest.fixture
def owner(org_a, make_member):
    return make_member(org_a, "owner")


@pytest.fixture
def sales(org_a, make_member):
    return make_member(org_a, "sales")


@pytest.fixture
def viewer(org_a, make_member):
    return make_member(org_a, "viewer")


@pytest.fixture
def jaz(org_a):
    travco = Customer.objects.create(organization=org_a, name="Travco", kind=CustomerKind.GROUP)
    return Customer.objects.create(organization=org_a, name="Jaz Hotels", kind=CustomerKind.CHAIN, parent=travco)


# --- phones -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["01060777030", "1060777030", "1060777030.0", "+201060777030", "0106 077 7030", "٠١٠٦٠٧٧٧٠٣٠", "00201060777030"],
)
def test_to_e164_accepts_egyptian_forms(raw):
    assert to_e164(raw) == "+201060777030"


def test_to_e164_rejects_garbage():
    with pytest.raises(ValidationError):
        to_e164("12345")


def test_to_local_round_trip():
    assert to_local("+201060777030") == "01060777030"


# --- model rules --------------------------------------------------------------------------


def test_recipient_lines_follow_tree(org_a, jaz):
    hotel = Customer.objects.create(organization=org_a, name="Jaz Aquamarine", kind=CustomerKind.HOTEL, parent=jaz)
    assert hotel.recipient_lines() == [
        ("السادة / شركة", "Travco"),
        ("سلسلة الفنادق", "Jaz Hotels"),
        ("فندق", "Jaz Aquamarine"),
    ]


def test_standalone_hotel_recipient(org_a):
    c = Customer.objects.create(organization=org_a, name="ريجينا", kind=CustomerKind.HOTEL)
    assert c.recipient_lines() == [("السادة / فندق", "ريجينا")]


def test_tree_cycle_is_rejected(org_a, jaz):
    travco = jaz.parent
    travco.parent = jaz
    with pytest.raises(ValidationError):
        travco.full_clean()


def test_duplicate_top_level_names_blocked(org_a):
    from django.db import IntegrityError

    Customer.objects.create(organization=org_a, name="ريجينا")
    with pytest.raises(IntegrityError):
        Customer.objects.create(organization=org_a, name="ريجينا")


def test_only_one_primary_contact_and_channel(org_a, jaz):
    a = Contact.objects.create(organization=org_a, customer=jaz, name="A", is_primary=True)
    b = Contact.objects.create(organization=org_a, customer=jaz, name="B", is_primary=True)
    a.refresh_from_db()
    assert not a.is_primary and b.is_primary
    c1 = ContactChannel.objects.create(
        organization=org_a, contact=b, type="whatsapp", value="01000000001", is_primary=True
    )
    ContactChannel.objects.create(organization=org_a, contact=b, type="whatsapp", value="01000000002", is_primary=True)
    c1.refresh_from_db()
    assert not c1.is_primary
    assert c1.value == "+201000000001"


def test_search_is_arabic_normalized(org_a):
    c = Customer.objects.create(organization=org_a, name="فندق الأميرة")
    Contact.objects.create(organization=org_a, customer=c, name="محمد عبد الغنى")
    qs = Customer.objects.for_org(org_a)
    assert list(qs.search("الاميره")) == [c]
    assert list(qs.search("عبد الغني")) == [c]


def test_search_by_phone(org_a, jaz):
    ct = Contact.objects.create(organization=org_a, customer=jaz, name="Ahmed")
    ContactChannel.objects.create(organization=org_a, contact=ct, type="whatsapp", value="01060777030")
    assert list(Customer.objects.for_org(org_a).search("01060777030")) == [jaz]


# --- views & permissions ------------------------------------------------------------------


def test_list_and_search(client, viewer, jaz):
    client.force_login(viewer)
    resp = client.get(reverse("customers:list"), {"q": "jaz"})
    assert resp.status_code == 200
    assert "Jaz Hotels" in resp.content.decode()
    resp = client.get(reverse("customers:list"), {"q": "jaz"}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="customer-rows")
    assert resp.status_code == 200
    assert "<html" not in resp.content.decode()


def test_viewer_cannot_edit(client, viewer, jaz):
    client.force_login(viewer)
    assert client.get(reverse("customers:create")).status_code == 403
    assert client.post(reverse("customers:edit", args=[jaz.pk]), {"name": "x"}).status_code == 403
    assert client.post(reverse("customers:contact_create", args=[jaz.pk]), {"name": "x"}).status_code == 403
    assert client.get(reverse("customers:detail", args=[jaz.pk])).status_code == 200


def test_sales_creates_customer_with_parent(client, sales, jaz, org_a):
    client.force_login(sales)
    resp = client.post(
        reverse("customers:create"),
        {"name": "  Jaz   Aquamarine ", "kind": "resort", "parent": jaz.pk, "tax_id": "25974098"},
    )
    assert resp.status_code == 302
    c = Customer.objects.get(name="Jaz Aquamarine")
    assert c.parent == jaz and c.organization == org_a
    assert c.tax_id == "025974098"


def test_create_duplicate_name_shows_error(client, sales, org_a):
    Customer.objects.create(organization=org_a, name="ريجينا")
    client.force_login(sales)
    resp = client.post(reverse("customers:create"), {"name": "ريجينا", "kind": "hotel"})
    assert resp.status_code == 200
    assert "بنفس الاسم" in resp.content.decode()


def test_edit_cannot_make_cycle(client, sales, jaz):
    client.force_login(sales)
    travco = jaz.parent
    resp = client.post(
        reverse("customers:edit", args=[travco.pk]), {"name": "Travco", "kind": "group", "parent": jaz.pk}
    )
    assert resp.status_code == 200  # invalid choice: descendants aren't offered as parents
    travco.refresh_from_db()
    assert travco.parent is None


def test_dod_three_contacts_stored_e164(client, sales, jaz):
    """Phase 2 DoD: Travco → Jaz Hotels with Ahmed (WA+Email), Mohamed (WA), Sara (Email)."""
    client.force_login(sales)
    url = reverse("customers:contact_create", args=[jaz.pk])
    hx = {"HTTP_HX_REQUEST": "true"}
    r = client.post(
        url,
        {"name": "Ahmed", "whatsapp": "0100 000 0101", "email": "Ahmed@Jaz.example", "preferred_channel": "both"},
        **hx,
    )
    assert r.status_code == 200 and r["HX-Retarget"] == "#contacts"
    client.post(url, {"name": "Mohamed", "whatsapp": "1000000102", "preferred_channel": "whatsapp"}, **hx)
    client.post(url, {"name": "Sara", "email": "sara@jaz.example", "preferred_channel": "email"}, **hx)

    contacts = {c.name: c for c in jaz.contacts.prefetch_related("channels")}
    assert set(contacts) == {"Ahmed", "Mohamed", "Sara"}
    assert contacts["Ahmed"].is_primary  # first contact becomes primary
    assert contacts["Ahmed"].primary_channel(ChannelType.WHATSAPP).value == "+201000000101"
    assert contacts["Ahmed"].primary_channel(ChannelType.EMAIL).value == "ahmed@jaz.example"
    assert contacts["Mohamed"].primary_channel(ChannelType.WHATSAPP).value == "+201000000102"
    assert contacts["Sara"].primary_channel(ChannelType.WHATSAPP) is None


def test_contact_form_errors_come_back_inline(client, sales, jaz):
    client.force_login(sales)
    r = client.post(
        reverse("customers:contact_create", args=[jaz.pk]),
        {"name": "X", "whatsapp": "123", "preferred_channel": "whatsapp"},
        HTTP_HX_REQUEST="true",
    )
    assert r.status_code == 200
    assert "HX-Retarget" not in r
    assert "مش صحيح" in r.content.decode()
    assert not jaz.contacts.exists()


def test_preferred_channel_requires_value(client, sales, jaz):
    client.force_login(sales)
    r = client.post(reverse("customers:contact_create", args=[jaz.pk]), {"name": "X", "preferred_channel": "email"})
    assert r.status_code == 400
    assert not jaz.contacts.exists()


def test_edit_contact_replaces_and_clears_channels(client, sales, jaz, org_a):
    ct = Contact.objects.create(organization=org_a, customer=jaz, name="Ahmed")
    ContactChannel.objects.create(organization=org_a, contact=ct, type="whatsapp", value="01000000101", is_primary=True)
    ContactChannel.objects.create(organization=org_a, contact=ct, type="email", value="a@x.com", is_primary=True)
    client.force_login(sales)
    client.post(
        reverse("customers:contact_edit", args=[ct.pk]),
        {"name": "Ahmed", "whatsapp": "01000000999", "email": "", "preferred_channel": "whatsapp"},
    )
    types = {(c.type, c.value) for c in ct.channels.all()}
    assert types == {("whatsapp", "+201000000999")}


def test_extra_channel_and_delete(client, sales, jaz, org_a):
    ct = Contact.objects.create(organization=org_a, customer=jaz, name="Ahmed")
    client.force_login(sales)
    client.post(reverse("customers:channel_add", args=[ct.pk]), {"type": "whatsapp", "value": "01000000101"})
    client.post(reverse("customers:channel_add", args=[ct.pk]), {"type": "whatsapp", "value": "01000000102"})
    first, second = ct.channels.order_by("id")
    assert first.is_primary and not second.is_primary
    client.post(reverse("customers:channel_delete", args=[first.pk]))
    second.refresh_from_db()
    assert second.is_primary


def test_history_is_recorded_with_user(client, sales, jaz):
    client.force_login(sales)
    client.post(reverse("customers:edit", args=[jaz.pk]), {"name": "Jaz Hotels", "kind": "chain", "city": "الغردقة"})
    latest = jaz.history.latest()
    assert latest.city == "الغردقة"
    assert latest.history_user == sales


# --- tenant isolation (every view) ------------------------------------------------------


@pytest.fixture
def foreign(org_a, org_b, make_member):
    """Data in org A, logged-in owner from org B."""
    cust = Customer.objects.create(organization=org_a, name="سري")
    contact = Contact.objects.create(organization=org_a, customer=cust, name="Secret Person")
    ch = ContactChannel.objects.create(organization=org_a, contact=contact, type="whatsapp", value="01000000999")
    return make_member(org_b, "owner"), cust, contact, ch


@pytest.mark.parametrize(
    ("name", "method", "which"),
    [
        ("customers:detail", "get", "cust"),
        ("customers:edit", "get", "cust"),
        ("customers:edit", "post", "cust"),
        ("customers:toggle", "post", "cust"),
        ("customers:history", "get", "cust"),
        ("customers:contacts", "get", "cust"),
        ("customers:contact_create", "post", "cust"),
        ("customers:contact_edit", "post", "contact"),
        ("customers:contact_toggle", "post", "contact"),
        ("customers:channel_add", "post", "contact"),
        ("customers:channel_delete", "post", "ch"),
    ],
)
def test_cross_tenant_access_is_404(client, foreign, name, method, which):
    user, cust, contact, ch = foreign
    client.force_login(user)
    obj = {"cust": cust, "contact": contact, "ch": ch}[which]
    resp = getattr(client, method)(reverse(name, args=[obj.pk]), {"name": "hacked", "type": "email", "value": "a@b.c"})
    assert resp.status_code == 404
    cust.refresh_from_db()
    contact.refresh_from_db()
    assert cust.name == "سري" and cust.is_active
    assert contact.name == "Secret Person" and contact.is_active
    assert ContactChannel.objects.filter(pk=ch.pk).exists()


def test_list_hides_other_org(client, foreign):
    user, cust, *_ = foreign
    client.force_login(user)
    link = reverse("customers:detail", args=[cust.pk])
    assert link not in client.get(reverse("customers:list")).content.decode()
    assert link not in client.get(reverse("customers:list"), {"q": "سري"}).content.decode()


def test_parent_choices_exclude_other_org(client, foreign):
    user, cust, *_ = foreign
    client.force_login(user)
    resp = client.post(reverse("customers:create"), {"name": "Mine", "kind": "hotel", "parent": cust.pk})
    assert resp.status_code == 200
    assert not Customer.objects.filter(name="Mine").exists()


def test_create_customer_via_other_org_parent_query_is_ignored(client, foreign):
    user, cust, *_ = foreign
    client.force_login(user)
    html = client.get(reverse("customers:create"), {"parent": cust.pk}).content.decode()
    assert "سري" not in html


# --- commands -----------------------------------------------------------------------------


def test_seed_demo_and_remove(org_a):
    call_command("seed_demo", org="albarq")
    call_command("seed_demo", org="albarq")  # idempotent
    jaz = Customer.objects.get(name="Jaz Hotels")
    assert jaz.parent.name == "Travco"
    assert jaz.contacts.count() == 3
    assert Customer.objects.filter(name="Jaz Aquamarine", parent=jaz).exists()
    call_command("seed_demo", org="albarq", remove=True)
    assert not Customer.objects.filter(name__in=["Travco", "Jaz Hotels", "Jaz Aquamarine"]).exists()


@pytest.fixture
def workbook(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.title = "شغل "
    ws = wb.create_sheet("ارقام عملاء وضريبى")
    ws.append(["الرقم الضريبى", "اسم الشركة", "رقم موبايل / واتساب", "الاسم", "المنصب"])
    ws.append([200043226.0, "بالم بيتش", "1006170165/1005406564", "محمد شوقى -ابراهيم", "م مشتريات"])
    ws.append([25974098.0, "يلو سي سكوب", None, None, None])
    ws.append([200149954.0, "فندق ريجينا", 1001779214.0, "محمد عبد الغنى", "م مشتريات"])
    ws.append([204890659.0, "الشركة العربية للاستثمار", 1067796541.0, "مصطفى", None])
    ws.append([452571944.0, "شتايجن بورجر", None, None, None])
    ws.append([100401740.0, "جولد سكاى", 1223844352.0, "عادل الصياد", "مندوب"])
    ws.append([None, "الماسة", 1003007055.0, "إبراهيم عزت", "مورد"])
    ws.append([None, None, 1007039867.0, "محمد عزت", "مورد"])
    ws.append([None, "مانو بلاست", 1120288801.0, "احمد مانو", None])
    ws.append([204933722.0, "كريازى", 1281000027.0, "فندق كريازى", "م مشتريات"])
    path = tmp_path / "customers.xlsx"
    wb.save(path)
    return path


def test_import_customers(org_a, workbook):
    call_command("import_customers", str(workbook), org="albarq")
    names = set(Customer.objects.for_org(org_a).values_list("name", flat=True))
    assert names == {"بالم بيتش", "يلو سي سكوب", "ريجينا", "العربية للاستثمار", "شتايجن بورجر", "كريازى"}

    palm = Customer.objects.get(name="بالم بيتش")
    contacts = {c.name: c for c in palm.contacts.all()}
    assert set(contacts) == {"محمد شوقى", "ابراهيم"}
    assert contacts["محمد شوقى"].primary_channel("whatsapp").value == "+201006170165"
    assert contacts["ابراهيم"].primary_channel("whatsapp").value == "+201005406564"
    assert contacts["محمد شوقى"].job_title == "مدير مشتريات"

    assert Customer.objects.get(name="يلو سي سكوب").tax_id == "025974098"
    assert Customer.objects.get(name="ريجينا").kind == CustomerKind.HOTEL
    assert Customer.objects.get(name="العربية للاستثمار").kind == CustomerKind.COMPANY
    assert Customer.objects.get(name="شتايجن بورجر").kind == CustomerKind.RESTAURANT
    assert Customer.objects.get(name="كريازى").contacts.get().name == "إدارة المشتريات"

    # Idempotent
    call_command("import_customers", str(workbook), org="albarq")
    assert Customer.objects.for_org(org_a).count() == 6
    assert Contact.objects.for_org(org_a).count() == 5
    assert ContactChannel.objects.for_org(org_a).count() == 5


def test_import_dry_run_writes_nothing(org_a, workbook):
    call_command("import_customers", str(workbook), org="albarq", dry_run=True)
    assert not Customer.objects.exists()
