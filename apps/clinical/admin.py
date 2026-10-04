from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Visit, Vitals


@admin.register(Visit)
class VisitAdmin(SimpleHistoryAdmin):
    list_display = ("patient", "doctor", "organization", "status", "started_at", "finished_at")
    list_filter = ("organization", "status")
    raw_id_fields = ("appointment", "patient")


@admin.register(Vitals)
class VitalsAdmin(SimpleHistoryAdmin):
    list_display = ("visit", "weight_kg", "bp_systolic", "bp_diastolic", "pulse", "recorded_at")
    raw_id_fields = ("visit",)
