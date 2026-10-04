from django.contrib import admin

from .models import Appointment, AppointmentEvent, DayLedger


@admin.register(DayLedger)
class DayLedgerAdmin(admin.ModelAdmin):
    list_display = ("doctor", "date", "queue_numbers")
    list_filter = ("organization", "doctor")


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("patient", "doctor", "start_at", "status", "queue_number", "price")
    list_filter = ("organization", "doctor", "status")
    search_fields = ("patient__full_name",)


@admin.register(AppointmentEvent)
class AppointmentEventAdmin(admin.ModelAdmin):
    list_display = ("appointment", "from_status", "to_status", "by", "at")
    list_filter = ("organization",)
