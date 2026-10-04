from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import InboundMessage, NotificationSettings, NotificationTemplate, ScheduledMessage, SchedulerHeartbeat


@admin.register(NotificationSettings)
class NotificationSettingsAdmin(SimpleHistoryAdmin):
    list_display = ("organization", "is_enabled", "quiet_start", "quiet_end", "default_channel", "no_show_policy")


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(SimpleHistoryAdmin):
    list_display = ("name_ar", "organization", "event", "offset_minutes", "channel", "applies_to_mode", "is_enabled")
    list_filter = ("organization", "event", "is_enabled")


@admin.register(ScheduledMessage)
class ScheduledMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "organization", "event", "channel", "send_at", "status", "status_reason", "attempts")
    list_filter = ("organization", "status", "event", "channel")
    raw_id_fields = ("patient", "appointment", "template", "created_by")


@admin.register(SchedulerHeartbeat)
class SchedulerHeartbeatAdmin(admin.ModelAdmin):
    list_display = ("name", "beat_at", "pid", "host")


@admin.register(InboundMessage)
class InboundMessageAdmin(admin.ModelAdmin):
    list_display = ("from_number", "organization", "patient", "received_at")
    raw_id_fields = ("patient",)
