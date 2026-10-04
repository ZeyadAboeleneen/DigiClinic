from django.urls import path

from . import views

app_name = "messaging"

urlpatterns = [
    path("org/settings/email/", views.email_settings, name="email_settings"),
    path("org/settings/email/test/", views.email_test, name="email_test"),
    path("org/settings/whatsapp/", views.whatsapp_settings, name="whatsapp"),
    path("org/settings/whatsapp/status/", views.whatsapp_status, name="whatsapp_status"),
    path("org/settings/whatsapp/connect/", views.whatsapp_connect, name="whatsapp_connect"),
    path("org/settings/whatsapp/disconnect/", views.whatsapp_disconnect, name="whatsapp_disconnect"),
    path("org/settings/whatsapp/test/", views.whatsapp_test, name="whatsapp_test"),
    path("integrations/whatsapp/webhook/", views.whatsapp_webhook, name="whatsapp_webhook"),
]
