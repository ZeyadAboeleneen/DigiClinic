"""Booking engine (docs/plan/12-booking-engine.md). Every write for a doctor/day happens under
`select_for_update()` on that day's `DayLedger` — this is the only place conflicts are prevented.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.timeutils import CAIRO
from apps.doctors.models import BookingMode
from apps.notifications import services as notifications

from .availability import periods_for
from .models import (
    ACTIVE_STATUSES,
    ALLOWED_TRANSITIONS,
    Appointment,
    AppointmentEvent,
    AppointmentStatus,
    BookingSource,
    DayLedger,
)


class BookingError(Exception):
    pass


class SlotTaken(BookingError):
    pass


class QueueFull(BookingError):
    pass


class InvalidTransition(Exception):
    pass


def _get_ledger(doctor, day):
    DayLedger.objects.get_or_create(organization=doctor.organization, doctor=doctor, date=day)
    return DayLedger.objects.select_for_update().get(organization=doctor.organization, doctor=doctor, date=day)


def _price_for(patient, doctor, visit_type):
    """A follow-up visit is free if the patient had a completed visit with this doctor within
    `visit_type.free_followup_days`. Returns (price, followup_of)."""
    if not visit_type.is_followup or not visit_type.free_followup_days:
        return visit_type.price, None
    cutoff = timezone.now() - timedelta(days=visit_type.free_followup_days)
    original = (
        Appointment.objects.filter(
            organization=doctor.organization,
            patient=patient,
            doctor=doctor,
            status=AppointmentStatus.COMPLETED,
            completed_at__gte=cutoff,
        )
        .order_by("-completed_at")
        .first()
    )
    if original is None:
        return visit_type.price, None
    return 0, original


def free_slots(doctor, visit_type, day):
    """Bookable slot-mode start times for `day` (list of aware datetimes), earliest first."""
    now = timezone.now()
    if day > (now + timedelta(days=doctor.booking_horizon_days)).date():
        return []
    duration = visit_type.duration_minutes or doctor.slot_minutes
    step = timedelta(minutes=doctor.slot_minutes)
    earliest = now + timedelta(minutes=doctor.min_notice_minutes)
    busy = list(
        Appointment.objects.filter(
            organization=doctor.organization, doctor=doctor, date=day, status__in=ACTIVE_STATUSES
        ).values_list("start_at", "end_at")
    )
    slots = []
    for period in periods_for(doctor, day):
        start = period.start_at
        while start + timedelta(minutes=duration) <= period.end_at:
            end = start + timedelta(minutes=duration)
            if start >= earliest and not any(start < b_end and end > b_start for b_start, b_end in busy):
                slots.append(start)
            start += step
    return slots


@dataclass
class PeriodAvailability:
    period_id: str
    label_start: object  # aware datetime, the period's start
    max_patients: int | None
    booked_count: int
    remaining: int | None
    next_number: int
    estimated_time: object  # aware datetime


def queue_availability(doctor, day):
    ledger = DayLedger.objects.filter(organization=doctor.organization, doctor=doctor, date=day).first()
    queue_numbers = ledger.queue_numbers if ledger else {}
    result = []
    for period in periods_for(doctor, day):
        booked_count = Appointment.objects.filter(
            organization=doctor.organization,
            doctor=doctor,
            date=day,
            period_key=period.period_id,
            status__in=ACTIVE_STATUSES,
            queue_number__isnull=False,
        ).count()
        next_number = queue_numbers.get(period.period_id, 0) + 1
        remaining = None if period.max_patients is None else max(period.max_patients - booked_count, 0)
        result.append(
            PeriodAvailability(
                period_id=period.period_id,
                label_start=period.start_at,
                max_patients=period.max_patients,
                booked_count=booked_count,
                remaining=remaining,
                next_number=next_number,
                estimated_time=period.start_at + timedelta(minutes=(next_number - 1) * doctor.queue_avg_minutes),
            )
        )
    return result


@transaction.atomic
def book(
    *,
    patient,
    doctor,
    visit_type,
    by,
    day=None,
    start_at=None,
    period_key=None,
    source=BookingSource.PHONE,
    overbook=False,
    notify=True,
):
    if doctor.booking_mode == BookingMode.SLOTS:
        if start_at is None:
            raise BookingError(_("اختار ميعاد."))
        day = start_at.date()
    elif day is None:
        raise BookingError(_("اختار يوم."))

    ledger = _get_ledger(doctor, day)
    periods = periods_for(doctor, day)

    if doctor.booking_mode == BookingMode.SLOTS:
        duration = visit_type.duration_minutes or doctor.slot_minutes
        end_at = start_at + timedelta(minutes=duration)
        in_schedule = any(p.start_at <= start_at and end_at <= p.end_at for p in periods)
        conflict = (
            Appointment.objects.filter(
                organization=doctor.organization, doctor=doctor, date=day, status__in=ACTIVE_STATUSES
            )
            .filter(start_at__lt=end_at, end_at__gt=start_at)
            .exists()
        )
        if not overbook and (not in_schedule or conflict):
            raise SlotTaken(_("الميعاد ده اتحجز أو برّه الجدول."))
        price, followup_of = _price_for(patient, doctor, visit_type)
        period = next((p for p in periods if p.start_at <= start_at and end_at <= p.end_at), None)
        appt = Appointment.objects.create(
            organization=doctor.organization,
            patient=patient,
            doctor=doctor,
            visit_type=visit_type,
            date=day,
            start_at=start_at,
            end_at=end_at,
            period_key=period.period_id if period else "",
            source=source,
            is_overbooked=overbook and (conflict or not in_schedule),
            price=price,
            followup_of=followup_of,
            booked_by=by,
        )
    else:
        if period_key:
            period = next((p for p in periods if p.period_id == period_key), None)
        else:
            period = periods[0] if periods else None
        if period is None:
            raise BookingError(_("الفترة دي مش متاحة في اليوم ده."))
        booked_count = Appointment.objects.filter(
            organization=doctor.organization,
            doctor=doctor,
            date=day,
            period_key=period.period_id,
            status__in=ACTIVE_STATUSES,
            queue_number__isnull=False,
        ).count()
        full = period.max_patients is not None and booked_count >= period.max_patients
        if full and not overbook:
            raise QueueFull(_("الطاقة خلصت في الفترة دي."))
        number = ledger.queue_numbers.get(period.period_id, 0) + 1
        ledger.queue_numbers[period.period_id] = number
        ledger.save(update_fields=["queue_numbers", "updated_at"])
        estimated = period.start_at + timedelta(minutes=(number - 1) * doctor.queue_avg_minutes)
        duration = visit_type.duration_minutes or doctor.queue_avg_minutes
        price, followup_of = _price_for(patient, doctor, visit_type)
        appt = Appointment.objects.create(
            organization=doctor.organization,
            patient=patient,
            doctor=doctor,
            visit_type=visit_type,
            date=day,
            start_at=estimated,
            end_at=estimated + timedelta(minutes=duration),
            period_key=period.period_id,
            queue_number=number,
            source=source,
            is_overbooked=full,
            price=price,
            followup_of=followup_of,
            booked_by=by,
        )

    AppointmentEvent.objects.create(
        organization=doctor.organization, appointment=appt, from_status="", to_status=appt.status, by=by
    )
    if notify:
        transaction.on_commit(lambda: notifications.schedule_for(appt, by=by))
    return appt


@transaction.atomic
def reschedule(appt, *, by, new_start_at=None, new_day=None, new_period_key=None, reason=""):
    appt = Appointment.objects.select_for_update().get(pk=appt.pk)
    if not appt.is_active:
        raise BookingError(_("الحجز ده مش نشط."))
    new_appt = book(
        patient=appt.patient,
        doctor=appt.doctor,
        visit_type=appt.visit_type,
        by=by,
        day=new_day,
        start_at=new_start_at,
        period_key=new_period_key,
        source=appt.source,
        notify=False,  # on_rescheduled sends "rescheduled" instead of a fresh confirmation
    )
    new_appt.price, new_appt.discount, new_appt.followup_of_id = appt.price, appt.discount, appt.followup_of_id
    new_appt.save(update_fields=["price", "discount", "followup_of", "updated_at"])
    appt.status = AppointmentStatus.RESCHEDULED
    appt.rescheduled_to = new_appt
    appt.version += 1
    appt.save(update_fields=["status", "rescheduled_to", "version", "updated_at"])
    AppointmentEvent.objects.create(
        organization=appt.organization,
        appointment=appt,
        from_status=AppointmentStatus.BOOKED,
        to_status=AppointmentStatus.RESCHEDULED,
        by=by,
        notes=reason,
    )
    transaction.on_commit(lambda: notifications.on_rescheduled(appt, new_appt, by=by))
    return new_appt


@transaction.atomic
def cancel(appt, *, by, reason="", notify=True):
    appt = Appointment.objects.select_for_update().get(pk=appt.pk)
    if not appt.is_active:
        raise BookingError(_("الحجز ده مش نشط."))
    old_status = appt.status
    appt.status = AppointmentStatus.CANCELLED
    appt.cancel_reason = reason
    appt.cancelled_at = timezone.now()
    appt.version += 1
    appt.save(update_fields=["status", "cancel_reason", "cancelled_at", "version", "updated_at"])
    AppointmentEvent.objects.create(
        organization=appt.organization,
        appointment=appt,
        from_status=old_status,
        to_status=appt.status,
        by=by,
        notes=reason,
    )
    if notify:
        transaction.on_commit(lambda: notifications.on_cancelled(appt, by=by))
    else:
        transaction.on_commit(lambda: notifications.cancel_pending(appt, _("الحجز اتلغى")))
    return appt


@transaction.atomic
def transition(appt, to_status, *, by, notes=""):
    appt = Appointment.objects.select_for_update().get(pk=appt.pk)
    allowed = ALLOWED_TRANSITIONS.get(appt.status, set())
    if to_status not in allowed:
        raise InvalidTransition(_("مينفعش تنقل الحجز من %(from)s لـ%(to)s.") % {"from": appt.status, "to": to_status})
    old_status = appt.status
    appt.status = to_status
    now = timezone.now()
    if _STATUS_ORDER.get(to_status, 0) < _STATUS_ORDER.get(old_status, 0):
        # "رجوع خطوة": forget the timestamp of the step being undone
        if old_status == AppointmentStatus.ARRIVED:
            appt.arrived_at = None
        elif old_status == AppointmentStatus.IN_CONSULTATION:
            appt.called_at = None
    elif to_status == AppointmentStatus.ARRIVED:
        appt.arrived_at = now
    elif to_status == AppointmentStatus.IN_CONSULTATION:
        appt.called_at = now
    elif to_status == AppointmentStatus.COMPLETED:
        appt.completed_at = now
    appt.save(update_fields=["status", "arrived_at", "called_at", "completed_at", "updated_at"])
    AppointmentEvent.objects.create(
        organization=appt.organization,
        appointment=appt,
        from_status=old_status,
        to_status=to_status,
        by=by,
        notes=notes,
    )
    if appt.queue_number and to_status in (AppointmentStatus.IN_CONSULTATION, AppointmentStatus.COMPLETED):
        doctor, day = appt.doctor, appt.date
        transaction.on_commit(lambda: notifications.on_queue_progress(doctor, day))
    return appt


_STATUS_ORDER = {
    AppointmentStatus.BOOKED: 1,
    AppointmentStatus.CONFIRMED: 1,
    AppointmentStatus.ARRIVED: 2,
    AppointmentStatus.IN_CONSULTATION: 3,
    AppointmentStatus.COMPLETED: 4,
}


@dataclass
class Suggestion:
    day: object  # date
    start_at: object  # aware datetime (slots) or estimated time (queue) — always set when a suggestion exists
    period_key: str | None  # set for queue-mode suggestions, used to actually book them


def affected_by_closing(doctor, day):
    """Active appointments on `day` that no longer fit the schedule (e.g. after a closing exception),
    each paired with the next free slot/period suggestion."""
    periods = periods_for(doctor, day)
    appts = Appointment.objects.filter(
        organization=doctor.organization, doctor=doctor, date=day, status__in=ACTIVE_STATUSES
    ).select_related("patient", "visit_type")
    affected = []
    for appt in appts:
        still_fits = any(p.start_at <= appt.start_at and appt.end_at <= p.end_at for p in periods)
        if still_fits:
            continue
        affected.append((appt, _next_available(doctor, appt.visit_type, after=day)))
    return affected


def _next_available(doctor, visit_type, *, after, horizon_days=30) -> "Suggestion | None":
    day = after + timedelta(days=1)
    for _i in range(horizon_days):
        if doctor.booking_mode == BookingMode.SLOTS:
            slots = free_slots(doctor, visit_type, day)
            if slots:
                return Suggestion(day=day, start_at=slots[0], period_key=None)
        else:
            for pa in queue_availability(doctor, day):
                if pa.remaining is None or pa.remaining > 0:
                    return Suggestion(day=day, start_at=pa.estimated_time, period_key=pa.period_id)
        day += timedelta(days=1)
    return None


@transaction.atomic
def reschedule_to_suggestion(appt, *, by, horizon_days=30):
    """Re-finds a fresh suggestion at submit time (the one shown on screen may be stale) and reschedules to it."""
    suggestion = _next_available(appt.doctor, appt.visit_type, after=appt.date, horizon_days=horizon_days)
    if suggestion is None:
        raise BookingError(_("مفيش ميعاد متاح قريب. لازم تأجيل يدوي."))
    if suggestion.period_key:
        return reschedule(
            appt, by=by, new_day=suggestion.day, new_period_key=suggestion.period_key, reason=_("قفل اليوم")
        )
    return reschedule(appt, by=by, new_start_at=suggestion.start_at, reason=_("قفل اليوم"))


# --- walk-in (12 §8) -------------------------------------------------------------------------


@transaction.atomic
def walk_in(*, patient, doctor, visit_type, by, overbook=False, now=None):
    """Same-day booking that is immediately `arrived`. No confirmation message (source=walk_in)."""
    now = now or timezone.now()
    today = timezone.localtime(now, CAIRO).date()
    common = {"patient": patient, "doctor": doctor, "visit_type": visit_type, "by": by, "source": BookingSource.WALK_IN}
    if doctor.booking_mode == BookingMode.SLOTS:
        slots = free_slots(doctor, visit_type, today)
        if slots:
            appt = book(start_at=slots[0], **common)
        elif overbook:
            appt = book(start_at=now, overbook=True, **common)
        else:
            raise SlotTaken(_("مفيش ميعاد فاضي النهارده."))
    else:
        periods = [p for p in periods_for(doctor, today) if p.end_at > now]
        if not periods:
            raise BookingError(_("مفيش فترة شغالة النهارده."))
        appt = book(day=today, period_key=periods[0].period_id, overbook=overbook, **common)
    return transition(appt, AppointmentStatus.ARRIVED, by=by, notes=_("حضور مباشر"))


# --- no-shows (12 §7) ------------------------------------------------------------------------


def find_rebook_slot(appt, ns) -> "Suggestion | None":
    """First free slot `auto_rebook_after_days` later, closest to the original time of day, within the window."""
    original = timezone.localtime(appt.start_at, CAIRO)
    original_minutes = original.hour * 60 + original.minute

    def distance(dt):
        local = timezone.localtime(dt, CAIRO)
        return abs(local.hour * 60 + local.minute - original_minutes)

    day = appt.date + timedelta(days=ns.auto_rebook_after_days)
    for _i in range(max(ns.auto_rebook_window_days, 1)):
        if appt.doctor.booking_mode == BookingMode.SLOTS:
            slots = free_slots(appt.doctor, appt.visit_type, day)
            if slots:
                return Suggestion(day=day, start_at=min(slots, key=distance), period_key=None)
        else:
            open_periods = [
                pa for pa in queue_availability(appt.doctor, day) if pa.remaining is None or pa.remaining > 0
            ]
            if open_periods:
                best = min(open_periods, key=lambda pa: distance(pa.label_start))
                return Suggestion(day=day, start_at=best.estimated_time, period_key=best.period_id)
        day += timedelta(days=1)
    return None


@transaction.atomic
def mark_no_show(appt, *, by=None):
    """Mark one appointment no_show and apply the clinic's policy. Returns the auto-rebooked appointment or None."""
    from apps.notifications.models import NoShowPolicy, NotificationSettings
    from apps.patients.models import Patient

    appt = transition(appt, AppointmentStatus.NO_SHOW, by=by)
    Patient.objects.filter(pk=appt.patient_id).update(no_show_count=F("no_show_count") + 1)
    ns = NotificationSettings.for_org(appt.organization)
    if ns.no_show_policy == NoShowPolicy.NONE:
        return None
    rebooked = None
    # At most one automatic rebooking per chain: a no-show of an auto-rebooked appointment only gets a message.
    if ns.no_show_policy == NoShowPolicy.AUTO_REBOOK and appt.auto_rebooked_from_id is None:
        suggestion = find_rebook_slot(appt, ns)
        if suggestion is not None:
            rebooked = book(
                patient=appt.patient,
                doctor=appt.doctor,
                visit_type=appt.visit_type,
                by=by,
                day=suggestion.day,
                start_at=None if suggestion.period_key else suggestion.start_at,
                period_key=suggestion.period_key,
                source=BookingSource.AUTO_REBOOK,
                notify=False,  # on_no_show sends "no_show_rebooked" instead of a plain confirmation
            )
            rebooked.auto_rebooked_from = appt
            rebooked.price, rebooked.discount = appt.price, appt.discount
            rebooked.save(update_fields=["auto_rebooked_from", "price", "discount", "updated_at"])
    transaction.on_commit(lambda: notifications.on_no_show(appt, rebooked=rebooked, by=by))
    return rebooked


