from django.conf import settings as dj_settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel


class PaymentMethod(models.TextChoices):
    CASH = "cash", _("كاش")
    CARD = "card", _("فيزا")
    INSTAPAY = "instapay", _("InstaPay")
    WALLET = "wallet", _("محفظة")


class Payment(TenantScopedModel):
    """Due = `appointment.price - discount`; paid/partial/unpaid is computed (billing.services.balance)."""

    appointment = models.ForeignKey("scheduling.Appointment", on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(_("المبلغ"), max_digits=10, decimal_places=2)
    method = models.CharField(
        _("طريقة الدفع"), max_length=10, choices=PaymentMethod.choices, default=PaymentMethod.CASH
    )
    received_by = models.ForeignKey(dj_settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    received_at = models.DateTimeField(default=timezone.now)
    note = models.CharField(_("ملاحظة"), max_length=200, blank=True)
    is_refund = models.BooleanField(_("استرداد"), default=False)

    class Meta:
        verbose_name = _("دفعة")
        verbose_name_plural = _("المدفوعات")
        ordering = ["received_at", "id"]
        indexes = [models.Index(fields=["organization", "received_at"])]

    def __str__(self):
        return f"{'-' if self.is_refund else ''}{self.amount} ({self.get_method_display()})"

    @property
    def signed_amount(self):
        return -self.amount if self.is_refund else self.amount
