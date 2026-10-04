from django.contrib import admin

from .models import Quotation, QuotationItem, QuoteSequence


class ItemInline(admin.TabularInline):
    model = QuotationItem
    extra = 0
    fields = ("description", "unit_name", "unit_price", "category_name")


@admin.register(Quotation)
class QuotationAdmin(admin.ModelAdmin):
    list_display = ("__str__", "customer", "status", "issue_date", "items_count", "organization")
    list_filter = ("organization", "status")
    search_fields = ("number", "customer__name")
    inlines = [ItemInline]


@admin.register(QuoteSequence)
class QuoteSequenceAdmin(admin.ModelAdmin):
    list_display = ("key", "last_value", "organization")
