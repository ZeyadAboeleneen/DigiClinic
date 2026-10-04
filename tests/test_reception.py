from datetime import date, time
from decimal import Decimal

import pytest
from django.core.management import call_command
from freezegun import freeze_time

from apps.billing import services as billing
from apps.billing.models import Payment
from apps.clinical.models import Vitals
from apps.core.timeutils import local_dt
from apps.doctors.models import BookingMode, Doctor, ScheduleException, VisitType, WorkingPeriod
from apps.notifications.management.commands.seed_notifications import seed_org_notifications
from apps.notifications.models import Event, MessageStatus, NotificationSettings, ScheduledMessage
from apps.patients.models import Gender, Patient
from apps.scheduling import services as booking
from apps.scheduling.models import Appointment, AppointmentStatus, BookingSource

SATURDAY = date(2026, 1, 3)  # Cairo standard time (UTC+2)


@pytest.fixture
def clinic(org_a, make_member):
    seed_org_notifications(org_a)
    doctor = Doctor.objects.create(organization=org_a, name_ar="سارة", title_ar="د.", slot_minutes=20)
    for weekday in range(7):
        WorkingPeriod.objects.create(organization=org_a, doctor=doctor, weekday=weekday,
                                     start_time="14:00", end_time="21:00")  # fmt: skip
    vt = VisitType.objects.create(organization=org_a, doctor=doctor, name_ar="كشف", duration_minutes=20,
                                  price=Decimal("300"))  # fmt: skip
    return {"org": org_a, "doctor": doctor, "vt": vt, "staff": make_member(org_a, "reception")}


def _patient(org, n=1, consent=True):
    return Patient.objects.create(organization=org, full_name=f"مريض {n}", phone=f"+2010012345{n:02d}",
                                  gender=Gender.MALE, file_number=n, messaging_consent=consent)  # fmt: skip


def _book(clinic, patient, start=None, day=None, **kw):
    return booking.book(patient=patient, doctor=clinic["doctor"], visit_type=clinic["vt"], by=clinic["staff"],
                        start_at=start, day=day, **kw)  # fmt: skip


def _messages(appt, event):
    return list(ScheduledMessage.objects.filter(appointment=appt, event=event))


# --- automatic no-show (12 §7) --------------------------------------------------------------


def test_no_show_after_grace_period(clinic):
    with freeze_time("2026-01-01 10:00:00+02:00"):
        appt = _book(clinic, _patient(clinic["org"]), local_dt(SATURDAY, time(15, 0)))
    with freeze_time("2026-01-03 15:29:00+02:00"):
        assert booking.mark_no_shows() == 0
    with freeze_time("2026-01-03 15:31:00+02:00"):
        assert booking.mark_no_shows() == 1
    appt.refresh_from_db()
    assert appt.status == AppointmentStatus.NO_SHOW
    assert Patient.objects.get(pk=appt.patient_id).no_show_count == 1


def test_auto_rebook_same_time_seven_days_later_with_message(clinic, django_capture_on_commit_callbacks):
    with freeze_time("2026-01-01 10:00:00+02:00"):
        appt = _book(clinic, _patient(clinic["org"]), local_dt(SATURDAY, time(15, 0)))
    with freeze_time("2026-01-03 15:31:00+02:00"), django_capture_on_commit_callbacks(execute=True):
        booking.mark_no_shows()
    new = Appointment.objects.get(auto_rebooked_from=appt)
    assert new.start_at == local_dt(date(2026, 1, 10), time(15, 0))
    assert (new.source, new.price) == (BookingSource.AUTO_REBOOK, appt.price)
    [msg] = _messages(new, Event.NO_SHOW_REBOOKED)
    assert msg.status == MessageStatus.PENDING
    assert not _messages(new, Event.BOOKING_CONFIRMED)
    assert _messages(new, Event.REMINDER)  # the new appointment gets its reminders too


def test_auto_rebook_skips_a_closed_day(clinic):
    ScheduleException.objects.create(organization=clinic["org"], doctor=clinic["doctor"], date=date(2026, 1, 10),
                                     kind="closed")  # fmt: skip
    with freeze_time("2026-01-01 10:00:00+02:00"):
        appt = _book(clinic, _patient(clinic["org"]), local_dt(SATURDAY, time(15, 0)))
    with freeze_time("2026-01-03 15:31:00+02:00"):
        new = booking.mark_no_show(appt)
    assert new.start_at == local_dt(date(2026, 1, 11), time(15, 0))


def test_auto_rebook_picks_closest_free_time(clinic):
    rebook_day = date(2026, 1, 10)
    with freeze_time("2026-01-01 10:00:00+02:00"):
        appt = _book(clinic, _patient(clinic["org"], 1), local_dt(SATURDAY, time(15, 0)))
        _book(clinic, _patient(clinic["org"], 2), local_dt(rebook_day, time(15, 0)))
    with freeze_time("2026-01-03 15:31:00+02:00"):
        new = booking.mark_no_show(appt)
    assert new.start_at in (local_dt(rebook_day, time(14, 40)), local_dt(rebook_day, time(15, 20)))


