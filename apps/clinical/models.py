"""Medical record (docs/plan/02-database-schema.md §2.6). Never hard-deleted; history kept by simple-history."""

import secrets
from decimal import Decimal
from pathlib import Path

from django.conf import settings as dj_settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.models import TenantScopedModel


class VisitStatus(models.TextChoices):
    OPEN = "open", _("مفتوحة")
    FINISHED = "finished", _("خلصت")


class Visit(TenantScopedModel):
    appointment = models.OneToOneField("scheduling.Appointment", on_delete=models.PROTECT, related_name="visit")
    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="visits")
    doctor = models.ForeignKey("doctors.Doctor", on_delete=models.PROTECT, related_name="visits")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    chief_complaint = models.TextField(_("الشكوى"), blank=True)
    history = models.TextField(_("التاريخ المرضي"), blank=True)
    examination = models.TextField(_("الفحص"), blank=True)
    diagnosis = models.TextField(_("التشخيص"), blank=True)
    diagnosis_tags = models.JSONField(default=list, blank=True)
    plan = models.TextField(_("الخطة"), blank=True)
    notes = models.TextField(_("ملاحظات"), blank=True)
    followup_after_days = models.PositiveIntegerField(_("إعادة بعد (يوم)"), null=True, blank=True)
    # Reception's "إعادات محتاجة حجز" task is done once a follow-up is booked or dismissed.
    followup_handled = models.BooleanField(default=False)
    custom_fields = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=VisitStatus.choices, default=VisitStatus.OPEN)

    records = HistoricalRecords()

    class Meta:
        verbose_name = _("زيارة")
        verbose_name_plural = _("الزيارات")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.patient} — {self.created_at:%Y-%m-%d}"

    @classmethod
    def for_appointment(cls, appt):
        visit, _created = cls.objects.get_or_create(
            appointment=appt,
            defaults={"organization": appt.organization, "patient": appt.patient, "doctor": appt.doctor},
        )
        return visit


class Vitals(TenantScopedModel):
    visit = models.OneToOneField(Visit, on_delete=models.PROTECT, related_name="vitals")
    weight_kg = models.DecimalField(_("الوزن (كجم)"), max_digits=5, decimal_places=1, null=True, blank=True)
    height_cm = models.DecimalField(_("الطول (سم)"), max_digits=5, decimal_places=1, null=True, blank=True)
    bmi = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, editable=False)
    bp_systolic = models.PositiveSmallIntegerField(_("الضغط (الانقباضي)"), null=True, blank=True)
    bp_diastolic = models.PositiveSmallIntegerField(_("الضغط (الانبساطي)"), null=True, blank=True)
    pulse = models.PositiveSmallIntegerField(_("النبض"), null=True, blank=True)
    temperature_c = models.DecimalField(_("الحرارة"), max_digits=3, decimal_places=1, null=True, blank=True)
    spo2 = models.PositiveSmallIntegerField(_("نسبة الأكسجين %"), null=True, blank=True)
    blood_glucose = models.PositiveSmallIntegerField(_("السكر"), null=True, blank=True)
    recorded_by = models.ForeignKey(dj_settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    recorded_at = models.DateTimeField(default=timezone.now)

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("علامات حيوية")
        verbose_name_plural = _("العلامات الحيوية")

    def __str__(self):
        return f"{self.visit}"

    def save(self, *args, **kwargs):
        if self.weight_kg and self.height_cm:
            meters = Decimal(self.height_cm) / 100
            self.bmi = (Decimal(self.weight_kg) / (meters * meters)).quantize(Decimal("0.1"))
        else:
            self.bmi = None
        super().save(*args, **kwargs)

    @property
    def is_empty(self):
        return not any(
            getattr(self, f)
            for f in ("weight_kg", "height_cm", "bp_systolic", "pulse", "temperature_c", "spo2", "blood_glucose")
        )


class AttachmentKind(models.TextChoices):
    LAB = "lab", _("تحليل")
    RADIOLOGY = "radiology", _("أشعة")
    REPORT = "report", _("تقرير")
    OTHER = "other", _("أخرى")


def attachment_path(instance, filename):
    """Private storage: `org_<id>/patients/<file_number>/<random>.<ext>` — the original name is never used on disk."""
    ext = Path(filename).suffix.lower()
    return f"org_{instance.organization_id}/patients/{instance.patient.file_number}/{secrets.token_hex(12)}{ext}"


class Attachment(TenantScopedModel):
    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="attachments")
    visit = models.ForeignKey(Visit, null=True, blank=True, on_delete=models.PROTECT, related_name="attachments")
    file = models.FileField(upload_to=attachment_path)
    content_type = models.CharField(max_length=50)
    size = models.PositiveIntegerField(default=0)
    kind = models.CharField(_("النوع"), max_length=10, choices=AttachmentKind.choices, default=AttachmentKind.OTHER)
    title = models.CharField(_("العنوان"), max_length=150, blank=True)
    taken_on = models.DateField(_("التاريخ"), null=True, blank=True)
    uploaded_by = models.ForeignKey(dj_settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    is_archived = models.BooleanField(default=False)  # never hard-deleted

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("مرفق")
        verbose_name_plural = _("المرفقات")
        ordering = ["-taken_on", "-created_at"]

    def __str__(self):
        return self.title or self.get_kind_display()

    @property
    def is_image(self):
        return self.content_type.startswith("image/")
