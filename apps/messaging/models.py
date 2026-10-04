from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.crypto import decrypt_json, encrypt_json
from apps.core.models import OrgQuerySet, TenantScopedModel


class ChannelKind(models.TextChoices):
    EMAIL = "email", _("إيميل")
    WHATSAPP = "whatsapp", _("واتساب")


class ChannelStatus(models.TextChoices):
    UNKNOWN = "unknown", _("لسه متجربش")
    CONNECTED = "connected", _("شغال")
    DISCONNECTED = "disconnected", _("مفصول")
    NEEDS_QR = "needs_qr", _("محتاج QR")
    ERROR = "error", _("فيه مشكلة")


class SendingChannelConfig(TenantScopedModel):
    """One per organization and kind. Credentials are Fernet-encrypted in `config_encrypted`."""

    kind = models.CharField(max_length=20, choices=ChannelKind.choices)
    is_active = models.BooleanField(_("مفعّل"), default=False)
    display_name = models.CharField(_("اسم المرسل"), max_length=100, blank=True)
    config_encrypted = models.TextField(blank=True)
    sender_identity = models.CharField(_("المرسل"), max_length=254, blank=True)
    status = models.CharField(max_length=20, choices=ChannelStatus.choices, default=ChannelStatus.UNKNOWN)
    last_error = models.TextField(blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)

    objects = OrgQuerySet.as_manager()

    class Meta:
        verbose_name = _("قناة إرسال")
        verbose_name_plural = _("قنوات الإرسال")
        constraints = [models.UniqueConstraint(fields=["organization", "kind"], name="uniq_channel_kind")]

    def __str__(self):
        return f"{self.get_kind_display()} — {self.sender_identity or '-'}"

    @property
    def config(self) -> dict:
        return decrypt_json(self.config_encrypted)

    @config.setter
    def config(self, value: dict):
        self.config_encrypted = encrypt_json(value)

    @classmethod
    def for_org(cls, org, kind):
        return cls.objects.get_or_create(organization=org, kind=kind)[0]


class DeliveryStatus(models.TextChoices):
    QUEUED = "queued", _("في الطابور")
    SENDING = "sending", _("بيتبعت")
    SENT = "sent", _("اتبعت")
    DELIVERED = "delivered", _("اتسلّم")
    READ = "read", _("اتقرا")
    FAILED = "failed", _("فشل")


SUCCESS_STATUSES = {DeliveryStatus.SENT, DeliveryStatus.DELIVERED, DeliveryStatus.READ}
PENDING_STATUSES = {DeliveryStatus.QUEUED, DeliveryStatus.SENDING}


class Delivery(TenantScopedModel):
    """A generic outbound message record. Rebuilt in Phase 4 to be driven by `notifications.ScheduledMessage`."""

    channel = models.CharField(max_length=20, choices=ChannelKind.choices)
    recipient = models.CharField(max_length=254)
    recipient_name = models.CharField(max_length=150, blank=True)
    subject = models.CharField(max_length=250, blank=True)
    message_text = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=DeliveryStatus.choices, default=DeliveryStatus.QUEUED)
    provider_message_id = models.CharField(max_length=250, blank=True)
    error_message = models.TextField(blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    queued_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    objects = OrgQuerySet.as_manager()

    class Meta:
        verbose_name = _("إرسال")
        verbose_name_plural = _("عمليات الإرسال")
        ordering = ["-queued_at", "-id"]

    def __str__(self):
        return f"{self.get_channel_display()} → {self.recipient} ({self.status})"

    @property
    def is_pending(self):
        return self.status in PENDING_STATUSES
