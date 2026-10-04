from django.contrib import admin

from .models import Allergy, ChronicCondition, Patient, PatientFieldDefinition, PatientSequence


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("full_name", "file_number", "organization", "phone", "is_active")
    list_filter = ("organization", "is_active", "gender")
    search_fields = ("full_name", "phone", "file_number")


@admin.register(Allergy)
class AllergyAdmin(admin.ModelAdmin):
    list_display = ("patient", "name", "severity")
    list_filter = ("organization", "severity")


@admin.register(ChronicCondition)
class ChronicConditionAdmin(admin.ModelAdmin):
    list_display = ("patient", "name")
    list_filter = ("organization",)


@admin.register(PatientFieldDefinition)
class PatientFieldDefinitionAdmin(admin.ModelAdmin):
    list_display = ("label_ar", "key", "scope", "type", "is_active")
    list_filter = ("organization", "scope")


@admin.register(PatientSequence)
class PatientSequenceAdmin(admin.ModelAdmin):
    list_display = ("organization", "last_value")
