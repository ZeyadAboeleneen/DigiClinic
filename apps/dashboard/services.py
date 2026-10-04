"""Dashboard & reports numbers (03 §3.9). All queries are aggregate (no per-row loops) and tenant-scoped."""

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.billing.models import Payment, PaymentMethod
from apps.clinical.models import Visit, VisitStatus
from apps.core.timeutils import CAIRO
from apps.messaging.models import DeliveryStatus
from apps.notifications.models import Event, MessageStatus, ScheduledMessage
from apps.patients.models import Patient
from apps.scheduling.models import Appointment, AppointmentStatus


def today():
    return timezone.localtime(timezone.now(), CAIRO).date()


def _range(start: date, end: date):
    """[start 00:00, end+1 00:00) in Cairo time, as aware datetimes."""
    return datetime.combine(start, time.min, tzinfo=CAIRO), datetime.combine(
        end + timedelta(days=1), time.min, tzinfo=CAIRO
    )


def day_counts(org, day):
    rows = Appointment.objects.filter(organization=org, date=day).values("status").annotate(n=Count("id"))
    c = {r["status"]: r["n"] for r in rows}
    return {
        "booked": c.get(AppointmentStatus.BOOKED, 0) + c.get(AppointmentStatus.CONFIRMED, 0),
        "arrived": c.get(AppointmentStatus.ARRIVED, 0),
        "in_consultation": c.get(AppointmentStatus.IN_CONSULTATION, 0),
        "completed": c.get(AppointmentStatus.COMPLETED, 0),
        "no_show": c.get(AppointmentStatus.NO_SHOW, 0),
    }


@dataclass
class Report:
    start: date
    end: date
    visits: int
    new_patients: int
    no_shows: int
    no_show_rate: float | None
    revenue_total: Decimal
    revenue_by_method: list  # [(label, amount)]
    top_diagnoses: list  # [(diagnosis, count)]
    reminders_sent: int
    reminders_delivered_rate: float | None
    reminders_read_rate: float | None
    per_day: list  # [(date, visits, no_shows, revenue)]


def report(org, start: date, end: date) -> Report:
    lo, hi = _range(start, end)
    appts = Appointment.objects.filter(organization=org, date__gte=start, date__lte=end)
    status_counts = dict(appts.values_list("status").annotate(n=Count("id")))
    visits = status_counts.get(AppointmentStatus.COMPLETED, 0)
    no_shows = status_counts.get(AppointmentStatus.NO_SHOW, 0)
    attended_or_missed = visits + no_shows

    payments = Payment.objects.filter(organization=org, received_at__gte=lo, received_at__lt=hi)
    by_method = dict(
        payments.values_list("method").annotate(
            s=Sum("amount", filter=Q(is_refund=False), default=0) - Sum("amount", filter=Q(is_refund=True), default=0)
        )
    )
    revenue_by_method = [(label, by_method[value]) for value, label in PaymentMethod.choices if by_method.get(value)]

    tags = Counter()
    for tag_list in Visit.objects.filter(
        organization=org, status=VisitStatus.FINISHED, finished_at__gte=lo, finished_at__lt=hi
    ).values_list("diagnosis_tags", flat=True):
        tags.update(tag_list)

    reminders = ScheduledMessage.objects.filter(
        organization=org, event=Event.REMINDER, status=MessageStatus.SENT, sent_at__gte=lo, sent_at__lt=hi
    )
    sent = reminders.count()
    delivered = (
        reminders.filter(deliveries__status__in=[DeliveryStatus.DELIVERED, DeliveryStatus.READ]).distinct().count()
    )
    read = reminders.filter(deliveries__status=DeliveryStatus.READ).distinct().count()

    per_day_visits = dict(appts.filter(status=AppointmentStatus.COMPLETED).values_list("date").annotate(n=Count("id")))
    per_day_missed = dict(appts.filter(status=AppointmentStatus.NO_SHOW).values_list("date").annotate(n=Count("id")))
    per_day_revenue = Counter()
    for received_at, amount, refund in payments.values_list("received_at", "amount", "is_refund"):
        per_day_revenue[timezone.localtime(received_at, CAIRO).date()] += -amount if refund else amount
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    per_day = [
        (d, per_day_visits.get(d, 0), per_day_missed.get(d, 0), per_day_revenue.get(d, Decimal(0)))
        for d in days
        if per_day_visits.get(d) or per_day_missed.get(d) or per_day_revenue.get(d)
    ]

    return Report(
        start=start,
        end=end,
        visits=visits,
        new_patients=Patient.objects.filter(organization=org, created_at__gte=lo, created_at__lt=hi).count(),
        no_shows=no_shows,
        no_show_rate=(no_shows / attended_or_missed) if attended_or_missed else None,
        revenue_total=sum((a for _l, a in revenue_by_method), Decimal(0)),
        revenue_by_method=revenue_by_method,
        top_diagnoses=tags.most_common(10),
        reminders_sent=sent,
        reminders_delivered_rate=(delivered / sent) if sent else None,
        reminders_read_rate=(read / sent) if sent else None,
        per_day=per_day,
    )


def month_bounds(day: date):
    start = day.replace(day=1)
    next_month = (start + timedelta(days=32)).replace(day=1)
    return start, next_month - timedelta(days=1)
