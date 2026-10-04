from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.models import TenantScopedModel


class BookingMode(models.TextChoices):
    SLOTS = "slots", _("ميعاد بالدقيقة")
    QUEUE = "queue", _("رقم دور")


class Doctor(TenantScopedModel):
    user = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    name_ar = models.CharField(_("الاسم بالعربي"), max_length=150)
    name_en = models.CharField(_("الاسم بالإنجليزي"), max_length=150, blank=True)
    title_ar = models.CharField(_("اللقب"), max_length=30, blank=True, help_text=_("د. / أ.د."))
    specialty_ar = models.CharField(_("التخصص"), max_length=150, blank=True)
    qualifications = models.JSONField(_("المؤهلات"), default=list, blank=True)
    booking_mode = models.CharField(
        _("طريقة الحجز"), max_length=10, choices=BookingMode.choices, default=BookingMode.SLOTS
    )
    slot_minutes = models.PositiveSmallIntegerField(_("مدة الميعاد الافتراضية (دقيقة)"), default=15)
    booking_horizon_days = models.PositiveSmallIntegerField(_("أقصى مدة حجز قدام (يوم)"), default=60)
    min_notice_minutes = models.PositiveIntegerField(_("أقل وقت قبل الميعاد (دقيقة)"), default=0)
    queue_avg_minutes = models.PositiveSmallIntegerField(_("متوسط مدة الكشف (دقيقة، للدور)"), default=15)
    near_turn_threshold = models.PositiveSmallIntegerField(_("دورك قرّب قبل N (0 = مقفول)"), default=0)
    allow_overbooking = models.BooleanField(_("يسمح بحجز استثنائي فوق الطاقة"), default=False)
    is_active = models.BooleanField(default=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("دكتور")
        verbose_name_plural = _("الدكاترة")

    def __str__(self):
        return f"{self.title_ar} {self.name_ar}".strip()


class WorkingPeriod(TenantScopedModel):
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="working_periods")
    weekday = models.PositiveSmallIntegerField(_("اليوم"), help_text=_("0=السبت ... 6=الجمعة"))
    start_time = models.TimeField(_("من"))
    end_time = models.TimeField(_("إلى"))
    max_patients = models.PositiveSmallIntegerField(_("الطاقة (للدور)"), null=True, blank=True)
    effective_from = models.DateField(_("ساري من"), null=True, blank=True)
    effective_to = models.DateField(_("ساري لحد"), null=True, blank=True)
    is_active = models.BooleanField(default=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("فترة عمل")
        verbose_name_plural = _("فترات العمل")
        ordering = ["weekday", "start_time"]

    def __str__(self):
        from apps.core.timeutils import WEEKDAY_LABELS

        return f"{WEEKDAY_LABELS[self.weekday]} {self.start_time:%H:%M}–{self.end_time:%H:%M}"

    def clean(self):
        if self.weekday is not None and not 0 <= self.weekday <= 6:
            raise ValidationError({"weekday": _("اليوم لازم يكون بين 0 و6.")})
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({"end_time": _("وقت النهاية لازم يكون بعد وقت البداية.")})
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({"effective_to": _("تاريخ النهاية لازم يكون بعد تاريخ البداية.")})


class ScheduleExceptionKind(models.TextChoices):
    CLOSED = "closed", _("مقفول")
    CUSTOM = "custom", _("مواعيد مختلفة")
    EXTRA = "extra", _("يوم إضافي")


class ScheduleException(TenantScopedModel):
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="schedule_exceptions")
    date = models.DateField(_("التاريخ"))
    kind = models.CharField(_("النوع"), max_length=10, choices=ScheduleExceptionKind.choices)
    start_time = models.TimeField(_("من"), null=True, blank=True)
    end_time = models.TimeField(_("إلى"), null=True, blank=True)
    max_patients = models.PositiveSmallIntegerField(_("الطاقة (للدور)"), null=True, blank=True)
    reason = models.CharField(_("السبب"), max_length=150, blank=True)

    class Meta:
        verbose_name = _("استثناء جدول")
        verbose_name_plural = _("استثناءات الجدول")
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(
                fields=["doctor", "date", "kind"],
                condition=models.Q(kind=ScheduleExceptionKind.CLOSED) | models.Q(kind=ScheduleExceptionKind.CUSTOM),
                name="uniq_closed_or_custom_exception_per_day",
            )
        ]

    def __str__(self):
        return f"{self.date} — {self.get_kind_display()}"

    def clean(self):
        if self.kind in (ScheduleExceptionKind.CUSTOM, ScheduleExceptionKind.EXTRA):
            if not self.start_time or not self.end_time:
                raise ValidationError(_("محتاج وقت البداية والنهاية لليوم ده."))
            if self.end_time <= self.start_time:
                raise ValidationError({"end_time": _("وقت النهاية لازم يكون بعد وقت البداية.")})


class VisitType(TenantScopedModel):
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="visit_types")
    name_ar = models.CharField(_("الاسم"), max_length=100)
    duration_minutes = models.PositiveSmallIntegerField(_("المدة (دقيقة)"), default=15)
    price = models.DecimalField(_("السعر"), max_digits=10, decimal_places=2)
    is_followup = models.BooleanField(_("نوع إعادة"), default=False)
    free_followup_days = models.PositiveSmallIntegerField(_("الإعادة مجانية خلال (يوم، 0 = مقفول)"), default=0)
    color = models.CharField(_("اللون"), max_length=7, default="#0E7C86")
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("نوع زيارة")
        verbose_name_plural = _("أنواع الزيارات")
        ordering = ["order", "id"]

    def __str__(self):
        return self.name_ar
