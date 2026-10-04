"""Notifications engine (docs/plan/06-notifications-engine.md). Every outgoing message is a `ScheduledMessage`
row (the outbox); only `dispatcher.py` actually sends anything."""

from datetime import time

from django.conf import settings as dj_settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.models import BaseModel, OrgQuerySet, TenantScopedModel


class QuietPolicy(models.TextChoices):
    DEFER = "defer", _("يتأجل لنهاية ساعات الهدوء")
    SKIP = "skip", _("يتلغي")


class DefaultChannel(models.TextChoices):
    PREFERRED = "preferred", _("حسب المريض")
    WHATSAPP = "whatsapp", _("واتساب")
    EMAIL = "email", _("إيميل")
    BOTH = "both", _("الاتنين")


class NoShowPolicy(models.TextChoices):
    AUTO_REBOOK = "auto_rebook", _("إعادة حجز تلقائي + رسالة")
    NOTIFY = "notify", _("رسالة افتقدناك بس")
    NONE = "none", _("ولا حاجة")


class NoShowQueueMarkAt(models.TextChoices):
    PERIOD_END = "period_end", _("آخر الفترة")
    MANUAL = "manual", _("يدوي")


class NotificationSettings(TenantScopedModel):
    is_enabled = models.BooleanField(_("الرسايل التلقائية شغالة"), default=True)
    quiet_start = models.TimeField(_("ساعات الهدوء من"), default=time(22, 0))
    quiet_end = models.TimeField(_("لحد"), default=time(9, 0))
    quiet_policy = models.CharField(
        _("رسالة جت في ساعات الهدوء"), max_length=10, choices=QuietPolicy.choices, default=QuietPolicy.DEFER
    )
    # 06 §6.5: rescheduled/cancelled messages for today/tomorrow still go out during quiet hours before this time.
    urgent_until = models.TimeField(_("رسايل التعديل/الإلغاء العاجلة مسموحة لحد"), default=time(23, 0))
    default_channel = models.CharField(
        _("القناة الافتراضية"), max_length=10, choices=DefaultChannel.choices, default=DefaultChannel.PREFERRED
    )
    email_fallback = models.BooleanField(_("لو الواتساب فشل ابعت إيميل"), default=True)
    send_prescription_after_visit = models.BooleanField(_("ابعت الروشتة بعد الكشف"), default=False)
    prescription_channel = models.CharField(
        _("قناة الروشتة"), max_length=10, choices=DefaultChannel.choices[1:], default=DefaultChannel.WHATSAPP
    )
    no_show_policy = models.CharField(
        _("سياسة الغياب"), max_length=15, choices=NoShowPolicy.choices, default=NoShowPolicy.AUTO_REBOOK
    )
    no_show_grace_minutes = models.PositiveIntegerField(_("مهلة السماح (دقيقة)"), default=30)
    no_show_queue_mark_at = models.CharField(
        _("الغياب في نظام الدور"),
        max_length=15,
        choices=NoShowQueueMarkAt.choices,
        default=NoShowQueueMarkAt.PERIOD_END,
    )
    auto_rebook_after_days = models.PositiveIntegerField(_("إعادة الحجز بعد (يوم)"), default=7)
    auto_rebook_window_days = models.PositiveIntegerField(_("يدوّر على ميعاد خلال (يوم)"), default=14)
    wa_min_gap_seconds = models.PositiveIntegerField(_("أقل فاصل بين رسايل الواتساب (ثانية)"), default=15)

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("إعدادات الرسايل")
        verbose_name_plural = _("إعدادات الرسايل")
        constraints = [models.UniqueConstraint(fields=["organization"], name="uniq_notification_settings")]

    def __str__(self):
        return f"{self.organization}"

    @classmethod
    def for_org(cls, org):
        return cls.objects.get_or_create(organization=org)[0]


class Event(models.TextChoices):
    BOOKING_CONFIRMED = "booking_confirmed", _("تأكيد الحجز")
    REMINDER = "reminder", _("تذكير")
    RESCHEDULED = "rescheduled", _("تعديل الميعاد")
    CANCELLED = "cancelled", _("إلغاء")
    NO_SHOW_REBOOKED = "no_show_rebooked", _("غياب + إعادة حجز")
    NO_SHOW_MISSED = "no_show_missed", _("غياب (افتقدناك)")
    NEAR_TURN = "near_turn", _("دورك قرّب")
    PRESCRIPTION = "prescription", _("الروشتة")
    FOLLOWUP_DUE = "followup_due", _("ميعاد الإعادة")
    TEST = "test", _("تجربة")


TEMPLATE_EVENTS = [c for c in Event.choices if c[0] != Event.TEST]


class TemplateChannel(models.TextChoices):
    DEFAULT = "default", _("الافتراضية")
    WHATSAPP = "whatsapp", _("واتساب")
    EMAIL = "email", _("إيميل")
    BOTH = "both", _("الاتنين")


class AppliesTo(models.TextChoices):
    ALL = "all", _("الكل")
    SLOTS = "slots", _("بالميعاد بس")
    QUEUE = "queue", _("بالدور بس")


