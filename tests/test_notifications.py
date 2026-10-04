import json
from datetime import date, time, timedelta
from decimal import Decimal

import httpx
import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from freezegun import freeze_time

from apps.core.timeutils import local_dt
from apps.doctors.models import BookingMode, Doctor, VisitType, WorkingPeriod
from apps.messaging.models import Delivery, SendingChannelConfig, WhatsAppNumber
from apps.notifications import dispatcher as dispatch
from apps.notifications import services, templating
from apps.notifications.management.commands.seed_notifications import seed_org_notifications
from apps.notifications.models import (
    Event,
    MessageStatus,
    NotificationSettings,
    NotificationTemplate,
    QuietPolicy,
    ScheduledMessage,
)
from apps.patients.models import Gender, Patient
from apps.scheduling import services as booking
from apps.scheduling.models import BookingSource

# 2026-01-03 is a Saturday (weekday_egypt == 0); January is standard time (UTC+2) in Cairo.
SATURDAY = date(2026, 1, 3)
SUNDAY = date(2026, 1, 4)


class FakeGateway:
    def __init__(self):
        self.sent = []
        self.registered = True
        self.fail_with = None  # (status, error code)

    def handler(self, request: httpx.Request):
        path = request.url.path
        if "/check/" in path:
            return httpx.Response(200, json={"registered": self.registered})
        if path.endswith("/send"):
            if self.fail_with:
                return httpx.Response(self.fail_with[0], json={"error": self.fail_with[1]})
            body = json.loads(request.content)
            self.sent.append(body)
            return httpx.Response(200, json={"id": f"MSG{len(self.sent)}"})
        return httpx.Response(200, json={"state": "ready", "me": "201000000000"})


@pytest.fixture
def gateway(settings):
    gw = FakeGateway()
    settings.WA_GATEWAY_TRANSPORT = httpx.MockTransport(gw.handler)
    return gw


@pytest.fixture
def clinic(org_a, make_member, gateway):
    seed_org_notifications(org_a)
    doctor = Doctor.objects.create(organization=org_a, name_ar="سارة علي", title_ar="د.", slot_minutes=10)
    for weekday in range(7):
        WorkingPeriod.objects.create(
            organization=org_a, doctor=doctor, weekday=weekday, start_time="09:00", end_time="21:00"
        )
    vt = VisitType.objects.create(
        organization=org_a, doctor=doctor, name_ar="كشف", duration_minutes=20, price=Decimal("300")
    )
    patient = Patient.objects.create(
        organization=org_a,
        full_name="محمد أحمد",
        phone="+201001234567",
        email="m@example.com",
        gender=Gender.MALE,
        file_number=1,
        messaging_consent=True,
    )
    return {"org": org_a, "doctor": doctor, "vt": vt, "patient": patient, "staff": make_member(org_a, "reception")}


def _book(clinic, capture, start, **kw):
    with capture(execute=True):
        return booking.book(
            patient=clinic["patient"], doctor=clinic["doctor"], visit_type=clinic["vt"], by=clinic["staff"],
            start_at=start, **kw,
        )  # fmt: skip


def _rows(appt, **filters):
    return list(ScheduledMessage.objects.filter(appointment=appt, **filters).order_by("send_at"))


def _run(at):
    with freeze_time(at):
        return dispatch.Dispatcher().run_once()


# --- scheduling on booking ---------------------------------------------------------------------


def test_booking_schedules_confirmation_and_two_reminders(clinic, django_capture_on_commit_callbacks):
    with freeze_time("2026-01-02 10:00:00+02:00"):
        appt = _book(clinic, django_capture_on_commit_callbacks, local_dt(SATURDAY, time(16, 0)))
    rows = _rows(appt)
    assert [(r.event, r.status) for r in rows] == [
        (Event.BOOKING_CONFIRMED, MessageStatus.PENDING),
        (Event.REMINDER, MessageStatus.PENDING),
        (Event.REMINDER, MessageStatus.PENDING),
    ]
    assert rows[1].send_at == local_dt(date(2026, 1, 2), time(16, 0))  # 24h before
    assert rows[2].send_at == local_dt(SATURDAY, time(15, 0))  # 1h before
    assert all(r.channel == "whatsapp" and r.recipient == "+201001234567" for r in rows)


