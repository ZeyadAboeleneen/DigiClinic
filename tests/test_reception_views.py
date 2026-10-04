import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import Payment
from apps.clinical.models import Vitals
from apps.doctors.models import Doctor, VisitType, WorkingPeriod
from apps.patients.models import Gender, Patient
from apps.scheduling import services as booking
from apps.scheduling.models import AppointmentStatus


@pytest.fixture
def world(org_a, org_b, make_member):
    doctor = Doctor.objects.create(organization=org_a, name_ar="سارة", slot_minutes=20)
    for weekday in range(7):
        WorkingPeriod.objects.create(organization=org_a, doctor=doctor, weekday=weekday,
                                     start_time="00:00", end_time="23:59")  # fmt: skip
    vt = VisitType.objects.create(organization=org_a, doctor=doctor, name_ar="كشف", duration_minutes=20,
                                  price=Decimal("300"))  # fmt: skip
    patient = Patient.objects.create(organization=org_a, full_name="منى سعيد", phone="+201001234567",
                                     gender=Gender.FEMALE, file_number=1)  # fmt: skip
    reception = make_member(org_a, "reception")
    start = (timezone.now() + timedelta(hours=1)).replace(second=0, microsecond=0)
    start = start.replace(minute=(start.minute // 20) * 20)
    appt = booking.book(patient=patient, doctor=doctor, visit_type=vt, by=reception, start_at=start, overbook=True)
    return {
        "org": org_a, "doctor": doctor, "vt": vt, "patient": patient, "appt": appt, "reception": reception,
        "viewer": make_member(org_a, "viewer"), "admin": make_member(org_a, "admin"),
        "other": make_member(org_b, "owner"),
    }  # fmt: skip


def test_reception_screen_permissions(client, world):
    for role in ("viewer", "admin"):
        client.force_login(world[role])
        assert client.get(reverse("reception:today")).status_code == 403
        assert client.get(reverse("billing:cashbox")).status_code == 403
    client.force_login(world["reception"])
    resp = client.get(reverse("reception:today"))
    assert resp.status_code == 200 and "منى سعيد" in resp.content.decode()
    assert client.get(reverse("billing:cashbox")).status_code == 200


def test_polling_returns_204_when_nothing_changed(client, world):
    client.force_login(world["reception"])
    html = client.get(reverse("reception:rows")).content.decode()
    sig = re.search(r'id="rows-sig" value="(\w+)"', html).group(1)
    assert client.get(reverse("reception:rows"), {"sig": sig}).status_code == 204
    booking.transition(world["appt"], AppointmentStatus.ARRIVED, by=world["reception"])
    assert client.get(reverse("reception:rows"), {"sig": sig}).status_code == 200


def test_arrive_with_vitals_then_call_in_and_undo(client, world):
    appt = world["appt"]
    client.force_login(world["reception"])
    assert "وصول" in client.get(reverse("reception:arrive", args=[appt.pk])).content.decode()
    resp = client.post(reverse("reception:arrive", args=[appt.pk]), {"weight_kg": "70", "bp_systolic": "120",
                                                                     "bp_diastolic": "80"})  # fmt: skip
    assert resp.status_code == 200 and "HX-Trigger" in resp.headers
    appt.refresh_from_db()
    assert appt.status == AppointmentStatus.ARRIVED and appt.arrived_at
    assert Vitals.objects.get(visit__appointment=appt).bp_systolic == 120

    client.post(reverse("reception:step", args=[appt.pk, "call"]))
    appt.refresh_from_db()
    assert appt.status == AppointmentStatus.IN_CONSULTATION
    client.post(reverse("reception:step", args=[appt.pk, "undo"]))
    appt.refresh_from_db()
    assert (appt.status, appt.called_at) == (AppointmentStatus.ARRIVED, None)


def test_invalid_vitals_keep_the_modal_open(client, world):
    client.force_login(world["reception"])
    resp = client.post(reverse("reception:arrive", args=[world["appt"].pk]), {"bp_systolic": "120"})
    assert resp.headers["HX-Retarget"] == "#modal"
    world["appt"].refresh_from_db()
    assert world["appt"].status == AppointmentStatus.BOOKED


def test_record_payment_from_the_screen(client, world):
    appt = world["appt"]
    client.force_login(world["reception"])
    resp = client.get(reverse("reception:payment", args=[appt.pk]))
    assert 'value="300' in resp.content.decode()  # amount due is prefilled
    client.post(reverse("reception:payment", args=[appt.pk]), {"amount": "300", "method": "cash"})
    p = Payment.objects.get()
    assert (p.amount, p.received_by) == (Decimal("300"), world["reception"])
    assert "مدفوع" in client.get(reverse("reception:rows")).content.decode()


def test_manual_no_show(client, world):
    client.force_login(world["reception"])
    client.post(reverse("reception:step", args=[world["appt"].pk, "no_show"]))
    world["appt"].refresh_from_db()
    assert world["appt"].status == AppointmentStatus.NO_SHOW


def test_walk_in_screen(client, world):
    other = Patient.objects.create(organization=world["org"], full_name="كريم", phone="+201009999999",
                                   gender=Gender.MALE, file_number=2)  # fmt: skip
    client.force_login(world["reception"])
    resp = client.get(reverse("reception:walk_in"), {"q": "كريم"}, HTTP_HX_REQUEST="true")
    assert "كريم" in resp.content.decode()
    resp = client.post(reverse("reception:walk_in"), {"patient": other.pk, "visit_type": world["vt"].pk})
    assert resp.url == reverse("reception:today")
    assert other.appointments.get().status == AppointmentStatus.ARRIVED


def test_reception_actions_are_tenant_scoped(client, world):
    appt = world["appt"]
    client.force_login(world["other"])
    assert client.get(reverse("reception:arrive", args=[appt.pk])).status_code == 404
    assert client.post(reverse("reception:step", args=[appt.pk, "call"])).status_code == 404
    assert client.get(reverse("reception:payment", args=[appt.pk])).status_code == 404
    assert client.get(reverse("reception:vitals", args=[appt.pk])).status_code == 404
    resp = client.post(reverse("reception:walk_in"), {"patient": world["patient"].pk, "visit_type": world["vt"].pk})
    assert resp.status_code == 404
    assert "منى سعيد" not in client.get(reverse("reception:today")).content.decode()
