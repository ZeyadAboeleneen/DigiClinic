from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.arabic import normalize_arabic
from apps.core.models import OrgQuerySet, TenantScopedModel
from apps.core.phones import to_e164, to_local

from .codes import suggest_code


class CustomerKind(models.TextChoices):
    GROUP = "group", _("مجموعة / شركة أم")
    CHAIN = "chain", _("سلسلة فنادق")
    HOTEL = "hotel", _("فندق")
    RESORT = "resort", _("منتجع")
    RESTAURANT = "restaurant", _("مطعم / كافيه")
    COMPANY = "company", _("شركة")
    OTHER = "other", _("أخرى")


# How each kind is introduced in the quotation's "To" block (reference PDF: "السادة / شركة Travco").
KIND_TITLES = {
    CustomerKind.GROUP: _("شركة"),
    CustomerKind.CHAIN: _("سلسلة الفنادق"),
    CustomerKind.HOTEL: _("فندق"),
    CustomerKind.RESORT: _("منتجع"),
    CustomerKind.RESTAURANT: _("مطعم"),
    CustomerKind.COMPANY: _("شركة"),
    CustomerKind.OTHER: "",
}

MAX_TREE_DEPTH = 5


class CustomerQuerySet(OrgQuerySet):
    def search(self, q: str):
        q = normalize_arabic(q)
        if not q:
            return self
        return self.filter(
            Q(name_normalized__icontains=q)
            | Q(contacts__name_normalized__icontains=q)
            | Q(contacts__channels__value__contains=q.lstrip("0"))
            | Q(tax_id__contains=q)
        ).distinct()


class Customer(TenantScopedModel):
    name = models.CharField(_("الاسم"), max_length=200)
    name_normalized = models.CharField(max_length=200, editable=False, db_index=True)
    code = models.CharField(
        _("كود العميل"),
        max_length=5,
        blank=True,
        help_text=_("حرفين أو أكتر بالإنجليزي، بيتكتبوا في رقم العرض: RG-2026/09/24"),
    )
    kind = models.CharField(_("النوع"), max_length=20, choices=CustomerKind.choices, default=CustomerKind.HOTEL)
    parent = models.ForeignKey(
        "self",
        verbose_name=_("تابع لـ"),
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
        help_text=_("مثلًا: الفندق تابع لسلسلة، والسلسلة تابعة لمجموعة."),
    )
    city = models.CharField(_("المدينة"), max_length=100, blank=True)
    area = models.CharField(_("المنطقة"), max_length=100, blank=True)
    address = models.TextField(_("العنوان"), blank=True)
    tax_id = models.CharField(_("الرقم الضريبي"), max_length=20, blank=True)
    notes = models.TextField(_("ملاحظات"), blank=True)
    is_active = models.BooleanField(_("نشط"), default=True)

    objects = CustomerQuerySet.as_manager()
    history = HistoricalRecords()

    class Meta:
        verbose_name = _("عميل")
        verbose_name_plural = _("العملاء")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name", "parent"], name="uniq_customer_name_in_parent", nulls_distinct=False
            ),
            models.UniqueConstraint(fields=["organization", "code"], condition=~Q(code=""), name="uniq_customer_code"),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = " ".join(self.name.split())
        self.name_normalized = normalize_arabic(self.name)
        self.code = (self.code or "").strip().upper()
        if not self.code:
            taken = set(
                Customer.objects.filter(organization_id=self.organization_id)
                .exclude(pk=self.pk)
                .values_list("code", flat=True)
            )
            self.code = suggest_code(self.name, taken)
        super().save(*args, **kwargs)

    def clean(self):
        if self.parent_id is None:
            return
        if self.parent.organization_id != self.organization_id:
            raise ValidationError({"parent": _("لازم يكون من نفس الشركة.")})
        node, depth = self.parent, 1
        while node is not None:
            if self.pk and node.pk == self.pk:
                raise ValidationError({"parent": _("مينفعش العميل يبقى تابع لنفسه أو لحاجة تابعة ليه.")})
            node, depth = node.parent, depth + 1
            if depth > MAX_TREE_DEPTH:
                raise ValidationError({"parent": _("الشجرة عميقة أكتر من اللازم.")})

    def ancestors(self):
        """Root first, excluding self."""
        chain, node = [], self.parent
        while node is not None and len(chain) < MAX_TREE_DEPTH:
            chain.append(node)
            node = node.parent
        return list(reversed(chain))

    def descendant_ids(self):
        ids, frontier = set(), [self.pk]
        while frontier:
            children = list(Customer.objects.filter(parent_id__in=frontier).values_list("pk", flat=True))
            frontier = [c for c in children if c not in ids]
            ids.update(frontier)
        return ids

    @property
    def path_display(self):
        return " ← ".join(c.name for c in [*self.ancestors(), self])

    def recipient_lines(self):
        """Lines for the quotation "To" block, root first: [("السادة / شركة", "Travco"), ("سلسلة الفنادق", "Jaz")]."""
        lines = []
        for i, node in enumerate([*self.ancestors(), self]):
            title = str(KIND_TITLES.get(node.kind, ""))
            if i == 0:
                title = f"{_('السادة')} / {title}" if title else str(_("السادة"))
            lines.append((title, node.name))
        return lines