class NotificationTemplate(TenantScopedModel):
    event = models.CharField(_("الحدث"), max_length=20, choices=TEMPLATE_EVENTS)
    name_ar = models.CharField(_("الاسم"), max_length=100)
    offset_minutes = models.PositiveIntegerField(
        _("قبل الميعاد بـ(دقيقة)"), null=True, blank=True, help_text=_("للتذكير بس: 1440 = يوم، 60 = ساعة")
    )
    min_lead_minutes = models.PositiveIntegerField(
        _("أقل وقت متبقي (دقيقة)"),
        default=0,
        help_text=_("لو الباقي على الميعاد أقل من كده وقت الإرسال، متتبعتش"),
    )
    channel = models.CharField(
        _("القناة"), max_length=10, choices=TemplateChannel.choices, default=TemplateChannel.DEFAULT
    )
    body = models.TextField(_("النص"))
    email_subject = models.CharField(_("عنوان الإيميل"), max_length=200, blank=True)
    attach_pdf = models.BooleanField(_("إرفاق PDF"), default=False)
    applies_to_mode = models.CharField(_("ينطبق على"), max_length=10, choices=AppliesTo.choices, default=AppliesTo.ALL)
    is_enabled = models.BooleanField(_("مفعّل"), default=True)
    order = models.PositiveSmallIntegerField(default=0)

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("قالب رسالة")
        verbose_name_plural = _("قوالب الرسايل")
        ordering = ["order", "id"]

    def __str__(self):
        return self.name_ar

    def applies_to(self, booking_mode) -> bool:
        return self.applies_to_mode in (AppliesTo.ALL, booking_mode)


class MessageStatus(models.TextChoices):
    PENDING = "pending", _("مستنية")
    SENDING = "sending", _("بتتبعت")
    SENT = "sent", _("اتبعتت")
    FAILED = "failed", _("فشلت")
    CANCELLED = "cancelled", _("اتلغت")
    SKIPPED = "skipped", _("متبعتتش")


class MessageChannel(models.TextChoices):
    WHATSAPP = "whatsapp", _("واتساب")
    EMAIL = "email", _("إيميل")


class ScheduledMessage(TenantScopedModel):
    """The outbox. `rendered_text` is filled at send time so template/data edits are always reflected."""

    patient = models.ForeignKey(
        "patients.Patient", null=True, blank=True, on_delete=models.CASCADE, related_name="scheduled_messages"
    )
    appointment = models.ForeignKey(
        "scheduling.Appointment", null=True, blank=True, on_delete=models.CASCADE, related_name="messages"
    )
    template = models.ForeignKey(
        NotificationTemplate, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    event = models.CharField(max_length=20, choices=Event.choices)
    channel = models.CharField(max_length=10, choices=MessageChannel.choices)
    recipient = models.CharField(max_length=254, blank=True)
    send_at = models.DateTimeField()
    appointment_version = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=MessageStatus.choices, default=MessageStatus.PENDING)
    status_reason = models.CharField(max_length=200, blank=True)
    # Extra render variables that can't be recomputed at send time (old_date/old_time, patients_ahead, test body).
    context = models.JSONField(default=dict, blank=True)
    rendered_text = models.TextField(blank=True)
    rendered_subject = models.CharField(max_length=250, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)
    dedupe_key = models.CharField(max_length=200, unique=True)
    created_by = models.ForeignKey(
        dj_settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    objects = OrgQuerySet.as_manager()

    class Meta:
        verbose_name = _("رسالة مجدولة")
        verbose_name_plural = _("الرسايل المجدولة")
        ordering = ["send_at", "id"]
        indexes = [
            models.Index(fields=["status", "send_at", "next_attempt_at"]),
            models.Index(fields=["organization", "-send_at"]),
        ]

    def __str__(self):
        return f"{self.get_event_display()} → {self.get_channel_display()} ({self.status})"

    @property
    def can_retry(self):
        return self.status in (MessageStatus.FAILED, MessageStatus.SKIPPED) and self.event != Event.TEST

    @property
    def can_cancel(self):
        return self.status == MessageStatus.PENDING


class SchedulerHeartbeat(BaseModel):
    """One row per scheduler name; screens warn when `beat_at` is older than 2 minutes (06 §6.2)."""

    name = models.CharField(max_length=50, unique=True)
    beat_at = models.DateTimeField()
    pid = models.PositiveIntegerField(default=0)
    host = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"{self.name} @ {self.beat_at}"


class InboundMessage(TenantScopedModel):
    """Messages patients send to the clinic's WhatsApp (06 §6.7). Stored as plain text only — never acted on in v1."""

    patient = models.ForeignKey(
        "patients.Patient", null=True, blank=True, on_delete=models.SET_NULL, related_name="inbound_messages"
    )
    from_number = models.CharField(max_length=20)
    body = models.TextField(blank=True)
    provider_message_id = models.CharField(max_length=250, unique=True)
    received_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = _("رسالة واردة")
        verbose_name_plural = _("الرسايل الواردة")
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.from_number} @ {self.received_at:%Y-%m-%d %H:%M}"
