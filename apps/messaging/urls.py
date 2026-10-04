from django.urls import path

from . import views

app_name = "messaging"

urlpatterns = [
    path("quotations/<int:pk>/send/", views.send_page, name="send"),
    path("quotations/<int:pk>/send/submit/", views.send_submit, name="send_submit"),
    path("quotations/<int:pk>/deliveries/", views.deliveries_partial, name="deliveries"),
    path("deliveries/<int:pk>/retry/", views.retry_delivery, name="retry"),
    path("org/settings/email/", views.email_settings, name="email_settings"),
    path("org/settings/email/test/", views.email_test, name="email_test"),
    path("org/settings/templates/", views.templates_settings, name="templates"),
    path("deliveries/<int:pk>/fallback-email/", views.fallback_email, name="fallback_email"),
    path("org/settings/whatsapp/", views.whatsapp_settings, name="whatsapp"),
    path("org/settings/whatsapp/status/", views.whatsapp_status, name="whatsapp_status"),
    path("org/settings/whatsapp/connect/", views.whatsapp_connect, name="whatsapp_connect"),
    path("org/settings/whatsapp/disconnect/", views.whatsapp_disconnect, name="whatsapp_disconnect"),
    path("org/settings/whatsapp/test/", views.whatsapp_test, name="whatsapp_test"),
    path("integrations/whatsapp/webhook/", views.whatsapp_webhook, name="whatsapp_webhook"),
]
