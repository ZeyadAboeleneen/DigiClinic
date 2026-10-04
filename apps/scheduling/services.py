"""Booking engine (docs/plan/12-booking-engine.md). Every write for a doctor/day happens under
`select_for_update()` on that day's `DayLedger` — this is the only place conflicts are prevented.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.doctors.models import BookingMode

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
    # transaction.on_commit(lambda: notifications.schedule_for(appt))  # wired in Phase 4
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
    return new_appt


@transaction.atomic
def cancel(appt, *, by, reason=""):
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
    if to_status == AppointmentStatus.ARRIVED:
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
    return appt


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
