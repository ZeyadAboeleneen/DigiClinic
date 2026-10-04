from django.contrib import admin

from .models import Membership, Organization, OrganizationSettings


class SettingsInline(admin.StackedInline):
    model = OrganizationSettings


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name_ar", "slug", "is_active")
    inlines = [SettingsInline]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role", "is_active")
    list_filter = ("organization", "role", "is_active")
