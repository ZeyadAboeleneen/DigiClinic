import threading
from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from django.db import connection
from freezegun import freeze_time

from apps.core.timeutils import CAIRO, local_dt
from apps.doctors.models import BookingMode, Doctor, VisitType, WorkingPeriod
from apps.patients.models import Gender, Patient
from apps.scheduling import services
from apps.scheduling.models import AppointmentStatus

SATURDAY = date(2026, 1, 3)  # weekday_egypt == 0


@pytest.fixture
def doctor_slots(org_a):
    d = Doctor.objects.create(organization=org_a, name_ar="سارة", booking_mode=BookingMode.SLOTS, slot_minutes=10)
    WorkingPeriod.objects.create(organization=org_a, doctor=d, weekday=0, start_time="14:00", end_time="21:00")
    return d


@pytest.fixture
def visit_type(org_a, doctor_slots):
    return VisitType.objects.create(
        organization=org_a, doctor=doctor_slots, name_ar="كشف", duration_minutes=20, price=Decimal("400")
    )


@pytest.fixture
def followup_type(org_a, doctor_slots):
    return VisitType.objects.create(
        organization=org_a,
        doctor=doctor_slots,
        name_ar="إعادة",
        duration_minutes=10,
        price=Decimal("200"),
        is_followup=True,
        free_followup_days=14,
    )


@pytest.fixture
def patient(org_a):
    return Patient.objects.create(
        organization=org_a, full_name="محمد أحمد", phone="+201001234567", gender=Gender.MALE, file_number=1
    )


@pytest.fixture
def staffer(org_a, make_member):
    return make_member(org_a, "reception")


# --- free_slots (12 §2) ----------------------------------------------------------------------


def test_first_and_last_slot_for_20_minute_visit(doctor_slots, visit_type):
    with freeze_time("2026-01-01 06:00:00"):  # well before the booking day, no min-notice effect
        slots = services.free_slots(doctor_slots, visit_type, SATURDAY)
    first, last = slots[0], slots[-1]
    assert first.strftime("%H:%M") == "14:00"
    assert last.strftime("%H:%M") == "20:40"


def test_booking_a_20_minute_slot_hides_the_next_slot_too(doctor_slots, visit_type, patient, staffer):
    with freeze_time("2026-01-01 06:00:00"):
        start = local_dt(SATURDAY, time(15, 0))
        services.book(patient=patient, doctor=doctor_slots, visit_type=visit_type, by=staffer, start_at=start)
        slots = services.free_slots(doctor_slots, visit_type, SATURDAY)
    times = {s.strftime("%H:%M") for s in slots}
    assert "15:00" not in times and "15:10" not in times
    assert "14:40" in times and "15:20" in times


def test_same_day_booking_respects_current_time(doctor_slots, visit_type):
    with freeze_time("2026-01-03 16:05:00+02:00"):  # 4:05pm Cairo (Jan is standard time, UTC+2)
        slots = services.free_slots(doctor_slots, visit_type, SATURDAY)
    assert slots[0].strftime("%H:%M") == "16:10"


def test_cancelled_appointments_free_the_slot(doctor_slots, visit_type, patient, staffer):
    with freeze_time("2026-01-01 06:00:00"):
        start = local_dt(SATURDAY, time(15, 0))
        appt = services.book(patient=patient, doctor=doctor_slots, visit_type=visit_type, by=staffer, start_at=start)
        services.cancel(appt, by=staffer)
        slots = services.free_slots(doctor_slots, visit_type, SATURDAY)
    assert "15:00" in {s.strftime("%H:%M") for s in slots}


# --- queue mode (12 §3) ----------------------------------------------------------------------


@pytest.fixture
def doctor_queue(org_a):
    d = Doctor.objects.create(organization=org_a, name_ar="سارة", booking_mode=BookingMode.QUEUE, queue_avg_minutes=15)
    WorkingPeriod.objects.create(
        organization=org_a, doctor=d, weekday=0, start_time="14:00", end_time="21:00", max_patients=20
    )
    return d


@pytest.fixture
def queue_visit_type(org_a, doctor_queue):
    return VisitType.objects.create(
        organization=org_a, doctor=doctor_queue, name_ar="كشف", duration_minutes=10, price=Decimal("300")
    )


