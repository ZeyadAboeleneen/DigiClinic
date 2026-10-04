from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("org/settings/messages/", views.settings_messages, name="settings"),
    path("org/settings/messages/templates/<int:pk>/", views.template_edit, name="template_edit"),
    path("org/settings/messages/templates/<int:pk>/test/", views.template_test, name="template_test"),
    path("org/settings/messages/templates/<int:pk>/delete/", views.template_delete, name="template_delete"),
    path("org/settings/messages/templates/add-reminder/", views.template_add_reminder, name="template_add_reminder"),
    path("org/settings/messages/preview/", views.template_preview, name="template_preview"),
    path("messages/", views.message_log, name="log"),
    path("messages/<int:pk>/retry/", views.message_retry, name="retry"),
    path("messages/<int:pk>/cancel/", views.message_cancel, name="cancel"),
]
