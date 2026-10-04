from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("appointment", "organization", "amount", "method", "is_refund", "received_by", "received_at")
    list_filter = ("organization", "method", "is_refund")
    raw_id_fields = ("appointment",)
