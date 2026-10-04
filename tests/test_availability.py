from datetime import date, time

import pytest

from apps.core.timeutils import weekday_egypt
from apps.doctors.models import Doctor, ScheduleException, ScheduleExceptionKind, WorkingPeriod
from apps.scheduling.availability import periods_for


@pytest.fixture
def doctor(org_a):
    return Doctor.objects.create(organization=org_a, name_ar="سارة علي")


def _wp(org, doctor, weekday, start="14:00", end="21:00", **kw):
    return WorkingPeriod.objects.create(
        organization=org, doctor=doctor, weekday=weekday, start_time=start, end_time=end, **kw
    )


def test_weekday_egypt_mapping():
    # Saturday=0 ... Friday=6 (Egyptian week order)
    assert weekday_egypt(date(2026, 1, 3)) == 0  # Saturday
    assert weekday_egypt(date(2026, 1, 4)) == 1  # Sunday
    assert weekday_egypt(date(2026, 1, 5)) == 2  # Monday
    assert weekday_egypt(date(2026, 1, 8)) == 5  # Thursday
    assert weekday_egypt(date(2026, 1, 9)) == 6  # Friday


def test_weekly_closed_day_has_no_periods(org_a, doctor):
    # Working days Sat-Wed only; Thursday (5) and Friday (6) have no WorkingPeriod at all.
    for wd in range(5):
        _wp(org_a, doctor, wd)
    thursday = date(2026, 1, 8)
    assert weekday_egypt(thursday) == 5
    assert periods_for(doctor, thursday) == []


def test_holiday_exception_closes_a_normally_open_day(org_a, doctor):
    tuesday = date(2026, 1, 6)
    assert weekday_egypt(tuesday) == 3
    _wp(org_a, doctor, 3)
    assert len(periods_for(doctor, tuesday)) == 1
    ScheduleException.objects.create(
        organization=org_a, doctor=doctor, date=tuesday, kind=ScheduleExceptionKind.CLOSED, reason="إجازة"
    )
    assert periods_for(doctor, tuesday) == []


def test_custom_hours_replace_the_weekly_period(org_a, doctor):
    day = date(2026, 1, 6)
    weekday = weekday_egypt(day)
    _wp(org_a, doctor, weekday, start="14:00", end="21:00")
    ScheduleException.objects.create(
        organization=org_a,
        doctor=doctor,
        date=day,
        kind=ScheduleExceptionKind.CUSTOM,
        start_time=time(10, 0),
        end_time=time(13, 0),
    )
    periods = periods_for(doctor, day)
    assert len(periods) == 1
    assert periods[0].start_at.strftime("%H:%M") == "10:00"
    assert periods[0].end_at.strftime("%H:%M") == "13:00"


def test_extra_day_adds_to_the_weekly_periods(org_a, doctor):
    friday = date(2026, 1, 9)
    assert weekday_egypt(friday) == 6
    assert periods_for(doctor, friday) == []  # no WorkingPeriod on Friday
    ScheduleException.objects.create(
        organization=org_a,
        doctor=doctor,
        date=friday,
        kind=ScheduleExceptionKind.EXTRA,
        start_time=time(16, 0),
        end_time=time(19, 0),
    )
    periods = periods_for(doctor, friday)
    assert len(periods) == 1
    assert periods[0].start_at.strftime("%H:%M") == "16:00"


def test_future_schedule_change_does_not_affect_the_current_month(org_a, doctor):
    weekday = weekday_egypt(date(2026, 1, 6))
    old = _wp(org_a, doctor, weekday, start="14:00", end="21:00", effective_to=date(2026, 1, 31))
    _wp(org_a, doctor, weekday, start="10:00", end="18:00", effective_from=date(2026, 2, 1))

    this_month = periods_for(doctor, date(2026, 1, 6))
    assert len(this_month) == 1 and this_month[0].period_id == f"wp-{old.pk}"

    next_month = periods_for(doctor, date(2026, 2, 3))
    assert len(next_month) == 1 and next_month[0].start_at.strftime("%H:%M") == "10:00"


def test_dst_spring_forward_day_keeps_correct_local_hours(org_a, doctor):
    # Africa/Cairo DST starts 2026-04-25 (offset +02:00 -> +03:00); periods_for must still read 14:00-21:00 local.
    day = date(2026, 4, 25)
    weekday = weekday_egypt(day)
    _wp(org_a, doctor, weekday, start="14:00", end="21:00")
    periods = periods_for(doctor, day)
    assert len(periods) == 1
    p = periods[0]
    assert p.start_at.strftime("%H:%M") == "14:00"
    assert p.end_at.strftime("%H:%M") == "21:00"
    assert p.start_at.utcoffset().total_seconds() == 3 * 3600


def test_cancelled_exceptions_do_not_duplicate(org_a, doctor):
    """Multiple EXTRA exceptions on the same day all add up; multiple CUSTOM/CLOSED on the same day are rejected."""
    from django.db import IntegrityError

    day = date(2026, 1, 10)
    ScheduleException.objects.create(organization=org_a, doctor=doctor, date=day, kind=ScheduleExceptionKind.CLOSED)
    with pytest.raises(IntegrityError):
        ScheduleException.objects.create(organization=org_a, doctor=doctor, date=day, kind=ScheduleExceptionKind.CLOSED)