def test_confirmation_is_sent_and_rendered_at_send_time(clinic, gateway, django_capture_on_commit_callbacks):
    with freeze_time("2026-01-02 10:00:00+02:00"):
        appt = _book(clinic, django_capture_on_commit_callbacks, local_dt(SATURDAY, time(16, 0)))
    NotificationTemplate.objects.filter(event=Event.BOOKING_CONFIRMED).update(body="أهلًا {first_name}، {date} {time}")
    assert _run("2026-01-02 10:00:05+02:00") == 1
    confirmation = _rows(appt, event=Event.BOOKING_CONFIRMED)[0]
    assert confirmation.status == MessageStatus.SENT
    assert gateway.sent == [{"to": "201001234567", "caption": "أهلًا محمد، السبت 3 يناير 4:00 م"}]
    delivery = Delivery.objects.get(scheduled_message=confirmation)
    assert delivery.provider_message_id == "MSG1"


def test_reminders_too_close_to_the_appointment_are_never_created(clinic, django_capture_on_commit_callbacks):
    with freeze_time("2026-01-03 14:00:00+02:00"):  # 2 hours before: the 24h reminder is already past
        appt = _book(clinic, django_capture_on_commit_callbacks, local_dt(SATURDAY, time(16, 0)))
    reminders = _rows(appt, event=Event.REMINDER)
    assert [r.send_at for r in reminders] == [local_dt(SATURDAY, time(15, 0))]


def test_walk_in_gets_no_confirmation(clinic, django_capture_on_commit_callbacks):
    with freeze_time("2026-01-02 10:00:00+02:00"):
        appt = _book(clinic, django_capture_on_commit_callbacks, local_dt(SATURDAY, time(16, 0)),
                     source=BookingSource.WALK_IN)  # fmt: skip
    assert not _rows(appt, event=Event.BOOKING_CONFIRMED)


def test_queue_mode_skips_slots_only_reminder(clinic, django_capture_on_commit_callbacks):
    doctor = clinic["doctor"]
    doctor.booking_mode = BookingMode.QUEUE
    doctor.save()
    with freeze_time("2026-01-02 10:00:00+02:00"), django_capture_on_commit_callbacks(execute=True):
        appt = booking.book(
            patient=clinic["patient"], doctor=doctor, visit_type=clinic["vt"], by=clinic["staff"], day=SUNDAY
        )
    assert [r.event for r in _rows(appt)] == [Event.BOOKING_CONFIRMED, Event.REMINDER]
    assert _rows(appt, event=Event.REMINDER)[0].template.offset_minutes == 1440


def test_no_consent_creates_skipped_rows_with_reason(clinic, gateway, django_capture_on_commit_callbacks):
    Patient.objects.filter(pk=clinic["patient"].pk).update(messaging_consent=False)
    clinic["patient"].refresh_from_db()
    with freeze_time("2026-01-02 10:00:00+02:00"):
        appt = _book(clinic, django_capture_on_commit_callbacks, local_dt(SATURDAY, time(16, 0)))
    assert {(r.status, r.status_reason) for r in _rows(appt)} == {(MessageStatus.SKIPPED, "مفيش موافقة")}
    _run("2026-01-03 16:00:00+02:00")
    assert gateway.sent == []


def test_patient_preferring_email_and_both_channel(clinic, django_capture_on_commit_callbacks):
    Patient.objects.filter(pk=clinic["patient"].pk).update(preferred_channel="email")
    clinic["patient"].refresh_from_db()
    NotificationTemplate.objects.filter(event=Event.REMINDER).update(channel="both")
    with freeze_time("2026-01-02 10:00:00+02:00"):
        appt = _book(clinic, django_capture_on_commit_callbacks, local_dt(SATURDAY, time(16, 0)))
    assert [r.channel for r in _rows(appt, event=Event.BOOKING_CONFIRMED)] == ["email"]
    assert sorted(r.channel for r in _rows(appt, event=Event.REMINDER)) == ["email", "email", "whatsapp", "whatsapp"]


# --- reschedule / cancel ---------------------------------------------------------------------


