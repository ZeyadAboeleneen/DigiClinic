from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel


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
    clinic_name_ar = models.CharField(_("اسم العيادة بالعربي"), max_length=200, blank=True)
    clinic_name_en = models.CharField(_("اسم العيادة بالإنجليزي"), max_length=200, blank=True)
    logo = models.ImageField(_("اللوجو"), upload_to=logo_upload_to, blank=True)
    primary_color = models.CharField(_("اللون الأساسي"), max_length=7, default="#AE171C")
    dark_color = models.CharField(_("اللون الغامق"), max_length=7, default="#111111")
    neutral_color = models.CharField(_("اللون المحايد"), max_length=7, default="#F5F2EF")
    phones = models.JSONField(_("أرقام التليفون"), default=list, blank=True)
    address_ar = models.CharField(_("العنوان بالعربي"), max_length=300, blank=True)
    map_link = models.URLField(_("لينك الموقع على الخريطة"), blank=True)
    working_hours_text = models.CharField(_("مواعيد العمل (سطر الفوتر)"), max_length=300, blank=True)
    timezone = models.CharField(_("التوقيت"), max_length=50, default="Africa/Cairo")
    whatsapp_sender = models.CharField(_("رقم WhatsApp للإرسال"), max_length=20, blank=True)

    class Meta:
        verbose_name = _("إعدادات المنظمة")
        verbose_name_plural = _("إعدادات المنظمات")

    def __str__(self):
        return f"{self.organization} settings"


class Role(models.TextChoices):
    OWNER = "owner", _("مالك")
    ADMIN = "admin", _("مدير النظام")
    DOCTOR = "doctor", _("دكتور")
    RECEPTION = "reception", _("استقبال")
    VIEWER = "viewer", _("مشاهد")


class Membership(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(_("الدور"), max_length=20, choices=Role.choices, default=Role.RECEPTION)
    is_active = models.BooleanField(_("نشط"), default=True)

    class Meta:
        verbose_name = _("عضوية")
        verbose_name_plural = _("العضويات")
        constraints = [models.UniqueConstraint(fields=["user", "organization"], name="uniq_membership")]

    def __str__(self):
        return f"{self.user} @ {self.organization} ({self.role})"