def _book_queue(doctor, visit_type, by, n=1):
    patients = [
        Patient.objects.create(
            organization=doctor.organization,
            full_name=f"مريض {i}",
            phone=f"+2010{i:08d}",
            gender=Gender.MALE,
            file_number=i,
        )
        for i in range(n)
    ]
    appts = []
    for p in patients:
        appts.append(services.book(patient=p, doctor=doctor, visit_type=visit_type, by=by, day=SATURDAY))
    return appts


def test_queue_day_full_at_capacity(doctor_queue, queue_visit_type, staffer):
    with freeze_time("2026-01-01 06:00:00"):
        _book_queue(doctor_queue, queue_visit_type, staffer, n=20)
        extra = Patient.objects.create(
            organization=doctor_queue.organization,
            full_name="زيادة",
            phone="+201099999999",
            gender=Gender.MALE,
            file_number=99,
        )
        with pytest.raises(services.QueueFull):
            services.book(patient=extra, doctor=doctor_queue, visit_type=queue_visit_type, by=staffer, day=SATURDAY)


def test_cancelling_a_queue_number_frees_capacity_but_not_the_number(doctor_queue, queue_visit_type, staffer):
    with freeze_time("2026-01-01 06:00:00"):
        appts = _book_queue(doctor_queue, queue_visit_type, staffer, n=20)
        services.cancel(appts[4], by=staffer)  # number 5
        avail = services.queue_availability(doctor_queue, SATURDAY)[0]
        assert avail.remaining == 1
        assert avail.next_number == 21


def test_queue_number_7_estimated_time(doctor_queue, queue_visit_type, staffer):
    with freeze_time("2026-01-01 06:00:00"):
        appts = _book_queue(doctor_queue, queue_visit_type, staffer, n=7)
    assert appts[-1].queue_number == 7
    assert appts[-1].start_at.astimezone(CAIRO).strftime("%H:%M") == "15:30"


# --- concurrency (12 §4) ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_concurrent_slot_booking_only_one_succeeds(org_a, make_member):
    doctor = Doctor.objects.create(organization=org_a, name_ar="سارة", booking_mode=BookingMode.SLOTS, slot_minutes=10)
    WorkingPeriod.objects.create(organization=org_a, doctor=doctor, weekday=0, start_time="14:00", end_time="21:00")
    vt = VisitType.objects.create(
        organization=org_a, doctor=doctor, name_ar="كشف", duration_minutes=20, price=Decimal("400")
    )
    staffer = make_member(org_a, "reception")
    patients = [
        Patient.objects.create(
            organization=org_a, full_name=f"م{i}", phone=f"+2010{i:08d}", gender=Gender.MALE, file_number=i
        )
        for i in range(2)
    ]
    start = local_dt(SATURDAY, time(15, 0))
    results = []

    def worker(p):
        try:
            services.book(patient=p, doctor=doctor, visit_type=vt, by=staffer, start_at=start)
            results.append("ok")
        except services.SlotTaken:
            results.append("taken")
        finally:
            connection.close()

    with freeze_time("2026-01-01 06:00:00"):
        threads = [threading.Thread(target=worker, args=(p,)) for p in patients]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert sorted(results) == ["ok", "taken"]


@pytest.mark.django_db(transaction=True)
def test_concurrent_last_queue_slot_only_one_succeeds(org_a, make_member):
    doctor = Doctor.objects.create(
        organization=org_a, name_ar="سارة", booking_mode=BookingMode.QUEUE, queue_avg_minutes=15
    )
    WorkingPeriod.objects.create(
        organization=org_a, doctor=doctor, weekday=0, start_time="14:00", end_time="21:00", max_patients=1
    )
    vt = VisitType.objects.create(
        organization=org_a, doctor=doctor, name_ar="كشف", duration_minutes=10, price=Decimal("300")
    )
    staffer = make_member(org_a, "reception")
    patients = [
        Patient.objects.create(
            organization=org_a, full_name=f"م{i}", phone=f"+2010{i:08d}", gender=Gender.MALE, file_number=i
        )
        for i in range(2)
    ]
    results = []

    def worker(p):
        try:
            services.book(patient=p, doctor=doctor, visit_type=vt, by=staffer, day=SATURDAY)
            results.append("ok")
        except services.QueueFull:
            results.append("full")
        finally:
            connection.close()

    with freeze_time("2026-01-01 06:00:00"):
        threads = [threading.Thread(target=worker, args=(p,)) for p in patients]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert sorted(results) == ["full", "ok"]