def test_reschedule_cancels_old_reminders_and_schedules_new(clinic, gateway, django_capture_on_commit_callbacks):
    with freeze_time("2026-01-02 10:00:00+02:00"):
        old = _book(clinic, django_capture_on_commit_callbacks, local_dt(SATURDAY, time(16, 0)))
    _run("2026-01-02 10:00:05+02:00")  # confirmation goes out
    with freeze_time("2026-01-02 11:00:00+02:00"), django_capture_on_commit_callbacks(execute=True):
        new = booking.reschedule(old, by=clinic["staff"], new_start_at=local_dt(SUNDAY, time(17, 0)))

    old_rows = _rows(old)
    assert [r.status for r in old_rows] == [MessageStatus.SENT, MessageStatus.CANCELLED, MessageStatus.CANCELLED]
    assert {r.status_reason for r in old_rows[1:]} == {"اتعدل الميعاد"}
    new_rows = _rows(new)
    assert [r.event for r in new_rows] == [Event.RESCHEDULED, Event.REMINDER, Event.REMINDER]
    assert new_rows[1].send_at == local_dt(SATURDAY, time(17, 0))

    _run("2026-01-02 11:00:05+02:00")
    assert gateway.sent[-1]["caption"] == (
        "تم تعديل ميعادك مع د. سارة علي من السبت 3 يناير 4:00 م إلى الأحد 4 يناير، الساعة 5:00 م."
    )
    _run("2026-01-03 15:00:00+02:00")  # the old 1h reminder time: nothing goes out
    assert len(gateway.sent) == 2


def test_cancel_cancels_pending_and_sends_cancellation(clinic, gateway, django_capture_on_commit_callbacks):
    with freeze_time("2026-01-02 10:00:00+02:00"):
        appt = _book(clinic, django_capture_on_commit_callbacks, local_dt(SATURDAY, time(16, 0)))
    with freeze_time("2026-01-02 10:30:00+02:00"), django_capture_on_commit_callbacks(execute=True):
        booking.cancel(appt, by=clinic["staff"], reason="اعتذر")
    statuses = {r.event: r.status for r in _rows(appt)}
    assert statuses[Event.REMINDER] == MessageStatus.CANCELLED
    assert statuses[Event.BOOKING_CONFIRMED] == MessageStatus.CANCELLED  # never sent: cancelled before dispatch
    _run("2026-01-02 10:30:05+02:00")
    assert [m["caption"] for m in gateway.sent] == ["تم إلغاء ميعادك مع د. سارة علي يوم السبت 3 يناير. للحجز من جديد:"]


# --- freshness guard -------------------------------------------------------------------------


def test_stale_version_is_cancelled_at_send_time(clinic, gateway, django_capture_on_commit_callbacks):
    with freeze_time("2026-01-02 10:00:00+02:00"):
        appt = _book(clinic, django_capture_on_commit_callbacks, local_dt(SATURDAY, time(16, 0)))
    type(appt).objects.filter(pk=appt.pk).update(version=appt.version + 1)  # changed behind the outbox's back
    _run("2026-01-02 10:00:05+02:00")
    row = _rows(appt, event=Event.BOOKING_CONFIRMED)[0]
    assert (row.status, row.status_reason) == (MessageStatus.CANCELLED, "اتعدل الميعاد")
    assert gateway.sent == []


def test_scheduler_down_for_three_hours_never_sends_a_past_reminder(
    clinic, gateway, django_capture_on_commit_callbacks
):
    with freeze_time("2026-01-02 10:00:00+02:00"):
        appt = _book(clinic, django_capture_on_commit_callbacks, local_dt(SATURDAY, time(16, 0)))
    _run("2026-01-02 10:00:05+02:00")  # confirmation
    _run("2026-01-02 16:00:05+02:00")  # 24h reminder
    # Machine off from 13:30 until 16:30 on the day: the 15:00 "one hour left" reminder is now meaningless.
    _run("2026-01-03 16:30:00+02:00")
    one_hour = _rows(appt, event=Event.REMINDER)[1]
    assert (one_hour.status, one_hour.status_reason) == (MessageStatus.SKIPPED, "فات وقتها")
    assert len(gateway.sent) == 2


def test_catch_up_sends_reminder_still_within_its_lead_time(clinic, gateway, django_capture_on_commit_callbacks):
    with freeze_time("2026-01-02 10:00:00+02:00"):
        appt = _book(clinic, django_capture_on_commit_callbacks, local_dt(SATURDAY, time(16, 0)))
    # Off until 15:45: 15 minutes left ≥ min_lead 10 → still worth sending (along with the late confirmation).
    _run("2026-01-03 15:45:00+02:00")
    statuses = [r.status for r in _rows(appt)]
    assert statuses == [MessageStatus.SENT, MessageStatus.SKIPPED, MessageStatus.SENT]  # 24h one: < 120 min left
    _run("2026-01-03 15:56:00+02:00")
    assert len(gateway.sent) == 2


