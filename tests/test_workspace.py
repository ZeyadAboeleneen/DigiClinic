"""Doctor workspace: snapshot, what changed, compare/reuse a previous visit, quick-add chips, smart defaults, quick
phrases, in-place finish and the "next patient ready" poll."""

import io
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.clinical import services as clinical
from apps.clinical import workspace
from apps.clinical.models import Vitals
from apps.doctors.models import Doctor, VisitType, WorkingPeriod
from apps.patients.models import Gender, Patient
from apps.prescriptions import services as rx_services
from apps.prescriptions.models import Drug
from apps.scheduling import services as booking
from apps.scheduling.models import AppointmentStatus

pytestmark = pytest.mark.usefixtures("midday")


def _arrive(w, patient, hours):
    appt = booking.book(patient=patient, doctor=w["doctor"], visit_type=w["vt"], by=w["reception"],
                        start_at=timezone.now() + timedelta(hours=hours), overbook=True)  # fmt: skip
    booking.transition(appt, AppointmentStatus.ARRIVED, by=w["reception"])
    return appt


@pytest.fixture
def w(org_a, org_b, make_member):
    """A patient with one finished visit (vitals, diagnosis, final prescription) and a second visit now open."""
    call_command("import_drugs", org=org_a.slug, stdout=io.StringIO())
    doctor = Doctor.objects.create(organization=org_a, name_ar="سارة", slot_minutes=20)
    for weekday in range(7):
        WorkingPeriod.objects.create(organization=org_a, doctor=doctor, weekday=weekday,
                                     start_time="00:00", end_time="23:59")  # fmt: skip
    vt = VisitType.objects.create(organization=org_a, doctor=doctor, name_ar="كشف", duration_minutes=20,
                                  price=Decimal("300"))  # fmt: skip
    patient = Patient.objects.create(organization=org_a, full_name="محمد أحمد", phone="+201001234567",
                                     gender=Gender.MALE, file_number=1)  # fmt: skip
    w = {"org": org_a, "doctor": doctor, "vt": vt, "patient": patient, "doc": make_member(org_a, "doctor"),
         "reception": make_member(org_a, "reception"), "other": make_member(org_b, "doctor")}  # fmt: skip
    first = clinical.start_visit(_arrive(w, patient, 1), by=w["doc"])
    fields = {"chief_complaint": "صداع منذ يومين", "examination": "Chest clear", "diagnosis": "ضغط مرتفع",
              "notes": "قلل الملح", "followup_after_days": "14"}  # fmt: skip
    for field, value in fields.items():
        clinical.save_field(first, field, value, by=w["doc"])
    Vitals.objects.create(organization=org_a, visit=first, weight_kg=Decimal("80"), bp_systolic=130, bp_diastolic=80)
    rx = rx_services.draft_for_visit(first)
    aug = Drug.objects.get(organization=org_a, name="Augmentin 1g")
    rx_services.add_item(rx, drug=aug, instructions="قرص كل 12 ساعة", duration="7 أيام")
    rx_services.finalize(rx, by=w["doc"])
    clinical.finish_visit(first, by=w["doc"])
    second = clinical.start_visit(_arrive(w, patient, 2), by=w["doc"])
    Vitals.objects.create(organization=org_a, visit=second, weight_kg=Decimal("77"), bp_systolic=145, bp_diastolic=90)
    return {**w, "first": first, "second": second, "aug": aug}


def test_snapshot_and_what_changed(client, w):
    snap = workspace.snapshot(w["second"])
    assert snap["prev"] == w["first"]
    assert [it.drug_name for it in snap["prev_rx"].items.all()] == ["Augmentin 1g"]
    changes = {c.label: (c.before, c.after, c.tone) for c in snap["changes"]}
    assert changes["الوزن"] == ("80", "77 kg", "down")
    assert changes["الضغط"] == ("130/80", "145/90", "up")
    assert "السكر" not in changes  # not recorded both times → not shown, never guessed
    client.force_login(w["doc"])
    page = client.get(reverse("clinical:visit", args=[w["second"].pk])).content.decode()
    assert "ضغط مرتفع" in page and "Augmentin 1g" in page and "اتغير من الزيارة اللي فاتت" in page


def test_first_visit_has_no_changes(w):
    assert workspace.changes_since(w["first"], None) == []