# --- pricing (free follow-up) ------------------------------------------------------------------


def test_followup_within_window_is_free_then_full_price_after(
    doctor_slots, visit_type, followup_type, patient, staffer
):
    with freeze_time("2026-01-01 06:00:00"):
        start = local_dt(SATURDAY, time(14, 0))
        original = services.book(
            patient=patient, doctor=doctor_slots, visit_type=visit_type, by=staffer, start_at=start
        )
        services.transition(original, AppointmentStatus.ARRIVED, by=staffer)
        services.transition(original, AppointmentStatus.IN_CONSULTATION, by=staffer)
        services.transition(original, AppointmentStatus.COMPLETED, by=staffer)

    with freeze_time("2026-01-10 06:00:00"):  # within 14 days
        next_sat = SATURDAY + timedelta(days=7)
        start2 = local_dt(next_sat, time(14, 0))
        followup = services.book(
            patient=patient, doctor=doctor_slots, visit_type=followup_type, by=staffer, start_at=start2
        )
    assert followup.price == 0 and followup.followup_of_id == original.pk

    with freeze_time("2026-01-25 06:00:00"):  # 22 days later, past the 14-day window
        later_sat = SATURDAY + timedelta(days=21)
        start3 = local_dt(later_sat, time(14, 0))
        late_followup = services.book(
            patient=patient, doctor=doctor_slots, visit_type=followup_type, by=staffer, start_at=start3
        )
    assert late_followup.price == followup_type.price


# --- reschedule / cancel / transition (12 §5, §6) -----------------------------------------------


def test_reschedule_marks_old_as_rescheduled_and_links_new(doctor_slots, visit_type, patient, staffer):
    with freeze_time("2026-01-01 06:00:00"):
        start = local_dt(SATURDAY, time(14, 0))
        appt = services.book(patient=patient, doctor=doctor_slots, visit_type=visit_type, by=staffer, start_at=start)
        new_start = local_dt(SATURDAY, time(16, 0))
        new_appt = services.reschedule(appt, by=staffer, new_start_at=new_start)
    appt.refresh_from_db()
    assert appt.status == AppointmentStatus.RESCHEDULED and appt.rescheduled_to_id == new_appt.pk
    assert appt.version == 2
    assert new_appt.status == AppointmentStatus.BOOKED


def test_cancel_sets_status_and_reason(doctor_slots, visit_type, patient, staffer):
    with freeze_time("2026-01-01 06:00:00"):
        start = local_dt(SATURDAY, time(14, 0))
        appt = services.book(patient=patient, doctor=doctor_slots, visit_type=visit_type, by=staffer, start_at=start)
        services.cancel(appt, by=staffer, reason="غيّر رأيه")
    appt.refresh_from_db()
    assert appt.status == AppointmentStatus.CANCELLED and appt.cancel_reason == "غيّر رأيه" and appt.cancelled_at


def test_invalid_transition_raises(doctor_slots, visit_type, patient, staffer):
    with freeze_time("2026-01-01 06:00:00"):
        start = local_dt(SATURDAY, time(14, 0))
        appt = services.book(patient=patient, doctor=doctor_slots, visit_type=visit_type, by=staffer, start_at=start)
        with pytest.raises(services.InvalidTransition):
            services.transition(appt, AppointmentStatus.COMPLETED, by=staffer)


def test_every_transition_creates_an_event(doctor_slots, visit_type, patient, staffer):
    from apps.scheduling.models import AppointmentEvent

    with freeze_time("2026-01-01 06:00:00"):
        start = local_dt(SATURDAY, time(14, 0))
        appt = services.book(patient=patient, doctor=doctor_slots, visit_type=visit_type, by=staffer, start_at=start)
        services.transition(appt, AppointmentStatus.CONFIRMED, by=staffer)
    assert AppointmentEvent.objects.filter(appointment=appt).count() == 2  # created + confirmed
