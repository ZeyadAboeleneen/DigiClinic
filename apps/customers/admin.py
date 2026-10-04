from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Contact, ContactChannel, Customer


class ContactInline(admin.TabularInline):
    model = Contact
    extra = 0
    fields = ("name", "job_title", "preferred_channel", "is_primary", "is_active")


@admin.register(Customer)
class CustomerAdmin(SimpleHistoryAdmin):
    list_display = ("name", "kind", "parent", "organization", "is_active")
    list_filter = ("organization", "kind", "is_active")
    search_fields = ("name", "tax_id")
    inlines = [ContactInline]


class ChannelInline(admin.TabularInline):
    model = ContactChannel
    extra = 0


@admin.register(Contact)
class ContactAdmin(SimpleHistoryAdmin):
    list_display = ("name", "customer", "job_title", "preferred_channel", "is_primary", "is_active")
    list_filter = ("organization", "is_active")
    search_fields = ("name", "customer__name")
    inlines = [ChannelInline]


@admin.register(ContactChannel)
class ContactChannelAdmin(SimpleHistoryAdmin):
    list_display = ("contact", "type", "value", "is_primary", "is_verified")
    list_filter = ("organization", "type")
    search_fields = ("value", "contact__name")
