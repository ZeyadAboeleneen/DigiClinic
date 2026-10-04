from datetime import date, time
from decimal import Decimal

import pytest
from django.urls import reverse
from freezegun import freeze_time

from apps.core.timeutils import local_dt
from apps.doctors.models import BookingMode, Doctor, ScheduleExceptionKind, VisitType, WorkingPeriod
from apps.patients import services as patient_services
from apps.patients.models import Gender, Patient
from apps.scheduling import services
from apps.scheduling.models import Appointment

SATURDAY = date(2026, 1, 3)


@pytest.fixture
def world(org_a, make_member):
    owner = make_member(org_a, "owner")
    reception = make_member(org_a, "reception")
    doctor = Doctor.objects.create(organization=org_a, name_ar="سارة", booking_mode=BookingMode.SLOTS, slot_minutes=10)
    WorkingPeriod.objects.create(organization=org_a, doctor=doctor, weekday=0, start_time="14:00", end_time="21:00")
    vt = VisitType.objects.create(
        organization=org_a, doctor=doctor, name_ar="كشف", duration_minutes=20, price=Decimal("400")
    )
    patient = patient_services.create_patient(
        org_a, Patient(organization=org_a, full_name="محمد أحمد", phone="+201001234567", gender=Gender.MALE)
    )
    return {"org": org_a, "owner": owner, "reception": reception, "doctor": doctor, "vt": vt, "patient": patient}


def test_booking_search_finds_existing_patient(client, world):
    client.force_login(world["reception"])
    r = client.get(reverse("scheduling:booking_search"), {"q": "0100 123"})
    assert "محمد أحمد" in r.content.decode()


def test_book_existing_patient_end_to_end(client, world):
    client.force_login(world["reception"])
    with freeze_time("2026-01-01 06:00:00"):
        page = client.get(
            reverse("scheduling:booking_patient", args=[world["patient"].pk]), {"day": SATURDAY.isoformat()}
        )
        assert "كشف" in page.content.decode()
        slots = client.get(
            reverse("scheduling:booking_slots", args=[world["patient"].pk]),
            {"visit_type": world["vt"].pk, "day": SATURDAY.isoformat()},
        )
        assert "14:00" in slots.content.decode()
        start = local_dt(SATURDAY, time(14, 0))
        r = client.post(
            reverse("scheduling:booking_confirm", args=[world["patient"].pk]),
            {"visit_type": world["vt"].pk, "start_at": start.isoformat()},
        )
    assert r.status_code == 302 and r.url == reverse("scheduling:booking_home")
    assert Appointment.objects.filter(patient=world["patient"]).exists()


def test_new_patient_quick_add_then_book(client, world):
    client.force_login(world["reception"])
    data = {
        "full_name": "منى حسن", "gender": "female", "phone": "01099998888", "whatsapp": "",
        "whatsapp_same_as_phone": "on", "preferred_channel": "whatsapp", "messaging_consent": "on",
    }  # fmt: skip
    r = client.post(reverse("scheduling:booking_new_patient"), data)
    assert r.status_code == 302
    patient = Patient.objects.get(full_name="منى حسن")
    assert r.url == reverse("scheduling:booking_patient", args=[patient.pk])


def test_booking_mode_switch_changes_the_screen(client, world):
    client.force_login(world["reception"])
    with freeze_time("2026-01-01 06:00:00"):
        slots = client.get(
            reverse("scheduling:booking_slots", args=[world["patient"].pk]),
            {"visit_type": world["vt"].pk, "day": SATURDAY.isoformat()},
        ).content.decode()
    assert "14:00" in slots and "رقم الدور الجاي" not in slots

    world["doctor"].booking_mode = BookingMode.QUEUE
    world["doctor"].queue_avg_minutes = 15
    world["doctor"].save()
    WorkingPeriod.objects.filter(doctor=world["doctor"]).update(max_patients=20)

    with freeze_time("2026-01-01 06:00:00"):
        queue_view = client.get(
            reverse("scheduling:booking_slots", args=[world["patient"].pk]),
            {"visit_type": world["vt"].pk, "day": SATURDAY.isoformat()},
        ).content.decode()
    assert "رقم الدور الجاي" in queue_view


def test_closing_a_day_with_bookings_shows_affected_with_suggestions(client, world):
    client.force_login(world["owner"])
    with freeze_time("2026-01-01 06:00:00"):
        for hour in (14, 15, 16, 17, 18):
            services.book(
                patient=world["patient"], doctor=world["doctor"], visit_type=world["vt"], by=world["owner"],
                start_at=local_dt(SATURDAY, time(hour, 0)),
            )  # fmt: skip
        assert Appointment.objects.filter(doctor=world["doctor"], date=SATURDAY).count() == 5

        r = client.post(
            reverse("doctors:exception_add"), {"date": SATURDAY.isoformat(), "kind": ScheduleExceptionKind.CLOSED}
        )
        assert r.status_code == 200 and r.headers.get("HX-Redirect")
        assert r.headers["HX-Redirect"].endswith(f"?date={SATURDAY}")

        page = client.get(r.headers["HX-Redirect"]).content.decode()
    assert page.count("أجّل للمقترح") == 5


def test_reception_cannot_manage_schedule_but_can_book(client, world):
    client.force_login(world["reception"])
    assert client.get(reverse("doctors:schedule")).status_code == 403
    assert client.get(reverse("scheduling:booking_home")).status_code == 200


def test_viewer_cannot_book(client, world, make_member):
    viewer = make_member(world["org"], "viewer")
    client.force_login(viewer)
    assert client.get(reverse("scheduling:booking_home")).status_code == 403
    assert client.get(reverse("scheduling:appointments_day")).status_code == 200  # view-only is fine


def test_cross_tenant_appointment_isolation(client, world, org_b, make_member):
    with freeze_time("2026-01-01 06:00:00"):
        appt = services.book(
            patient=world["patient"], doctor=world["doctor"], visit_type=world["vt"], by=world["owner"],
            start_at=local_dt(SATURDAY, time(14, 0)),
        )  # fmt: skip
    client.force_login(make_member(org_b, "owner"))
    assert client.post(reverse("scheduling:appointment_cancel", args=[appt.pk])).status_code == 404
