"""The actual working schedule for a doctor on a given day (docs/plan/12-booking-engine.md §1)."""

from dataclasses import dataclass
from datetime import date, datetime

from django.db.models import Q

from apps.core.timeutils import local_dt, weekday_egypt
from apps.doctors.models import ScheduleException, ScheduleExceptionKind, WorkingPeriod


@dataclass(frozen=True)
class Period:
    start_at: datetime  # aware, Africa/Cairo
    end_at: datetime  # aware, Africa/Cairo
    max_patients: int | None
    period_id: str  # stable id of the source WorkingPeriod/ScheduleException, used as the queue-ledger key


def _from_working_period(wp: WorkingPeriod, day: date) -> Period:
    return Period(
        start_at=local_dt(day, wp.start_time),
        end_at=local_dt(day, wp.end_time),
        max_patients=wp.max_patients,
        period_id=f"wp-{wp.pk}",
    )


def _from_exception(exc: ScheduleException, day: date) -> Period:
    return Period(
        start_at=local_dt(day, exc.start_time),
        end_at=local_dt(day, exc.end_time),
        max_patients=exc.max_patients,
        period_id=f"exc-{exc.pk}",
    )


def periods_for(doctor, day: date) -> list[Period]:
    """The effective working periods for `doctor` on `day`, after exceptions are applied."""
    exceptions = list(ScheduleException.objects.filter(doctor=doctor, date=day))
    if any(e.kind == ScheduleExceptionKind.CLOSED for e in exceptions):
        return []

    custom = next((e for e in exceptions if e.kind == ScheduleExceptionKind.CUSTOM), None)
    extras = [e for e in exceptions if e.kind == ScheduleExceptionKind.EXTRA]

    periods: list[Period] = []
    if custom:
        periods.append(_from_exception(custom, day))
    else:
        weekday = weekday_egypt(day)
        qs = (
            WorkingPeriod.objects.filter(doctor=doctor, weekday=weekday, is_active=True)
            .filter(Q(effective_from__isnull=True) | Q(effective_from__lte=day))
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=day))
            .order_by("start_time")
        )
        periods += [_from_working_period(wp, day) for wp in qs]

    periods += [_from_exception(e, day) for e in extras]
    return sorted(periods, key=lambda p: p.start_at)