def test_compare_shows_both_visits_and_reuse_copies_the_chosen_parts(client, w):
    client.force_login(w["doc"])
    clinical.save_field(w["second"], "chief_complaint", "دوخة", by=w["doc"])
    html = client.get(reverse("clinical:visit_compare", args=[w["second"].pk, w["first"].pk])).content.decode()
    assert "صداع منذ يومين" in html and "دوخة" in html and "Augmentin 1g" in html
    url = reverse("clinical:visit_reuse", args=[w["second"].pk])
    data = {"source": w["first"].pk, "fields": ["chief_complaint", "diagnosis", "prescription"]}
    resp = client.post(url, data)
    assert resp.status_code == 204 and resp["HX-Refresh"] == "true"
    w["second"].refresh_from_db()
    assert w["second"].chief_complaint == "دوخة\nصداع منذ يومين"  # written text kept, old one appended
    assert w["second"].diagnosis == "ضغط مرتفع" and w["second"].notes == ""  # notes were not chosen
    rx = rx_services.current_for_visit(w["second"])
    assert [it.instructions for it in rx.items.all()] == ["قرص كل 12 ساعة"]
    client.post(url, data)  # twice: nothing duplicated
    w["second"].refresh_from_db()
    assert w["second"].chief_complaint.count("صداع منذ يومين") == 1 and rx.items.count() == 1


def test_reuse_refuses_another_patients_visit(client, w):
    other = Patient.objects.create(organization=w["org"], full_name="س", phone="+201001234599", file_number=2)
    foreign = clinical.start_visit(_arrive(w, other, 3), by=w["doc"])
    clinical.save_field(foreign, "diagnosis", "سري", by=w["doc"])
    client.force_login(w["doc"])
    client.post(reverse("clinical:visit_reuse", args=[w["second"].pk]), {"source": foreign.pk, "fields": ["diagnosis"]})
    w["second"].refresh_from_db()
    assert w["second"].diagnosis == ""
    assert client.get(reverse("clinical:visit_compare", args=[w["second"].pk, foreign.pk])).status_code == 404


def test_compare_and_reuse_are_tenant_scoped(client, w):
    client.force_login(w["other"])
    assert client.get(reverse("clinical:visit_compare", args=[w["second"].pk, w["first"].pk])).status_code == 404
    url = reverse("clinical:visit_reuse", args=[w["second"].pk])
    assert client.post(url, {"source": w["first"].pk, "fields": ["diagnosis"]}).status_code == 404


def test_quick_add_chips_and_smart_defaults(client, w):
    client.force_login(w["doc"])
    html = client.get(reverse("prescriptions:builder", args=[w["second"].pk])).content.decode()
    assert "أدوية المريض" in html and "كرر آخر روشتة" in html
    rx = rx_services.current_for_visit(w["second"])
    # picking the drug from search: the doctor's own last dose/duration, not the catalog default
    client.post(reverse("prescriptions:item_add", args=[rx.pk]), {"drug": w["aug"].pk})
    item = rx.items.get()
    assert (item.instructions, item.duration) == ("قرص كل 12 ساعة", "7 أيام")
    html = client.get(reverse("prescriptions:builder", args=[w["second"].pk])).content.decode()
    assert "أدوية المريض" not in html  # already on the prescription → chip hidden


def test_favorites_need_repeated_use(w):
    assert rx_services.favorites_for_doctor(w["doctor"]) == []  # used once
    rx = rx_services.draft_for_visit(w["second"])
    rx_services.add_item(rx, drug=w["aug"], instructions="قرص كل 8 ساعات")
    rx_services.finalize(rx, by=w["doc"])
    [fav] = rx_services.favorites_for_doctor(w["doctor"])
    assert (fav.drug_id, fav.instructions) == (w["aug"].pk, "قرص كل 8 ساعات")  # the latest usage


def test_quick_phrases_learn_from_the_doctors_notes(w):
    clinical.save_field(w["second"], "chief_complaint", "صداع منذ يومين، دوخة", by=w["doc"])
    phrases = workspace.quick_phrases(w["doctor"], "chief_complaint")
    assert phrases[0] == "صداع منذ يومين"  # used twice
    assert len(phrases) <= 8


def test_diagnosis_suggestions_most_used_first(w):
    clinical.save_field(w["second"], "diagnosis", "برد، ضغط مرتفع", by=w["doc"])
    assert clinical.diagnosis_suggestions(w["doctor"])[0] == "ضغط مرتفع"


def test_finish_in_place_then_next_patient_ready(client, w):
    client.force_login(w["doc"])
    html = client.post(reverse("clinical:visit_finish", args=[w["second"].pk]), HTTP_HX_REQUEST="true").content.decode()
    assert "الكشف خلص" in html and "مستني المريض الجاي" in html
    poll = reverse("clinical:poll")
    assert "مريض جديد جاهز" not in client.get(poll).content.decode()
    nxt = Patient.objects.create(organization=w["org"], full_name="التالي", phone="+201001234588", file_number=3)
    appt = _arrive(w, nxt, 3)
    booking.transition(appt, AppointmentStatus.IN_CONSULTATION, by=w["reception"])  # reception moves them in
    assert "مريض جديد جاهز" in client.get(poll).content.decode()
    client.force_login(w["reception"])
    assert client.get(poll).status_code == 403
