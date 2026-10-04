import io
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.clinical import services as clinical
from apps.doctors.models import Doctor, VisitType, WorkingPeriod
from apps.patients.models import Gender, Patient
from apps.prescriptions import categories as cats
from apps.prescriptions import services
from apps.prescriptions.models import DoctorCategory, Drug, DrugCategory, Prescription
from apps.prescriptions.specialties import SPECIALTIES
from apps.scheduling import services as booking
from apps.scheduling.models import AppointmentStatus

pytestmark = pytest.mark.usefixtures("midday")


@pytest.fixture
def world(org_a, org_b, make_member):
    call_command("import_drugs", org=org_a.slug, stdout=io.StringIO())
    doctor = Doctor.objects.create(organization=org_a, name_ar="سارة", slot_minutes=20)
    for weekday in range(7):
        WorkingPeriod.objects.create(organization=org_a, doctor=doctor, weekday=weekday,
                                     start_time="00:00", end_time="23:59")  # fmt: skip
    vt = VisitType.objects.create(organization=org_a, doctor=doctor, name_ar="كشف", duration_minutes=20,
                                  price=Decimal("300"))  # fmt: skip
    patient = Patient.objects.create(organization=org_a, full_name="منى", phone="+201001234567",
                                     gender=Gender.FEMALE, file_number=1)  # fmt: skip
    doc, reception = make_member(org_a, "doctor"), make_member(org_a, "reception")
    appt = booking.book(patient=patient, doctor=doctor, visit_type=vt, by=reception,
                        start_at=timezone.now() + timedelta(hours=1), overbook=True)  # fmt: skip
    booking.transition(appt, AppointmentStatus.ARRIVED, by=reception)
    visit = clinical.start_visit(appt, by=doc)
    return {"org": org_a, "doctor": doctor, "doc": doc, "reception": reception, "visit": visit,
            "other": make_member(org_b, "doctor")}  # fmt: skip


def _cat(world, name):
    return DrugCategory.objects.get(organization=world["org"], name=name)


def test_every_specialty_preset_is_well_formed():
    assert len(SPECIALTIES) == 29 + 24
    for key, (name, items) in SPECIALTIES.items():
        names = [n for n, _ in items]
        assert name and items, key
        assert len(names) == len(set(names)), f"duplicate category in {key}"
        assert all(lvl in ("primary", "common", "occasional", "not_typical") for _, lvl in items), key


def test_apply_specialty_keeps_the_given_order(world):
    cats.apply_specialty(world["doctor"], "internal_medicine")
    names = [e.category.name for e in cats.for_doctor(world["doctor"])]
    assert names[:4] == ["Antihypertensives", "Diabetes medications", "GI medications", "Analgesics / Antipyretics"]
    assert names[-1] == "Chemotherapy" and len(names) == 18
    world["doctor"].refresh_from_db()
    assert world["doctor"].drug_specialty == "internal_medicine"
    # categories are shared by name: the seed drugs' "Antibiotics" tag is the same row
    assert _cat(world, "Antibiotics").drugs.filter(name="Augmentin 1g").exists()


def test_category_search_is_prefix_and_restricted(world):
    nsaids = _cat(world, "NSAIDs")
    names = [d.name for d in services.search_drugs(world["org"], "c", category=nsaids)]
    assert names == ["Cataflam 50"]  # starts with "c", and only NSAIDs (not Ciprofloxacin / Concor / Controloc)
    assert {d.name for d in services.search_drugs(world["org"], "", category=nsaids)} == {
        "Brufen 400", "Cataflam 50", "Voltaren 75 amp"}  # fmt: skip
    analgesics = _cat(world, "Analgesics / Antipyretics")
    assert [d.name for d in services.search_drugs(world["org"], "p", category=analgesics)] == [
        "Panadol 500", "Panadol Extra"]  # fmt: skip
    # Arabic alias prefix works inside a category too
    assert [d.name for d in services.search_drugs(world["org"], "بنادول اك", category=analgesics)] == ["Panadol Extra"]
    # without a category: anywhere in the name, prefix matches first
    assert services.search_drugs(world["org"], "co")[0].name in ("Concor 5", "Controloc 40")


def test_builder_shows_chips_and_search_uses_the_category(client, world):
    cats.apply_specialty(world["doctor"], "internal_medicine")
    client.force_login(world["doc"])
    html = client.get(reverse("prescriptions:builder", args=[world["visit"].pk])).content.decode()
    assert 'id="rx-cats"' in html
    assert html.index(">Antihypertensives<") < html.index(">Diabetes medications<") < html.index(">Chemotherapy<")
    rx = Prescription.objects.get(visit=world["visit"])
    abx = _cat(world, "Antibiotics")
    resp = client.get(reverse("prescriptions:drug_search", args=[rx.pk]), {"drug_name": "", "category": abx.pk})
    assert "Augmentin 1g" in resp.content.decode() and "Panadol" not in resp.content.decode()
    # Enter with a category picks inside it: "f" → Flagyl/Flumox, never another category's drug
    client.post(reverse("prescriptions:item_add", args=[rx.pk]), {"drug_name": "f", "category": abx.pk})
    assert rx.items.get().drug.name in ("Flagyl 500", "Flumox 500")