@transaction.atomic
def undo_no_show(appt, *, by):
    """Back to booked; the automatic rebooking (if any) is withdrawn without messaging the patient again."""
    from apps.patients.models import Patient

    appt = transition(appt, AppointmentStatus.BOOKED, by=by, notes=_("رجوع عن لم يحضر"))
    Patient.objects.filter(pk=appt.patient_id, no_show_count__gt=0).update(no_show_count=F("no_show_count") - 1)
    for rebooked in Appointment.objects.filter(auto_rebooked_from=appt, status__in=ACTIVE_STATUSES):
        cancel(rebooked, by=by, reason=_("رجوع عن لم يحضر"), notify=False)
    transaction.on_commit(lambda: notifications.cancel_pending(appt, _("رجوع عن لم يحضر")))
    return appt


def due_no_shows(now=None):
    """Appointments that should become no_show now (12 §7). Read-only; `mark_no_shows` acts on them."""
    from apps.notifications.models import NoShowQueueMarkAt, NotificationSettings

    now = now or timezone.now()
    candidates = Appointment.objects.filter(
        status__in=[AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED],
        date__lte=timezone.localtime(now, CAIRO).date(),
    ).select_related("doctor", "organization")
    settings_cache, periods_cache, due = {}, {}, []
    for appt in candidates:
        ns = settings_cache.get(appt.organization_id)
        if ns is None:
            ns = settings_cache[appt.organization_id] = NotificationSettings.for_org(appt.organization)
        if appt.queue_number is None:
            if appt.start_at + timedelta(minutes=ns.no_show_grace_minutes) < now:
                due.append(appt)
            continue
        if ns.no_show_queue_mark_at != NoShowQueueMarkAt.PERIOD_END:
            continue
        key = (appt.doctor_id, appt.date)
        if key not in periods_cache:
            periods_cache[key] = {p.period_id: p.end_at for p in periods_for(appt.doctor, appt.date)}
        period_end = periods_cache[key].get(appt.period_key)
        if period_end is None:  # the period was removed later: wait until the end of that day
            period_end = datetime.combine(appt.date + timedelta(days=1), datetime.min.time(), tzinfo=CAIRO)
        if period_end < now:
            due.append(appt)
    return due


def mark_no_shows(now=None):
    """Scheduler job (every 2 minutes). One transaction per appointment so a failure doesn't block the rest."""
    marked = 0
    for appt in due_no_shows(now):
        try:
            mark_no_show(appt)
            marked += 1
        except InvalidTransition:
            continue  # changed meanwhile (e.g. just arrived)
    return marked
