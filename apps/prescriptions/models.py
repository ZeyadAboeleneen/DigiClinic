"""Prescriptions (docs/plan/02-database-schema.md §2.7, 05-prescription-pdf.md). A `final` prescription is
immutable: editing it creates a new revision (`RX-2026-00042-R1`). Nothing here is ever hard-deleted."""

from django.conf import settings as dj_settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.models import OrgQuerySet, TenantScopedModel

# Suggested values for Drug.form / PrescriptionItem.form (free text is allowed).
DRUG_FORMS = ["قرص", "كبسولة", "شراب", "حقن", "نقط", "نقط أنف", "كريم", "بخاخ", "لبوس", "فوار", "أكياس"]


class Drug(TenantScopedModel):
    name = models.CharField(_("الاسم التجاري"), max_length=150)
    generic_name = models.CharField(_("الاسم العلمي"), max_length=200, blank=True)
    form = models.CharField(_("الشكل"), max_length=20, blank=True)
    strength = models.CharField(_("التركيز"), max_length=50, blank=True)
    aliases_ar = models.JSONField(_("أسماء بالعربي"), default=list, blank=True)
    default_instructions = models.CharField(_("الجرعة الافتراضية"), max_length=200, blank=True)
    default_duration = models.CharField(_("المدة الافتراضية"), max_length=100, blank=True)
    usage_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _("دوا")
        verbose_name_plural = _("الأدوية")
        ordering = ["-usage_count", "name"]
        constraints = [models.UniqueConstraint(fields=["organization", "name"], name="uniq_drug_name_per_org")]

    def __str__(self):
        return self.name


class PhraseKind(models.TextChoices):
    DOSE = "dose", _("الجرعة")
    FREQUENCY = "frequency", _("التكرار")
    TIMING = "timing", _("التوقيت")
    DURATION = "duration", _("المدة")


class DosePhrase(TenantScopedModel):
    text = models.CharField(max_length=100)
    kind = models.CharField(max_length=10, choices=PhraseKind.choices)
    order = models.PositiveSmallIntegerField(default=0)
    usage_count = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = _("جملة جاهزة")
        verbose_name_plural = _("الجمل الجاهزة")
        ordering = ["kind", "order", "id"]

    def __str__(self):
        return self.text


class PageSize(models.TextChoices):
    A5 = "A5", "A5"
    A4 = "A4", "A4"


class PrintMode(models.TextChoices):
    FULL = "full", _("تصميم كامل")
    PREPRINTED = "preprinted", _("ورق العيادة المطبوع")


class PrescriptionSettings(TenantScopedModel):
    page_size = models.CharField(_("مقاس الورق"), max_length=2, choices=PageSize.choices, default=PageSize.A5)
    print_mode = models.CharField(_("وضع الطباعة"), max_length=10, choices=PrintMode.choices, default=PrintMode.FULL)
    margin_top_mm = models.PositiveSmallIntegerField(_("هامش فوق (مم)"), default=45)
    margin_bottom_mm = models.PositiveSmallIntegerField(_("هامش تحت (مم)"), default=25)
    margin_right_mm = models.PositiveSmallIntegerField(_("هامش يمين (مم)"), default=12)
    margin_left_mm = models.PositiveSmallIntegerField(_("هامش شمال (مم)"), default=12)
    voice_dictation_enabled = models.BooleanField(_("الإملاء الصوتي"), default=True)
    reception_can_reprint = models.BooleanField(_("الاستقبال تقدر تعيد طباعة الروشتات"), default=False)

    history = HistoricalRecords()

    class Meta:
        verbose_name = _("إعدادات الروشتة")
        verbose_name_plural = _("إعدادات الروشتة")
        constraints = [models.UniqueConstraint(fields=["organization"], name="uniq_rx_settings")]

    def __str__(self):
        return f"{self.organization}"

    @classmethod
    def for_org(cls, org):
        return cls.objects.get_or_create(organization=org)[0]


