from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import (
    DosePhrase,
    Drug,
    Prescription,
    PrescriptionItem,
    PrescriptionSequence,
    PrescriptionSettings,
    PrescriptionTemplate,
    PrescriptionTemplateItem,
)


@admin.register(Drug)
class DrugAdmin(admin.ModelAdmin):
    list_display = ("name", "generic_name", "form", "organization", "usage_count", "is_active")
    list_filter = ("organization", "is_active")
    search_fields = ("name", "generic_name")


@admin.register(DosePhrase)
class DosePhraseAdmin(admin.ModelAdmin):
    list_display = ("text", "kind", "organization", "order")


class ItemInline(admin.TabularInline):
    model = PrescriptionItem
    extra = 0
    raw_id_fields = ("drug",)


@admin.register(Prescription)
class PrescriptionAdmin(SimpleHistoryAdmin):
    list_display = ("__str__", "patient", "doctor", "status", "issued_at", "organization")
    list_filter = ("organization", "status")
    raw_id_fields = ("visit", "patient", "revision_of")
    inlines = [ItemInline]


@admin.register(PrescriptionSettings)
class PrescriptionSettingsAdmin(SimpleHistoryAdmin):
    list_display = ("organization", "page_size", "print_mode")


@admin.register(PrescriptionSequence)
class PrescriptionSequenceAdmin(admin.ModelAdmin):
    list_display = ("organization", "year", "last")


class TemplateItemInline(admin.TabularInline):
    model = PrescriptionTemplateItem
    extra = 0
    raw_id_fields = ("drug",)


@admin.register(PrescriptionTemplate)
class PrescriptionTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "doctor", "organization", "usage_count")
    inlines = [TemplateItemInline]
