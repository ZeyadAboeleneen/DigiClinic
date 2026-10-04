from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Attachment, Visit, Vitals


@admin.register(Visit)
class VisitAdmin(SimpleHistoryAdmin):
    list_display = ("patient", "doctor", "organization", "status", "started_at", "finished_at")
    list_filter = ("organization", "status")
    raw_id_fields = ("appointment", "patient")


@admin.register(Vitals)
class VitalsAdmin(SimpleHistoryAdmin):
    list_display = ("visit", "weight_kg", "bp_systolic", "bp_diastolic", "pulse", "recorded_at")
    raw_id_fields = ("visit",)


@admin.register(Attachment)
class AttachmentAdmin(SimpleHistoryAdmin):
    list_display = ("patient", "organization", "kind", "title", "content_type", "taken_on", "is_archived")
    list_filter = ("organization", "kind", "is_archived")
    raw_id_fields = ("patient", "visit")