class PreferredChannel(models.TextChoices):
    WHATSAPP = "whatsapp", _("واتساب")
    EMAIL = "email", _("إيميل")
    BOTH = "both", _("الاتنين")


class Contact(TenantScopedModel):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="contacts")
    name = models.CharField(_("الاسم"), max_length=150)
    name_normalized = models.CharField(max_length=150, editable=False, db_index=True)
    job_title = models.CharField(_("المنصب"), max_length=150, blank=True)
    department = models.CharField(_("الإدارة"), max_length=150, blank=True)
    salutation = models.CharField(_("اللقب"), max_length=20, blank=True, help_text=_("مثلًا: أ. / م. / د."))
    preferred_channel = models.CharField(
        _("طريقة الإرسال المفضلة"),
        max_length=10,
        choices=PreferredChannel.choices,
        default=PreferredChannel.WHATSAPP,
    )
    is_primary = models.BooleanField(_("المسؤول الأساسي"), default=False)
    is_active = models.BooleanField(_("نشط"), default=True)

    objects = OrgQuerySet.as_manager()
    history = HistoricalRecords()

    class Meta:
        verbose_name = _("مسؤول")
        verbose_name_plural = _("المسؤولين")
        ordering = ["-is_primary", "name"]

    def __str__(self):
        return f"{self.salutation} {self.name}".strip()

    def save(self, *args, **kwargs):
        self.name = " ".join(self.name.split())
        self.name_normalized = normalize_arabic(self.name)
        super().save(*args, **kwargs)
        if self.is_primary:
            Contact.objects.filter(customer_id=self.customer_id).exclude(pk=self.pk).update(is_primary=False)

    def primary_channel(self, type_):
        chans = [c for c in self.channels.all() if c.type == type_]
        return next((c for c in chans if c.is_primary), chans[0] if chans else None)


class ChannelType(models.TextChoices):
    WHATSAPP = "whatsapp", _("واتساب")
    PHONE = "phone", _("تليفون")
    EMAIL = "email", _("إيميل")


class ContactChannel(TenantScopedModel):
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="channels")
    type = models.CharField(_("النوع"), max_length=10, choices=ChannelType.choices)
    value = models.CharField(_("القيمة"), max_length=254)
    label = models.CharField(_("وصف"), max_length=50, blank=True, help_text=_("مثلًا: شخصي / الشغل"))
    is_primary = models.BooleanField(default=False)
    is_verified = models.BooleanField(default=False, help_text=_("اتبعتله قبل كده بنجاح"))

    objects = OrgQuerySet.as_manager()
    history = HistoricalRecords()

    class Meta:
        verbose_name = _("وسيلة تواصل")
        verbose_name_plural = _("وسائل التواصل")
        ordering = ["type", "-is_primary", "id"]
        constraints = [models.UniqueConstraint(fields=["contact", "type", "value"], name="uniq_contact_channel")]

    def __str__(self):
        return f"{self.get_type_display()}: {self.display_value}"

    @property
    def display_value(self):
        return self.value if self.type == ChannelType.EMAIL else to_local(self.value)

    def clean(self):
        if self.type == ChannelType.EMAIL:
            self.value = self.value.strip().lower()
        else:
            self.value = to_e164(self.value)

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
        if self.is_primary:
            ContactChannel.objects.filter(contact_id=self.contact_id, type=self.type).exclude(pk=self.pk).update(
                is_primary=False
            )
