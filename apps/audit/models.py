from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import OrgQuerySet

ACTION_LABELS = {
    "auth.login": _("دخول"),
    "auth.logout": _("خروج"),
    "auth.login_failed": _("محاولة دخول فاشلة"),
    "delivery.queued": _("طلب إرسال"),
    "delivery.sent": _("اتبعت"),
    "delivery.failed": _("فشل الإرسال"),
    "patient.created": _("إضافة مريض"),
    "patient.edited": _("تعديل بيانات مريض"),
    "patient.allergy_added": _("إضافة حساسية"),
    "patient.condition_added": _("إضافة مرض مزمن"),
    "patient.merged": _("دمج مريضين"),
    "settings.company": _("تعديل بيانات الشركة"),
    "settings.doctor": _("تعديل جدول الدكتور"),
    "settings.visit_type": _("تعديل نوع زيارة"),
    "settings.email": _("تعديل إعدادات الإيميل"),
    "whatsapp.connect": _("ربط واتساب"),
    "whatsapp.disconnect": _("فصل واتساب"),
    "user.invited": _("دعوة مستخدم"),
    "user.invite_revoked": _("إلغاء دعوة"),
    "user.joined": _("مستخدم انضم"),
    "user.role": _("تغيير دور"),
    "user.toggled": _("تفعيل/إيقاف مستخدم"),
}

ACTION_GROUPS = {
    "patient": _("المرضى"),
    "delivery": _("الإرسال"),
    "auth": _("الدخول"),
    "user": _("المستخدمين"),
    "settings": _("الإعدادات"),
    "whatsapp": _("واتساب"),
}


class AuditEventQuerySet(OrgQuerySet):
    pass


class AuditEvent(models.Model):
    """Append-only activity log. Never edited or deleted from the UI (see save/delete)."""

    organization = models.ForeignKey(
        "organizations.Organization", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    action = models.CharField(max_length=50, db_index=True)
    target_type = models.CharField(max_length=50, blank=True)
    target_id = models.PositiveBigIntegerField(null=True, blank=True)
    summary = models.CharField(max_length=300, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = AuditEventQuerySet.as_manager()

    class Meta:
        verbose_name = _("حدث")
        verbose_name_plural = _("سجل النشاط")
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["organization", "-created_at"], name="audit_org_time_idx"),
            models.Index(fields=["target_type", "target_id"], name="audit_target_idx"),
        ]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.summary}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("Audit events are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit events are append-only.")

    @property
    def action_label(self):
        return ACTION_LABELS.get(self.action, self.action)

    @property
    def group(self):
        return self.action.split(".", 1)[0]
