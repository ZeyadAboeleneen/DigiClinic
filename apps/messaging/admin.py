from django.contrib import admin

from .models import Delivery, SendingChannelConfig


@admin.register(SendingChannelConfig)
class SendingChannelConfigAdmin(admin.ModelAdmin):
    list_display = ("organization", "kind", "is_active", "sender_identity", "status", "last_checked_at")
    exclude = ("config_encrypted",)


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("channel", "recipient", "status", "attempts", "sent_by", "queued_at")
    list_filter = ("organization", "channel", "status")
