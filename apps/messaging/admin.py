from django.contrib import admin

from .models import Delivery, SendingChannelConfig, WhatsAppNumber


@admin.register(SendingChannelConfig)
class SendingChannelConfigAdmin(admin.ModelAdmin):
    list_display = ("organization", "kind", "is_active", "sender_identity", "status", "last_checked_at")
    exclude = ("config_encrypted",)


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("channel", "recipient", "status", "attempts", "scheduled_message", "queued_at")
    raw_id_fields = ("scheduled_message",)
    list_filter = ("organization", "channel", "status")


@admin.register(WhatsAppNumber)
class WhatsAppNumberAdmin(admin.ModelAdmin):
    list_display = ("number", "organization", "is_registered", "checked_at")