class PrescriptionSequence(TenantScopedModel):
    """One row per org+year; `services.next_number()` increments it under select_for_update()."""

    year = models.PositiveSmallIntegerField()
    last = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "year"], name="uniq_rx_seq_year")]


class RxStatus(models.TextChoices):
    DRAFT = "draft", _("مسودة")
    FINAL = "final", _("معتمدة")
    VOIDED = "voided", _("ملغية")


def pdf_upload_to(instance, filename):
    return f"org_{instance.organization_id}/prescriptions/{instance.issued_at:%Y}/{filename}"


class Prescription(TenantScopedModel):
    visit = models.ForeignKey("clinical.Visit", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="prescriptions")  # fmt: skip
    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="prescriptions")
    doctor = models.ForeignKey("doctors.Doctor", on_delete=models.PROTECT, related_name="prescriptions")
    number = models.CharField(max_length=30, blank=True)  # assigned when final; revisions share the base number
    revision = models.PositiveSmallIntegerField(default=0)
    revision_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="revisions")
    status = models.CharField(max_length=10, choices=RxStatus.choices, default=RxStatus.DRAFT)
    advice = models.TextField(_("نصايح"), blank=True)
    next_visit_date = models.DateField(_("الزيارة الجاية"), null=True, blank=True)
    patient_snapshot = models.JSONField(default=dict, blank=True)
    doctor_snapshot = models.JSONField(default=dict, blank=True)
    allergy_ack = models.JSONField(default=list, blank=True)  # warnings the doctor confirmed before finalizing
    send_to_patient = models.BooleanField(_("ابعتها للمريض"), default=True)
    pdf_full = models.FileField(upload_to=pdf_upload_to, blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.ForeignKey(dj_settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")  # fmt: skip
    void_reason = models.CharField(max_length=200, blank=True)

    objects = OrgQuerySet.as_manager()
    history = HistoricalRecords()

    class Meta:
        verbose_name = _("روشتة")
        verbose_name_plural = _("الروشتات")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "number", "revision"],
                condition=~models.Q(number=""),
                name="uniq_rx_number_revision",
            )
        ]

    def __str__(self):
        return self.display_number or f"draft #{self.pk}"

    @property
    def display_number(self):
        if not self.number:
            return ""
        return f"{self.number}-R{self.revision}" if self.revision else self.number

    @property
    def is_editable(self):
        return self.status == RxStatus.DRAFT


class PrescriptionItem(TenantScopedModel):
    prescription = models.ForeignKey(Prescription, on_delete=models.CASCADE, related_name="items")
    drug = models.ForeignKey(Drug, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    drug_name = models.CharField(_("الدوا"), max_length=150)
    form = models.CharField(_("الشكل"), max_length=20, blank=True)
    instructions = models.CharField(_("الجرعة"), max_length=250, blank=True)
    duration = models.CharField(_("المدة"), max_length=100, blank=True)
    notes = models.CharField(_("ملاحظات"), max_length=200, blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    # Phase 8: lines added by voice must be confirmed by the doctor before the prescription can be finalized.
    needs_review = models.BooleanField(default=False)

    history = HistoricalRecords()

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.drug_name


class PrescriptionTemplate(TenantScopedModel):
    doctor = models.ForeignKey("doctors.Doctor", on_delete=models.CASCADE, related_name="rx_templates")
    name = models.CharField(_("اسم القالب"), max_length=100)
    advice = models.TextField(_("نصايح"), blank=True)
    usage_count = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = _("قالب روشتة")
        verbose_name_plural = _("قوالب الروشتات")
        ordering = ["-usage_count", "name"]

    def __str__(self):
        return self.name


class PrescriptionTemplateItem(TenantScopedModel):
    template = models.ForeignKey(PrescriptionTemplate, on_delete=models.CASCADE, related_name="items")
    drug = models.ForeignKey(Drug, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    drug_name = models.CharField(max_length=150)
    form = models.CharField(max_length=20, blank=True)
    instructions = models.CharField(max_length=250, blank=True)
    duration = models.CharField(max_length=100, blank=True)
    notes = models.CharField(max_length=200, blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