def test_rebooked_appointment_missing_again_only_gets_a_message(clinic, django_capture_on_commit_callbacks):
    with freeze_time("2026-01-01 10:00:00+02:00"):
        appt = _book(clinic, _patient(clinic["org"]), local_dt(SATURDAY, time(15, 0)))
    with freeze_time("2026-01-03 15:31:00+02:00"):
        second = booking.mark_no_show(appt)
    with freeze_time("2026-01-10 15:31:00+02:00"), django_capture_on_commit_callbacks(execute=True):
        assert booking.mark_no_show(second) is None
    assert not Appointment.objects.filter(auto_rebooked_from=second).exists()
    assert len(_messages(second, Event.NO_SHOW_MISSED)) == 1


@pytest.mark.parametrize(("policy", "expected"), [("notify", 1), ("none", 0)])
def test_notify_and_none_policies(clinic, django_capture_on_commit_callbacks, policy, expected):
    NotificationSettings.objects.filter(organization=clinic["org"]).update(no_show_policy=policy)
    with freeze_time("2026-01-01 10:00:00+02:00"):
        appt = _book(clinic, _patient(clinic["org"]), local_dt(SATURDAY, time(15, 0)))
    with freeze_time("2026-01-03 15:31:00+02:00"), django_capture_on_commit_callbacks(execute=True):
        assert booking.mark_no_show(appt) is None
    assert len(_messages(appt, Event.NO_SHOW_MISSED)) == expected


def test_undo_no_show_withdraws_the_rebooking(clinic, django_capture_on_commit_callbacks):
    with freeze_time("2026-01-01 10:00:00+02:00"):
        appt = _book(clinic, _patient(clinic["org"]), local_dt(SATURDAY, time(15, 0)))
    with freeze_time("2026-01-03 15:31:00+02:00"), django_capture_on_commit_callbacks(execute=True):
        new = booking.mark_no_show(appt)
    with freeze_time("2026-01-03 15:35:00+02:00"), django_capture_on_commit_callbacks(execute=True):
        booking.undo_no_show(appt, by=clinic["staff"])
    appt.refresh_from_db()
    new.refresh_from_db()
    assert (appt.status, new.status) == (AppointmentStatus.BOOKED, AppointmentStatus.CANCELLED)
    assert Patient.objects.get(pk=appt.patient_id).no_show_count == 0
    assert {m.status for m in ScheduledMessage.objects.filter(appointment=new)} == {MessageStatus.CANCELLED}
    assert not _messages(new, Event.CANCELLED)  # withdrawn silently, no "cancelled" message


def test_queue_no_show_waits_for_period_end_or_manual(clinic):
    doctor = clinic["doctor"]
    doctor.booking_mode = BookingMode.QUEUE
    doctor.save()
    with freeze_time("2026-01-01 10:00:00+02:00"):
        appt = _book(clinic, _patient(clinic["org"]), day=SATURDAY)
    with freeze_time("2026-01-03 20:59:00+02:00"):
        assert booking.mark_no_shows() == 0  # estimated time long past, but the period hasn't ended
    NotificationSettings.objects.filter(organization=clinic["org"]).update(no_show_queue_mark_at="manual")
    with freeze_time("2026-01-03 21:01:00+02:00"):
        assert booking.mark_no_shows() == 0
    NotificationSettings.objects.filter(organization=clinic["org"]).update(no_show_queue_mark_at="period_end")
    with freeze_time("2026-01-03 21:01:00+02:00"):
        assert booking.mark_no_shows() == 1
    appt.refresh_from_db()
    assert appt.status == AppointmentStatus.NO_SHOW


# --- "دورك قرّب" (06 §6.4) -------------------------------------------------------------------


def test_near_turn_when_number_four_goes_in(clinic, django_capture_on_commit_callbacks):
    doctor = clinic["doctor"]
    doctor.booking_mode, doctor.near_turn_threshold = BookingMode.QUEUE, 3
    doctor.save()
    with freeze_time("2026-01-01 10:00:00+02:00"):
        appts = [_book(clinic, _patient(clinic["org"], n), day=SATURDAY) for n in range(1, 10)]
    staff = clinic["staff"]
    with freeze_time("2026-01-03 15:00:00+02:00"):
        for a in appts[:3]:
            for st in (AppointmentStatus.ARRIVED, AppointmentStatus.IN_CONSULTATION, AppointmentStatus.COMPLETED):
                booking.transition(a, st, by=staff)
        booking.transition(appts[4], AppointmentStatus.ARRIVED, by=staff)  # #5 already in the clinic
        booking.transition(appts[3], AppointmentStatus.ARRIVED, by=staff)
        with django_capture_on_commit_callbacks(execute=True):
            booking.transition(appts[3], AppointmentStatus.IN_CONSULTATION, by=staff)  # #4 goes in
    got = {a.queue_number for a in appts if _messages(a, Event.NEAR_TURN)}
    assert got == {6, 7}  # #5 is already here; #8 still has 4 ahead (#4 with the doctor, #5, #6, #7)
    [msg] = _messages(appts[6], Event.NEAR_TURN)
    assert msg.context["patients_ahead"] == 3

    with freeze_time("2026-01-03 15:20:00+02:00"), django_capture_on_commit_callbacks(execute=True):
        booking.transition(appts[3], AppointmentStatus.COMPLETED, by=staff)  # #4 done → #8 now has 3 ahead
    got = {a.queue_number: len(_messages(a, Event.NEAR_TURN)) for a in appts if _messages(a, Event.NEAR_TURN)}
    assert got == {6: 1, 7: 1, 8: 1}  # never twice for the same appointment


