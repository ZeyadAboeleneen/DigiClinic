from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel

# {code} = customer code (RG). A second quotation for the same code+date gets "-2", "-3", ...
DEFAULT_QUOTE_NUMBER_FORMAT = "{code}-{year}/{month:02d}/{day:02d}"


class ValidityMode(models.TextChoices):
    END_OF_MONTH = "end_of_month", _("لحد آخر الشهر")
    DAYS = "days", _("عدد أيام")


class Organization(BaseModel):
    name_ar = models.CharField(_("الاسم بالعربي"), max_length=200)
    name_en = models.CharField(_("الاسم بالإنجليزي"), max_length=200, blank=True)
    slug = models.SlugField(unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _("منظمة")
        verbose_name_plural = _("المنظمات")

    def __str__(self):
        return self.name_ar


def logo_upload_to(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower()
    return f"org_{instance.organization_id}/branding/logo.{ext}"


class OrganizationSettings(BaseModel):
    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name="settings")
    logo = models.ImageField(_("اللوجو"), upload_to=logo_upload_to, blank=True)
    primary_color = models.CharField(_("اللون الأساسي"), max_length=7, default="#AE171C")
    dark_color = models.CharField(_("اللون الغامق"), max_length=7, default="#111111")
    neutral_color = models.CharField(_("اللون المحايد"), max_length=7, default="#F5F2EF")
    section_row_color = models.CharField(_("لون صف القسم"), max_length=7, default="#FBECEC")
    tagline_ar = models.CharField(_("السطر التعريفي"), max_length=300, blank=True)
    phones = models.JSONField(_("أرقام التليفون"), default=list, blank=True)
    email_display = models.EmailField(_("الإيميل الظاهر"), blank=True)
    address_ar = models.CharField(_("العنوان بالعربي"), max_length=300, blank=True)
    address_en = models.CharField(_("العنوان بالإنجليزي"), max_length=300, blank=True)
    quote_number_prefix = models.CharField(_("بادئة رقم العرض"), max_length=10, default="AB")
    quote_number_format = models.CharField(_("صيغة رقم العرض"), max_length=80, default=DEFAULT_QUOTE_NUMBER_FORMAT)
    validity_mode = models.CharField(
        _("سريان العرض"), max_length=20, choices=ValidityMode.choices, default=ValidityMode.END_OF_MONTH
    )
    default_validity_days = models.PositiveSmallIntegerField(_("مدة سريان العرض (أيام)"), default=30)
    vat_rate = models.DecimalField(_("نسبة الضريبة %"), max_digits=5, decimal_places=2, default=Decimal("14.00"))
    prices_include_vat = models.BooleanField(_("الأسعار شاملة الضريبة"), default=False)
    default_intro_text = models.TextField(_("النص الافتتاحي"), blank=True)
    price_note = models.CharField(_("ملحوظة الأسعار"), max_length=300, blank=True)
    default_terms = models.JSONField(_("الشروط الافتراضية"), default=list, blank=True)
    default_whatsapp_message = models.TextField(_("رسالة WhatsApp الافتراضية"), blank=True)
    default_email_subject = models.CharField(_("عنوان الإيميل الافتراضي"), max_length=200, blank=True)
    default_email_body = models.TextField(_("نص الإيميل الافتراضي"), blank=True)
    whatsapp_sender = models.CharField(_("رقم WhatsApp للإرسال"), max_length=20, blank=True)

    class Meta:
        verbose_name = _("إعدادات المنظمة")
        verbose_name_plural = _("إعدادات المنظمات")

    def __str__(self):
        return f"{self.organization} settings"

    def default_valid_until(self, issue_date):
        import calendar
        from datetime import timedelta

        if self.validity_mode == ValidityMode.DAYS:
            return issue_date + timedelta(days=self.default_validity_days)
        last_day = calendar.monthrange(issue_date.year, issue_date.month)[1]
        return issue_date.replace(day=last_day)


class Role(models.TextChoices):
    OWNER = "owner", _("مالك")
    ADMIN = "admin", _("مدير النظام")
    MANAGER = "manager", _("مدير")
    SALES = "sales", _("مبيعات")
    VIEWER = "viewer", _("مشاهد")


class Membership(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(_("الدور"), max_length=20, choices=Role.choices, default=Role.SALES)
    is_active = models.BooleanField(_("نشط"), default=True)

    class Meta:
        verbose_name = _("عضوية")
        verbose_name_plural = _("العضويات")
        constraints = [models.UniqueConstraint(fields=["user", "organization"], name="uniq_membership")]

    def __str__(self):
        return f"{self.user} @ {self.organization} ({self.role})"