def test_nothing_is_ever_sent_twice(clinic, gateway, django_capture_on_commit_callbacks):
    with freeze_time("2026-01-02 10:00:00+02:00"):
        appt = _book(clinic, django_capture_on_commit_callbacks, local_dt(SATURDAY, time(16, 0)))
        services.schedule_for(appt)  # a duplicate hook call must not duplicate rows
    assert len(_rows(appt)) == 3
    for at in ("2026-01-02 10:00:05", "2026-01-02 10:00:30", "2026-01-02 16:00:00", "2026-01-02 16:00:20",
               "2026-01-03 15:00:00", "2026-01-03 15:00:20", "2026-01-03 15:30:00"):  # fmt: skip
        _run(at + "+02:00")
    assert len(gateway.sent) == 3
    assert len({(m["caption"]) for m in gateway.sent}) == 3


# --- quiet hours -----------------------------------------------------------------------------


def test_reminder_in_quiet_hours_is_deferred_to_quiet_end(clinic, django_capture_on_commit_callbacks):
    with freeze_time("2026-01-02 10:00:00+02:00"):
        appt = _book(clinic, django_capture_on_commit_callbacks, local_dt(SATURDAY, time(9, 30)))
    one_hour = _rows(appt, event=Event.REMINDER)[-1]
    assert one_hour.send_at == local_dt(SATURDAY, time(9, 0))  # 08:30 → quiet_end 09:00, 30 min still left
    assert one_hour.status == MessageStatus.PENDING


def test_deferred_reminder_too_close_is_skipped(clinic, django_capture_on_commit_callbacks):
    with freeze_time("2026-01-02 10:00:00+02:00"):
        appt = _book(clinic, django_capture_on_commit_callbacks, local_dt(SATURDAY, time(9, 0)))
    one_hour = _rows(appt, event=Event.REMINDER)[-1]
    assert (one_hour.status, one_hour.status_reason) == (MessageStatus.SKIPPED, "فات وقتها")


def test_quiet_skip_policy(clinic, django_capture_on_commit_callbacks):
    NotificationSettings.objects.filter(organization=clinic["org"]).update(quiet_policy=QuietPolicy.SKIP)
    with freeze_time("2026-01-02 10:00:00+02:00"):
        appt = _book(clinic, django_capture_on_commit_callbacks, local_dt(SATURDAY, time(9, 30)))
    one_hour = _rows(appt, event=Event.REMINDER)[-1]
    assert (one_hour.status, one_hour.status_reason) == (MessageStatus.SKIPPED, "ساعات الهدوء")


def test_late_night_cancellation_for_tomorrow_is_urgent_until_11pm(clinic, gateway, django_capture_on_commit_callbacks):
    with freeze_time("2026-01-02 10:00:00+02:00"):
        a1 = _book(clinic, django_capture_on_commit_callbacks, local_dt(SATURDAY, time(10, 0)))
        a2 = _book(clinic, django_capture_on_commit_callbacks, local_dt(SATURDAY, time(11, 0)))
    with freeze_time("2026-01-02 22:30:00+02:00"), django_capture_on_commit_callbacks(execute=True):
        booking.cancel(a1, by=clinic["staff"])
    with freeze_time("2026-01-02 23:30:00+02:00"), django_capture_on_commit_callbacks(execute=True):
        booking.cancel(a2, by=clinic["staff"])
    assert _rows(a1, event=Event.CANCELLED)[0].send_at == local_dt(date(2026, 1, 2), time(22, 30))
    assert _rows(a2, event=Event.CANCELLED)[0].send_at == local_dt(SATURDAY, time(9, 0))


def test_scheduler_restarting_at_night_defers_instead_of_sending(clinic, gateway, django_capture_on_commit_callbacks):
    with freeze_time("2026-01-02 21:50:00+02:00"):
        appt = _book(clinic, django_capture_on_commit_callbacks, local_dt(SUNDAY, time(16, 0)))
    _run("2026-01-02 23:30:00+02:00")  # confirmation was due at 21:50, scheduler only woke at 23:30
    row = _rows(appt, event=Event.BOOKING_CONFIRMED)[0]
    assert row.status == MessageStatus.PENDING and row.send_at == local_dt(SATURDAY, time(9, 0))
    assert gateway.sent == []
    _run("2026-01-03 09:00:10+02:00")
    assert len(gateway.sent) == 1


# --- retries, failures, fallback -------------------------------------------------------------