# --- walk-in (12 §8) -------------------------------------------------------------------------


def test_walk_in_slots_takes_next_free_slot_and_arrives_without_confirmation(
    clinic, django_capture_on_commit_callbacks
):
    with freeze_time("2026-01-03 15:05:00+02:00"), django_capture_on_commit_callbacks(execute=True):
        appt = booking.walk_in(patient=_patient(clinic["org"]), doctor=clinic["doctor"], visit_type=clinic["vt"],
                               by=clinic["staff"])  # fmt: skip
    assert appt.start_at == local_dt(SATURDAY, time(15, 20))
    assert (appt.status, appt.source) == (AppointmentStatus.ARRIVED, BookingSource.WALK_IN)
    assert not _messages(appt, Event.BOOKING_CONFIRMED)


def test_walk_in_queue_gets_next_number(clinic):
    doctor = clinic["doctor"]
    doctor.booking_mode = BookingMode.QUEUE
    doctor.save()
    with freeze_time("2026-01-01 10:00:00+02:00"):
        _book(clinic, _patient(clinic["org"], 1), day=SATURDAY)
    with freeze_time("2026-01-03 15:05:00+02:00"):
        appt = booking.walk_in(patient=_patient(clinic["org"], 2), doctor=doctor, visit_type=clinic["vt"],
                               by=clinic["staff"])  # fmt: skip
    assert (appt.queue_number, appt.status) == (2, AppointmentStatus.ARRIVED)


# --- payments & cashbox ----------------------------------------------------------------------


def test_balance_partial_paid_refund_and_cashbox(clinic):
    staff = clinic["staff"]
    with freeze_time("2026-01-03 15:00:00+02:00"):
        a1 = _book(clinic, _patient(clinic["org"], 1), local_dt(SATURDAY, time(16, 0)))
        a2 = _book(clinic, _patient(clinic["org"], 2), local_dt(SATURDAY, time(16, 20)))
        assert billing.balance(a1).status == "unpaid"
        billing.record_payment(a1, amount=100, by=staff)
        assert billing.balance(a1).status == "partial"
        billing.record_payment(a1, amount=200, method="instapay", by=staff)
        assert billing.balance(a1).status == "paid"
        billing.record_payment(a2, amount=300, by=staff)
        billing.record_payment(a2, amount=50, by=staff, is_refund=True)
        with pytest.raises(billing.PaymentError):
            billing.record_payment(a2, amount=1000, by=staff, is_refund=True)
        with pytest.raises(billing.PaymentError):
            billing.record_payment(a2, amount=0, by=staff)
    with freeze_time("2026-01-03 23:59:00+02:00"):
        box = billing.cashbox(clinic["org"], SATURDAY)
    assert box.total == Decimal("550")
    assert dict(box.by_method) == {"كاش": Decimal("350"), "InstaPay": Decimal("200")}
    assert box.total == sum(p.signed_amount for p in Payment.objects.all())
    assert billing.cashbox(clinic["org"], date(2026, 1, 4)).total == 0


def test_bmi_is_computed(clinic):
    from apps.clinical.models import Visit

    with freeze_time("2026-01-03 15:00:00+02:00"):
        appt = _book(clinic, _patient(clinic["org"]), local_dt(SATURDAY, time(16, 0)))
    v = Vitals.objects.create(organization=clinic["org"], visit=Visit.for_appointment(appt), weight_kg=80,
                              height_cm=180)  # fmt: skip
    assert v.bmi == Decimal("24.7")


# --- simulation command ----------------------------------------------------------------------


def test_simulate_day_runs_and_sends_nothing(clinic):
    from apps.organizations.models import Organization

    Organization.objects.filter(pk=clinic["org"].pk).update(slug="demo-clinic")
    with freeze_time("2026-01-03 17:00:00+02:00"):
        call_command("simulate_day")
        statuses = sorted(a.status for a in Appointment.objects.filter(notes="simulate_day"))
        assert statuses.count(AppointmentStatus.COMPLETED) == 3 and statuses.count(AppointmentStatus.NO_SHOW) == 1
        assert not ScheduledMessage.objects.filter(status=MessageStatus.PENDING).exists()
        call_command("simulate_day", "--remove")
    assert not Appointment.objects.filter(notes="simulate_day").exists()
