from django.urls import path

from . import views

app_name = "doctors"

urlpatterns = [
    path("org/settings/doctor/", views.schedule_settings, name="schedule"),
    path("org/settings/doctor/periods/add/", views.period_add, name="period_add"),
    path("org/settings/doctor/periods/<int:pk>/delete/", views.period_delete, name="period_delete"),
    path("org/settings/doctor/exceptions/add/", views.exception_add, name="exception_add"),
    path("org/settings/doctor/exceptions/<int:pk>/delete/", views.exception_delete, name="exception_delete"),
    path("org/settings/visit-types/", views.visit_types_settings, name="visit_types"),
    path("org/settings/visit-types/add/", views.visit_type_add, name="visit_type_add"),
    path("org/settings/visit-types/<int:pk>/delete/", views.visit_type_delete, name="visit_type_delete"),
]