def test_temporary_failures_retry_then_fail_with_email_fallback(
    clinic, gateway, django_capture_on_commit_callbacks, mailoutbox
):
    cfg = SendingChannelConfig.for_org(clinic["org"], "email")
    cfg.config = {"host": "smtp.example.com", "username": "c@example.com", "password": "x"}
    cfg.is_active = True
    cfg.save()
    gateway.fail_with = (409, "not_ready")
    with freeze_time("2026-01-02 10:00:00+02:00"):
        appt = _book(clinic, django_capture_on_commit_callbacks, local_dt(SATURDAY, time(16, 0)))
    row = _rows(appt, event=Event.BOOKING_CONFIRMED)[0]
    _run("2026-01-02 10:00:01+02:00")
    row.refresh_from_db()
    assert (row.status, row.attempts) == (MessageStatus.PENDING, 1)
    assert row.next_attempt_at == local_dt(date(2026, 1, 2), time(10, 1, 1))
    for at in ("10:01:02", "10:06:03", "10:21:04"):
        _run(f"2026-01-02 {at}+02:00")
    row.refresh_from_db()
    assert (row.status, row.attempts) == (MessageStatus.FAILED, 4)
    assert Delivery.objects.filter(scheduled_message=row).count() == 4
    fallback = ScheduledMessage.objects.get(dedupe_key=row.dedupe_key + ":fallback")
    _run("2026-01-02 10:21:05+02:00")
    fallback.refresh_from_db()
    assert fallback.status == MessageStatus.SENT
    assert mailoutbox[0].to == ["m@example.com"] and "أهلًا محمد" in mailoutbox[0].body


def test_number_not_on_whatsapp_is_never_sent_and_cached(clinic, gateway, django_capture_on_commit_callbacks):
    gateway.registered = False
    with freeze_time("2026-01-02 10:00:00+02:00"):
        appt = _book(clinic, django_capture_on_commit_callbacks, local_dt(SATURDAY, time(16, 0)))
    _run("2026-01-02 10:00:01+02:00")
    row = _rows(appt, event=Event.BOOKING_CONFIRMED)[0]
    assert row.status == MessageStatus.FAILED and "واتساب" in row.status_reason
    assert gateway.sent == []
    assert WhatsAppNumber.objects.get(number="+201001234567").is_registered is False


def test_whatsapp_gap_between_sends(clinic, gateway, settings):
    settings.NOTIFY_SLEEP_BETWEEN_WA = True
    slept, clock = [], [100.0]
    d = dispatch.Dispatcher(sleep=slept.append, monotonic=lambda: clock[0], jitter=lambda: 4)
    for i in range(2):
        services.create_test_message(clinic["org"], channel="whatsapp", recipient="+201001234567", body=f"t{i}")
    d.run_once()
    assert slept == [19.0]  # 15s gap + 4s jitter after the first send
    assert len(gateway.sent) == 2


def test_interrupted_sends_are_not_resent(clinic, gateway):
    row = services.create_test_message(clinic["org"], channel="whatsapp", recipient="+201001234567", body="x")
    ScheduledMessage.objects.filter(pk=row.pk).update(status=MessageStatus.SENDING)
    assert dispatch.recover_interrupted() == 1
    dispatch.Dispatcher().run_once()
    assert gateway.sent == []


# --- templates & scheduler process -----------------------------------------------------------


def test_template_validation_rejects_unknown_variables():
    templating.validate_body("أهلًا {first_name} {time_label}")
    with pytest.raises(ValidationError):
        templating.validate_body("أهلًا {firstname}")
    with pytest.raises(ValidationError):
        templating.validate_body("أهلًا {first_name")


def test_seed_notifications_is_idempotent(org_a):
    call_command("seed_notifications")
    call_command("seed_notifications")
    assert NotificationTemplate.objects.filter(organization=org_a).count() == 9
    reminders = NotificationTemplate.objects.filter(organization=org_a, event=Event.REMINDER).order_by("order")
    assert [(t.offset_minutes, t.min_lead_minutes, t.applies_to_mode) for t in reminders] == [
        (1440, 120, "all"),
        (60, 10, "slots"),
    ]


def test_run_scheduler_once_writes_heartbeat_and_sends(clinic, gateway):
    services.create_test_message(clinic["org"], channel="whatsapp", recipient="+201001234567", body="hi")
    assert not services.scheduler_alive()
    call_command("run_scheduler", "--once")
    assert services.scheduler_alive()
    assert gateway.sent[0]["caption"] == "hi"
    with freeze_time(services.last_heartbeat().beat_at + timedelta(minutes=3)):
        assert not services.scheduler_alive()


def test_cleanup_expires_ancient_pending_rows(clinic):
    row = services.create_test_message(clinic["org"], channel="whatsapp", recipient="+201001234567", body="x")
    with freeze_time(row.send_at + timedelta(days=3)):
        assert dispatch.cleanup() == 1
