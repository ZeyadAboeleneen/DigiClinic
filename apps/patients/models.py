from django.conf import settings as dj_settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.arabic import normalize_arabic
from apps.core.models import OrgQuerySet, TenantScopedModel


class PatientSequence(TenantScopedModel):
    """One row per organization; `file_number`s are assigned from it under `select_for_update()`."""

    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization"], name="uniq_patient_sequence_per_org")]


class Gender(models.TextChoices):
    MALE = "male", _("ذكر")
    FEMALE = "female", _("أنثى")


class PreferredChannel(models.TextChoices):
    WHATSAPP = "whatsapp", _("واتساب")
    EMAIL = "email", _("إيميل")
    NONE = "none", _("من غير رسايل")


class PatientQuerySet(OrgQuerySet):
    def search(self, q: str):
        q = (q or "").strip()
        if not q:
            return self
        digits = "".join(ch for ch in q if ch.isdigit())
        if digits.startswith("0020"):
            digits = digits[4:]
        if digits.startswith("20") and len(digits) > 10:
            digits = digits[2:]
        digits = digits.lstrip("0")
        filters = Q(name_normalized__icontains=normalize_arabic(q))
        if digits:
            filters |= Q(phone__icontains=digits) | Q(whatsapp__icontains=digits)
        if q.isdigit():
            filters |= Q(file_number=int(q))
        return self.filter(filters).distinct()


class Patient(TenantScopedModel):
    file_number = models.PositiveIntegerField(_("رقم الملف"))
    full_name = models.CharField(_("الاسم"), max_length=150)
    name_normalized = models.CharField(max_length=150, editable=False, db_index=True)
    gender = models.CharField(_("النوع"), max_length=10, choices=Gender.choices)
    date_of_birth = models.DateField(_("تاريخ الميلاد"), null=True, blank=True)
    dob_is_estimated = models.BooleanField(_("السن تقريبي"), default=False)
    phone = models.CharField(_("رقم الموبايل"), max_length=20)
    whatsapp = models.CharField(_("رقم الواتساب"), max_length=20, blank=True)
    email = models.EmailField(_("الإيميل"), blank=True)
    guardian_name = models.CharField(_("اسم ولي الأمر"), max_length=150, blank=True)
    address = models.CharField(_("العنوان"), max_length=300, blank=True)
    occupation = models.CharField(_("المهنة"), max_length=150, blank=True)
    preferred_channel = models.CharField(
        _("القناة المفضلة"), max_length=10, choices=PreferredChannel.choices, default=PreferredChannel.WHATSAPP
    )
    messaging_consent = models.BooleanField(_("موافق على استقبال رسايل"), default=False)
    consent_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(_("ملاحظات إدارية"), blank=True)
    custom_fields = models.JSONField(default=dict, blank=True)
    no_show_count = models.PositiveIntegerField(default=0, editable=False)
    merged_into = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="merged_duplicates"
    )
    is_active = models.BooleanField(default=True)

    objects = PatientQuerySet.as_manager()
    history = HistoricalRecords()

    class Meta:
        verbose_name = _("مريض")
        verbose_name_plural = _("المرضى")
        ordering = ["-id"]
        constraints = [models.UniqueConstraint(fields=["organization", "file_number"], name="uniq_patient_file_number")]
        indexes = [
            models.Index(fields=["organization", "phone"]),
            models.Index(fields=["organization", "name_normalized"]),
        ]

    def __str__(self):
        return f"{self.full_name} (#{self.file_number})"

    def save(self, *args, **kwargs):
        self.full_name = " ".join(self.full_name.split())
        self.name_normalized = normalize_arabic(self.full_name)
        if not self.whatsapp:
            self.whatsapp = self.phone
        if self.messaging_consent and not self.consent_at:
            self.consent_at = timezone.now()
        super().save(*args, **kwargs)


class AllergySeverity(models.TextChoices):
    MILD = "mild", _("خفيفة")
    MODERATE = "moderate", _("متوسطة")
    SEVERE = "severe", _("شديدة")


class Allergy(TenantScopedModel):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="allergies")
    name = models.CharField(_("الحساسية"), max_length=150)
    severity = models.CharField(
        _("الشدة"), max_length=10, choices=AllergySeverity.choices, default=AllergySeverity.MODERATE
    )
    notes = models.CharField(_("ملاحظات"), max_length=300, blank=True)
    recorded_by = models.ForeignKey(dj_settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = _("حساسية")
        verbose_name_plural = _("الحساسيات")

    def __str__(self):
        return self.name


class ChronicCondition(TenantScopedModel):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="chronic_conditions")
    name = models.CharField(_("المرض المزمن"), max_length=150)
    notes = models.CharField(_("ملاحظات"), max_length=300, blank=True)
    recorded_by = models.ForeignKey(dj_settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = _("مرض مزمن")
        verbose_name_plural = _("الأمراض المزمنة")

    def __str__(self):
        return self.name


class FieldType(models.TextChoices):
    TEXT = "text", _("نص")
    NUMBER = "number", _("رقم")
    DATE = "date", _("تاريخ")
    CHOICE = "choice", _("اختيار")
    BOOL = "bool", _("صح/غلط")


class FieldScope(models.TextChoices):
    PATIENT = "patient", _("المريض")
    VISIT = "visit", _("الزيارة")


class PatientFieldDefinition(TenantScopedModel):
    key = models.SlugField(_("المفتاح"), max_length=50)
    label_ar = models.CharField(_("التسمية"), max_length=100)
    type = models.CharField(_("النوع"), max_length=10, choices=FieldType.choices, default=FieldType.TEXT)
    choices = models.JSONField(default=list, blank=True)
    scope = models.CharField(_("النطاق"), max_length=10, choices=FieldScope.choices, default=FieldScope.PATIENT)
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _("حقل إضافي")
        verbose_name_plural = _("الحقول الإضافية")
        ordering = ["scope", "order", "id"]
        constraints = [models.UniqueConstraint(fields=["organization", "key"], name="uniq_field_key_per_org")]

    def __str__(self):
        return self.label_ar
