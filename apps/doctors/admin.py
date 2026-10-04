from django.contrib import admin

from .models import Doctor, ScheduleException, VisitType, WorkingPeriod


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ("name_ar", "organization", "booking_mode", "is_active")
    list_filter = ("organization", "booking_mode", "is_active")


@admin.register(WorkingPeriod)
class WorkingPeriodAdmin(admin.ModelAdmin):
    list_display = ("doctor", "weekday", "start_time", "end_time", "max_patients", "is_active")
    list_filter = ("organization", "doctor", "weekday")


@admin.register(ScheduleException)
class ScheduleExceptionAdmin(admin.ModelAdmin):
    list_display = ("doctor", "date", "kind", "reason")
    list_filter = ("organization", "doctor", "kind")


@admin.register(VisitType)
class VisitTypeAdmin(admin.ModelAdmin):
    list_display = ("name_ar", "doctor", "duration_minutes", "price", "is_followup", "is_active")
    list_filter = ("organization", "doctor", "is_followup", "is_active")
