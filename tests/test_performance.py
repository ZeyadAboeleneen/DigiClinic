"""Phase 9 N+1 review: query counts of the busy screens must not grow with the number of rows."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.billing.services import record_payment
from apps.clinical import services as clinical
from apps.clinical.models import Vitals
from apps.doctors.models import Doctor, VisitType, WorkingPeriod
from apps.patients.models import Allergy, Gender, Patient
from apps.scheduling import services as booking
from apps.scheduling.models import AppointmentStatus

pytestmark = pytest.mark.usefixtures("midday")


@pytest.fixture
def clinic(org_a, make_member):
    doctor = Doctor.objects.create(organization=org_a, name_ar="سارة", slot_minutes=10)
    for weekday in range(7):
        WorkingPeriod.objects.create(organization=org_a, doctor=doctor, weekday=weekday,
                                     start_time="00:00", end_time="23:59")  # fmt: skip
    vt = VisitType.objects.create(organization=org_a, doctor=doctor, name_ar="كشف", duration_minutes=10,
                                  price=Decimal("300"))  # fmt: skip
    return {"org": org_a, "doctor": doctor, "vt": vt, "reception": make_member(org_a, "reception"),
            "doc": make_member(org_a, "doctor"), "n": 0}  # fmt: skip


def _add_patients(clinic, count):
    """Each patient: booked today, arrived (with vitals), paid; half of them also completed visits before."""
    base = timezone.now().replace(second=0, microsecond=0)
    patients = []
    for _ in range(count):
        clinic["n"] += 1
        n = clinic["n"]
        p = Patient.objects.create(organization=clinic["org"], full_name=f"مريض {n}", phone=f"+2010012{n:05d}",
                                   gender=Gender.MALE, file_number=n)  # fmt: skip
        Allergy.objects.create(organization=clinic["org"], patient=p, name="Penicillin")
        a = booking.book(patient=p, doctor=clinic["doctor"], visit_type=clinic["vt"], by=clinic["reception"],
                         start_at=base + timedelta(minutes=10 * n), overbook=True)  # fmt: skip
        booking.transition(a, AppointmentStatus.ARRIVED, by=clinic["reception"])
        record_payment(a, amount=150, by=clinic["reception"])
        patients.append((p, a))
    return patients


def _queries(client, url):
    """Warm measurement: the first hit may do one-off writes (hourly audit event, get-or-create settings rows)."""
    client.get(url)
    with CaptureQueriesContext(connection) as ctx:
        resp = client.get(url)
    assert resp.status_code == 200, url
    return len(ctx.captured_queries)


def test_reception_screen_is_constant_in_queries(client, clinic):
    client.force_login(clinic["reception"])
    _add_patients(clinic, 3)
    small = (_queries(client, reverse("reception:today")), _queries(client, reverse("reception:rows")))
    _add_patients(clinic, 12)
    big = (_queries(client, reverse("reception:today")), _queries(client, reverse("reception:rows")))
    assert big == small, (small, big)


def test_doctor_screens_are_constant_in_queries(client, clinic):
    client.force_login(clinic["doc"])
    first = _add_patients(clinic, 3)
    patient, appt = first[0]

    def history_rich(count):
        """Give the first patient `count` finished visits with vitals and diagnoses."""
        for _ in range(count):
            _p2, a2 = _add_patients(clinic, 1)[0]
            a2.patient = patient
            a2.save(update_fields=["patient"])
            v = clinical.start_visit(a2, by=clinic["doc"])
            Vitals.objects.create(organization=clinic["org"], visit=v, weight_kg=80, bp_systolic=120, bp_diastolic=80)
            clinical.save_field(v, "diagnosis", "برد")
            clinical.finish_visit(v, by=clinic["doc"])

    visit = clinical.start_visit(appt, by=clinic["doc"])
    urls = [
        reverse("clinical:visit", args=[visit.pk]),
        reverse("clinical:history", args=[patient.pk]),
        reverse("clinical:record", args=[patient.pk]),
        reverse("prescriptions:patient_list", args=[patient.pk]),
    ]
    history_rich(2)
    small = [_queries(client, u) for u in urls]
    history_rich(8)
    _add_patients(clinic, 8)  # a longer queue too
    big = [_queries(client, u) for u in urls]
    assert big == small, list(zip(urls, small, big, strict=True))
