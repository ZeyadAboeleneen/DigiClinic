from datetime import date, time
from decimal import Decimal

import pytest
from django.urls import reverse
from freezegun import freeze_time

from apps.core.timeutils import local_dt
from apps.doctors.models import BookingMode, Doctor, ScheduleException, VisitType, WorkingPeriod
from apps.notifications.management.commands.seed_notifications import seed_org_notifications
from apps.notifications.models import Event, MessageStatus, ScheduledMessage
from apps.patients.models import Gender, Patient
from apps.scheduling import services
from apps.scheduling.models import Appointment, AppointmentStatus
from apps.scheduling.views import week_start

SATURDAY = date(2026, 1, 3)
NOW = "2026-01-02 10:00:00+02:00"  # the Friday before


@pytest.fixture
def world(org_a, org_b, make_member):
    seed_org_notifications(org_a)
    doctor = Doctor.objects.create(organization=org_a, name_ar="سارة", slot_minutes=20)
    for weekday in range(5):  # Saturday..Wednesday; Thursday + Friday off
        WorkingPeriod.objects.create(organization=org_a, doctor=doctor, weekday=weekday,
                                     start_time="14:00", end_time="21:00")  # fmt: skip
    vt = VisitType.objects.create(organization=org_a, doctor=doctor, name_ar="كشف", duration_minutes=20,
                                  price=Decimal("300"), color="#123456")  # fmt: skip
    patient = Patient.objects.create(organization=org_a, full_name="منى سعيد", phone="+201001234567",
                                     gender=Gender.FEMALE, file_number=1, messaging_consent=True)  # fmt: skip
    reception = make_member(org_a, "reception")
    with freeze_time(NOW):
        appt = services.book(patient=patient, doctor=doctor, visit_type=vt, by=reception,
                             start_at=local_dt(SATURDAY, time(16, 0)))  # fmt: skip
    return {"org": org_a, "doctor": doctor, "vt": vt, "appt": appt, "reception": reception,
            "viewer": make_member(org_a, "viewer"), "owner": make_member(org_a, "owner"),
            "other": make_member(org_b, "owner")}  # fmt: skip


def test_week_starts_on_saturday():
    assert week_start(date(2026, 1, 7)) == SATURDAY  # Wednesday → its Saturday
    assert week_start(SATURDAY) == SATURDAY
    assert week_start(date(2026, 1, 9)) == SATURDAY  # Friday is the last day


def test_week_shows_days_hours_closures_and_colored_bookings(client, world):
    ScheduleException.objects.create(organization=world["org"], doctor=world["doctor"], date=date(2026, 1, 5),
                                     kind="closed", reason="مؤتمر")  # fmt: skip
    client.force_login(world["viewer"])
    with freeze_time(NOW):
        resp = client.get(reverse("scheduling:appointments_week"), {"date": "2026-01-06"})
    html = resp.content.decode()
    cols = resp.context["columns"]
    assert [c["day"] for c in cols] == [date(2026, 1, 3 + i) for i in range(7)]
    assert cols[0]["periods"] == ["2:00 م – 9:00 م"] and cols[0]["appointments"][0].pk == world["appt"].pk
    assert cols[2]["periods"] == [] and "مؤتمر" in html  # Monday closed by exception
    assert cols[5]["periods"] == []  # Thursday off
    assert "منى سعيد" in html and "#123456" in html
    assert "draggable" not in html and "قفل اليوم" not in html  # viewer: read-only


