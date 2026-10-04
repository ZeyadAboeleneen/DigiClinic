from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import OrgQuerySet, TenantScopedModel

# Special items without a catalog category are printed in this section, after everything else.
OTHER_SECTION_NAME = "أصناف أخرى"
OTHER_SECTION_ORDER = 10**9


class ImmutableQuotationError(Exception):
    pass


class QuoteSequence(TenantScopedModel):
    """Per-key counter, e.g. key "RG-2026/09/24" → 1, 2, 3. Locked with select_for_update when issuing."""

    key = models.CharField(max_length=60)
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "key"], name="uniq_quote_sequence")]

    def __str__(self):
        return f"{self.key}: {self.last_value}"


def pdf_upload_to(instance, filename):
    return f"org_{instance.organization_id}/quotations/{instance.issue_date:%Y}/{filename}"


class QuotationStatus(models.TextChoices):
    DRAFT = "draft", _("مسودة")
    FINALIZED = "finalized", _("صادر")
    SENT = "sent", _("اتبعت")
    ACCEPTED = "accepted", _("مقبول")
    REJECTED = "rejected", _("مرفوض")
    EXPIRED = "expired", _("منتهي")
    CANCELLED = "cancelled", _("ملغي")
    SUPERSEDED = "superseded", _("اتعدّل")


LOCKED_STATUSES = {s for s in QuotationStatus.values if s != QuotationStatus.DRAFT}


class QuotationQuerySet(OrgQuerySet):
    def issued(self):
        return self.exclude(status__in=[QuotationStatus.DRAFT, QuotationStatus.CANCELLED])


class Quotation(TenantScopedModel):
    number = models.CharField(_("رقم العرض"), max_length=60, blank=True, db_index=True)
    revision = models.PositiveSmallIntegerField(default=0)
    parent_quotation = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="revisions"
    )
    status = models.CharField(
        _("الحالة"), max_length=20, choices=QuotationStatus.choices, default=QuotationStatus.DRAFT, db_index=True
    )

    customer = models.ForeignKey(
        "customers.Customer",
        verbose_name=_("العميل"),
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="quotations",
    )
    contact = models.ForeignKey(
        "customers.Contact",
        verbose_name=_("المسؤول"),
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="quotations",
    )

    issue_date = models.DateField(_("التاريخ"))
    valid_until = models.DateField(_("سريان العرض حتى"))
    currency = models.CharField(max_length=3, default="EGP")
    prices_include_vat = models.BooleanField(default=False)
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("14.00"))
    intro_text = models.TextField(_("النص الافتتاحي"), blank=True)
    price_note = models.CharField(_("ملحوظة الأسعار"), max_length=300, blank=True)
    terms = models.JSONField(_("الشروط"), default=list, blank=True)
    internal_notes = models.TextField(_("ملاحظات داخلية"), blank=True, help_text=_("مش بتظهر للعميل"))

    items_count = models.PositiveIntegerField(default=0)
    customer_snapshot = models.JSONField(default=dict, blank=True)
    contact_snapshot = models.JSONField(default=dict, blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    finalized_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    pdf_file = models.FileField(upload_to=pdf_upload_to, blank=True, max_length=255)
    pdf_generated_at = models.DateTimeField(null=True, blank=True)

    objects = QuotationQuerySet.as_manager()

    class Meta:
        verbose_name = _("عرض سعر")
        verbose_name_plural = _("عروض الأسعار")
        ordering = ["-issue_date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "number", "revision"], condition=~Q(number=""), name="uniq_quote_number"
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status", "-issue_date"], name="quote_status_idx"),
            models.Index(fields=["organization", "customer"], name="quote_customer_idx"),
        ]

    def __str__(self):
        return self.display_number or f"مسودة #{self.pk}"

    @property
    def display_number(self):
        if not self.number:
            return ""
        return f"{self.number}-R{self.revision}" if self.revision else self.number

    @property
    def is_editable(self):
        return self.status == QuotationStatus.DRAFT

    @property
    def root(self):
        node = self
        while node.parent_quotation_id:
            node = node.parent_quotation
        return node


class QuotationItem(TenantScopedModel):
    """A price-list line: item, unit, unit price. No quantities, no totals."""

    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        "catalog.Product", null=True, blank=True, on_delete=models.SET_NULL, related_name="quotation_items"
    )
    category_name = models.CharField(max_length=150)
    section_order = models.BigIntegerField(default=0, help_text="group order * 10000 + category order")
    item_order = models.PositiveIntegerField(default=0)
    description = models.CharField(_("الصنف"), max_length=250)
    unit_name = models.CharField(_("الوحدة"), max_length=50)
    unit_price = models.DecimalField(
        _("السعر"),
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    class Meta:
        ordering = ["section_order", "item_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["quotation", "product"], condition=Q(product__isnull=False), name="uniq_product_per_quote"
            ),
        ]

    def __str__(self):
        return self.description

    def _guard(self):
        status = Quotation.objects.filter(pk=self.quotation_id).values_list("status", flat=True).first()
        if status and status != QuotationStatus.DRAFT:
            raise ImmutableQuotationError("Items of an issued quotation can't change; create a revision instead.")

    def save(self, *args, **kwargs):
        self._guard()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._guard()
        return super().delete(*args, **kwargs)

    @property
    def is_custom(self):
        return self.product_id is None
