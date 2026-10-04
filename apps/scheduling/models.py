from django.conf import settings as dj_settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel


class DayLedger(TenantScopedModel):
    """One row per doctor+day. Every write for that day happens under `select_for_update()` on this row
    (docs/plan/12-booking-engine.md §4) — it's also where queue numbers are handed out."""

    doctor = models.ForeignKey("doctors.Doctor", on_delete=models.CASCADE, related_name="day_ledgers")
    date = models.DateField()
    # {period_id: last assigned queue number} — one counter per working period, never reused (12-booking-engine §3).
    queue_numbers = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["doctor", "date"], name="uniq_day_ledger")]

    def __str__(self):
        return f"{self.doctor} — {self.date}"


class AppointmentStatus(models.TextChoices):
    BOOKED = "booked", _("محجوز")
    CONFIRMED = "confirmed", _("مؤكد")
    ARRIVED = "arrived", _("وصل")
    IN_CONSULTATION = "in_consultation", _("جوه الكشف")
    COMPLETED = "completed", _("خلص")
    NO_SHOW = "no_show", _("لم يحضر")
    CANCELLED = "cancelled", _("ملغي")
    RESCHEDULED = "rescheduled", _("اتأجل")


ACTIVE_STATUSES = {
    AppointmentStatus.BOOKED,
    AppointmentStatus.CONFIRMED,
    AppointmentStatus.ARRIVED,
    AppointmentStatus.IN_CONSULTATION,
    AppointmentStatus.COMPLETED,
}
# Allowed status → status transitions (12-booking-engine.md §6). Anything else raises InvalidTransition.
ALLOWED_TRANSITIONS = {
    AppointmentStatus.BOOKED: {
        AppointmentStatus.CONFIRMED,
        AppointmentStatus.ARRIVED,
        AppointmentStatus.NO_SHOW,
        AppointmentStatus.CANCELLED,
        AppointmentStatus.RESCHEDULED,
    },
    AppointmentStatus.CONFIRMED: {
        AppointmentStatus.ARRIVED,
        AppointmentStatus.NO_SHOW,
        AppointmentStatus.CANCELLED,
        AppointmentStatus.RESCHEDULED,
    },
    AppointmentStatus.ARRIVED: {AppointmentStatus.IN_CONSULTATION, AppointmentStatus.BOOKED},
    AppointmentStatus.IN_CONSULTATION: {AppointmentStatus.COMPLETED, AppointmentStatus.ARRIVED},
    AppointmentStatus.COMPLETED: set(),
    AppointmentStatus.NO_SHOW: {AppointmentStatus.BOOKED},
    AppointmentStatus.CANCELLED: set(),
    AppointmentStatus.RESCHEDULED: set(),
}


class BookingSource(models.TextChoices):
    PHONE = "phone", _("تليفون")
    WALK_IN = "walk_in", _("حضور مباشر")
    AUTO_REBOOK = "auto_rebook", _("إعادة حجز تلقائي")
    WHATSAPP = "whatsapp", _("واتساب")
    ONLINE = "online", _("أونلاين")


class Appointment(TenantScopedModel):
    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="appointments")
    doctor = models.ForeignKey("doctors.Doctor", on_delete=models.PROTECT, related_name="appointments")
    visit_type = models.ForeignKey("doctors.VisitType", on_delete=models.PROTECT, related_name="appointments")
    date = models.DateField()
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    # The source Period's id (`wp-<pk>` or `exc-<pk>`, see apps.scheduling.availability.Period) — queue bookings
    # anchor their number/capacity to it; slot bookings keep it only for display/debugging.
    period_key = models.CharField(max_length=20, blank=True)
    queue_number = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=AppointmentStatus.choices, default=AppointmentStatus.BOOKED)
    version = models.PositiveIntegerField(default=1)
    source = models.CharField(max_length=20, choices=BookingSource.choices, default=BookingSource.PHONE)
    is_overbooked = models.BooleanField(default=False)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    followup_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    rescheduled_to = models.OneToOneField("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    auto_rebooked_from = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    booked_by = models.ForeignKey(dj_settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    arrived_at = models.DateTimeField(null=True, blank=True)
    called_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=200, blank=True)
    notes = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["start_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["doctor", "date", "period_key", "queue_number"],
                condition=~models.Q(status__in=[AppointmentStatus.CANCELLED, AppointmentStatus.RESCHEDULED])
                & models.Q(queue_number__isnull=False),
                name="uniq_queue_number_per_period",
            )
        ]
        indexes = [
            models.Index(fields=["organization", "doctor", "date"]),
            models.Index(fields=["organization", "patient", "-start_at"]),
            models.Index(fields=["organization", "status", "start_at"]),
        ]

    def __str__(self):
        return f"{self.patient} — {self.doctor} — {self.start_at:%Y-%m-%d %H:%M}"

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES


class AppointmentEvent(TenantScopedModel):
    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name="events")
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    by = models.ForeignKey(dj_settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    notes = models.CharField(max_length=300, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["at"]

    def __str__(self):
        return f"{self.appointment_id}: {self.from_status} → {self.to_status}"
