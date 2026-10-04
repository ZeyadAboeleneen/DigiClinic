from django.urls import path

from . import views

app_name = "organizations"

urlpatterns = [
    path("settings/", views.settings_company, name="settings"),
    path("logo/", views.logo, name="logo"),
]
