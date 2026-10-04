from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.arabic import normalize_arabic
from apps.core.models import OrgQuerySet, TenantScopedModel


def _squash(text: str) -> str:
    return " ".join((text or "").split())


class ProductGroup(TenantScopedModel):
    """Top level (e.g. أدوات المائدة والتقديم). Used for filtering and "add whole group" — not printed in the PDF."""

    name = models.CharField(_("اسم المجموعة"), max_length=150)
    description = models.TextField(_("الوصف"), blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(_("نشطة"), default=True)

    class Meta:
        verbose_name = _("مجموعة منتجات")
        verbose_name_plural = _("مجموعات المنتجات")
        ordering = ["sort_order", "id"]
        constraints = [models.UniqueConstraint(fields=["organization", "name"], name="uniq_group_name")]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = _squash(self.name)
        super().save(*args, **kwargs)


class Category(TenantScopedModel):
    """Section printed as a pink row in the quotation PDF (e.g. أدوات المائدة الخشبية)."""

    group = models.ForeignKey(ProductGroup, on_delete=models.PROTECT, related_name="categories")
    name = models.CharField(_("اسم القسم"), max_length=150)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(_("نشط"), default=True)

    class Meta:
        verbose_name = _("قسم")
        verbose_name_plural = _("الأقسام")
        ordering = ["group__sort_order", "sort_order", "id"]
        constraints = [models.UniqueConstraint(fields=["organization", "name"], name="uniq_category_name")]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = _squash(self.name)
        super().save(*args, **kwargs)


class Unit(TenantScopedModel):
    name = models.CharField(_("الوحدة"), max_length=50)

    class Meta:
        verbose_name = _("وحدة")
        verbose_name_plural = _("الوحدات")
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["organization", "name"], name="uniq_unit_name")]

    def __str__(self):
        return self.name

    @classmethod
    def get_for(cls, organization, name):
        unit, _ = cls.objects.get_or_create(organization=organization, name=_squash(name))
        return unit


class ProductQuerySet(OrgQuerySet):
    def search(self, q: str):
        q = normalize_arabic(q)
        if not q:
            return self
        cond = Q()
        for word in q.split():
            cond &= Q(name_normalized__icontains=word)
        return self.filter(cond)


class Product(TenantScopedModel):
    """A catalog item. No reference price: prices are entered per quotation."""

    category = models.ForeignKey(Category, verbose_name=_("القسم"), on_delete=models.PROTECT, related_name="products")
    name = models.CharField(_("اسم الصنف"), max_length=200)
    name_normalized = models.CharField(max_length=200, editable=False, db_index=True)
    unit = models.ForeignKey(Unit, verbose_name=_("الوحدة"), on_delete=models.PROTECT, related_name="products")
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(_("نشط"), default=True)

    objects = ProductQuerySet.as_manager()
    history = HistoricalRecords()

    class Meta:
        verbose_name = _("منتج")
        verbose_name_plural = _("المنتجات")
        ordering = ["category__group__sort_order", "category__sort_order", "sort_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "category", "name"], name="uniq_product_in_category")
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = _squash(self.name)
        self.name_normalized = normalize_arabic(self.name)
        super().save(*args, **kwargs)