def test_drag_to_another_day_reschedules_and_messages(client, world, django_capture_on_commit_callbacks):
    client.force_login(world["reception"])
    with freeze_time(NOW):
        html = client.get(reverse("scheduling:appointments_week"), {"date": "2026-01-03"}).content.decode()
        assert 'draggable="true"' in html
        modal = client.get(reverse("scheduling:reschedule_options", args=[world["appt"].pk]), {"day": "2026-01-04"})
        slots = modal.context["slots"]
        assert slots[0] == local_dt(date(2026, 1, 4), time(14, 0))
        with django_capture_on_commit_callbacks(execute=True):
            resp = client.post(reverse("scheduling:reschedule_confirm", args=[world["appt"].pk]),
                               {"start_at": slots[3].isoformat()})  # fmt: skip
    assert resp.url == reverse("scheduling:appointments_week") + "?date=2026-01-04"
    world["appt"].refresh_from_db()
    assert world["appt"].status == AppointmentStatus.RESCHEDULED
    new = Appointment.objects.get(pk=world["appt"].rescheduled_to_id)
    assert new.start_at == local_dt(date(2026, 1, 4), time(15, 0))
    assert ScheduledMessage.objects.filter(appointment=new, event=Event.RESCHEDULED).exists()
    assert not ScheduledMessage.objects.filter(appointment=world["appt"], status=MessageStatus.PENDING).exists()


def test_reschedule_to_a_taken_slot_is_refused(client, world):
    other = Patient.objects.create(organization=world["org"], full_name="س", phone="+201001234568",
                                   gender=Gender.MALE, file_number=2)  # fmt: skip
    with freeze_time(NOW):
        services.book(patient=other, doctor=world["doctor"], visit_type=world["vt"], by=world["reception"],
                      start_at=local_dt(date(2026, 1, 4), time(14, 0)))  # fmt: skip
        client.force_login(world["reception"])
        client.post(reverse("scheduling:reschedule_confirm", args=[world["appt"].pk]),
                    {"start_at": local_dt(date(2026, 1, 4), time(14, 0)).isoformat()})  # fmt: skip
    world["appt"].refresh_from_db()
    assert world["appt"].status == AppointmentStatus.BOOKED


def test_queue_mode_reschedule_picks_a_period(client, world):
    doctor = world["doctor"]
    doctor.booking_mode = BookingMode.QUEUE
    doctor.save()
    client.force_login(world["reception"])
    with freeze_time(NOW):
        modal = client.get(reverse("scheduling:reschedule_options", args=[world["appt"].pk]), {"day": "2026-01-04"})
        period = modal.context["periods"][0]
        client.post(reverse("scheduling:reschedule_confirm", args=[world["appt"].pk]),
                    {"day": "2026-01-04", "period_key": period.period_id})  # fmt: skip
    new = Appointment.objects.get(pk=Appointment.objects.get(pk=world["appt"].pk).rescheduled_to_id)
    assert (new.date, new.queue_number) == (date(2026, 1, 4), 1)


def test_close_day_from_calendar_goes_to_affected_screen(client, world):
    client.force_login(world["owner"])
    with freeze_time(NOW):
        resp = client.post(reverse("scheduling:close_day"), {"date": "2026-01-03"})
        assert resp.url == reverse("scheduling:affected") + "?date=2026-01-03"
        resp = client.post(reverse("scheduling:close_day"), {"date": "2026-01-04"})  # nothing booked
        assert resp.url == reverse("scheduling:appointments_week") + "?date=2026-01-04"
    assert ScheduleException.objects.filter(doctor=world["doctor"], kind="closed").count() == 2


def test_permissions_and_isolation(client, world):
    url_options = reverse("scheduling:reschedule_options", args=[world["appt"].pk])
    url_confirm = reverse("scheduling:reschedule_confirm", args=[world["appt"].pk])
    client.force_login(world["viewer"])
    assert client.get(url_options).status_code == 403
    assert client.post(url_confirm).status_code == 403
    client.force_login(world["reception"])
    assert client.post(reverse("scheduling:close_day"), {"date": "2026-01-03"}).status_code == 403
    client.force_login(world["other"])
    assert client.get(url_options).status_code == 404
    assert client.post(url_confirm, {"start_at": "2026-01-04T14:00:00+02:00"}).status_code == 404
    with freeze_time(NOW):
        assert "منى" not in client.get(reverse("scheduling:appointments_week"), {"date": "2026-01-03"}).content.decode()