def test_free_drug_added_to_catalog_lands_in_the_selected_category(client, world):
    client.force_login(world["doc"])
    client.get(reverse("prescriptions:builder", args=[world["visit"].pk]))
    rx = Prescription.objects.get(visit=world["visit"])
    abx = _cat(world, "Antibiotics")
    client.post(reverse("prescriptions:item_add", args=[rx.pk]), {"drug_name": "Xithrone 500", "free": "1"})
    item = rx.items.get()
    client.post(reverse("prescriptions:item_to_catalog", args=[item.pk]), {"category": abx.pk})
    assert Drug.objects.get(organization=world["org"], name="Xithrone 500").categories.filter(pk=abx.pk).exists()


def test_categories_settings_add_remove_reorder(client, world):
    client.force_login(world["doc"])
    url = reverse("prescriptions:categories")
    assert "اعتمد التخصص" in client.get(url).content.decode()
    client.post(url, {"action": "specialty", "specialty": "pediatrics"})
    entries = cats.for_doctor(world["doctor"])
    assert entries[0].category.name == "Pediatric Antipyretics"
    client.post(url, {"action": "add", "name": "Antibiotics"})  # existing name is reused, appended last
    assert cats.for_doctor(world["doctor"])[-1].category == _cat(world, "Antibiotics")
    second = cats.for_doctor(world["doctor"])[1]
    client.post(url, {"action": "up", "entry": second.pk})
    assert cats.for_doctor(world["doctor"])[0].pk == second.pk
    client.post(url, {"action": "remove", "entry": second.pk})
    assert not DoctorCategory.objects.filter(pk=second.pk).exists()
    assert DrugCategory.objects.filter(pk=second.category_id).exists()  # the name and drug tags stay


def test_catalog_filter_and_edit_categories(client, world):
    cats.apply_specialty(world["doctor"], "internal_medicine")
    client.force_login(world["doc"])
    gi = _cat(world, "GI medications")
    html = client.get(reverse("prescriptions:drugs"), {"category": gi.pk}).content.decode()
    assert "Nexium 40" in html and "Brufen 400" not in html
    brufen = Drug.objects.get(organization=world["org"], name="Brufen 400")
    data = {"name": "Brufen 400", "generic_name": "Ibuprofen", "form": "قرص", "strength": "400 mg",
            "default_instructions": "", "default_duration": "", "aliases_text": "بروفين",
            "categories": [_cat(world, "NSAIDs").pk, gi.pk]}  # fmt: skip
    client.post(reverse("prescriptions:drug_edit", args=[brufen.pk]), data)
    assert set(brufen.categories.values_list("name", flat=True)) == {"NSAIDs", "GI medications"}


def test_permissions_and_isolation(client, world):
    client.force_login(world["reception"])
    assert client.get(reverse("prescriptions:categories")).status_code == 403
    client.force_login(world["other"])
    cats.apply_specialty(world["doctor"], "internal_medicine")
    entry = cats.for_doctor(world["doctor"])[0]
    client.post(reverse("prescriptions:categories"), {"action": "remove", "entry": entry.pk})
    assert DoctorCategory.objects.filter(pk=entry.pk).exists()  # other clinic's doctor can't touch it
    rx = services.draft_for_visit(world["visit"])
    resp = client.get(reverse("prescriptions:drug_search", args=[rx.pk]), {"category": entry.category_id})
    assert resp.status_code == 404


def test_import_reads_categories_column(world, tmp_path):
    f = tmp_path / "drugs.csv"
    f.write_text("name,generic_name,form,categories\nNewcef 1g,Ceftriaxone,حقن,Antibiotics|Cephalosporins\n",
                 encoding="utf-8")  # fmt: skip
    call_command("import_drugs", str(f), org=world["org"].slug, stdout=io.StringIO())
    drug = Drug.objects.get(organization=world["org"], name="Newcef 1g")
    assert set(drug.categories.values_list("name", flat=True)) == {"Antibiotics", "Cephalosporins"}


def test_search_box_does_not_inherit_the_forms_outerhtml_swap(client, world):
    """Regression: the search input sits inside the add-drug form (hx-swap=outerHTML). Without its own
    hx-swap it replaced #drug-results on the first keystroke and later keystrokes showed nothing."""
    client.force_login(world["doc"])
    html = client.get(reverse("prescriptions:builder", args=[world["visit"].pk])).content.decode()
    tag = html[html.index('id="rx-search"') : html.index(">", html.index('id="rx-search"'))]
    assert 'hx-target="#drug-results"' in tag and 'hx-swap="innerHTML"' in tag
