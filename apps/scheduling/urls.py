from django.urls import path

from . import views

app_name = "scheduling"

urlpatterns = [
    path("booking/", views.booking_home, name="booking_home"),
    path("booking/search/", views.booking_search, name="booking_search"),
    path("booking/new-patient/", views.booking_new_patient, name="booking_new_patient"),
    path("booking/<int:patient_pk>/", views.booking_patient, name="booking_patient"),
    path("booking/<int:patient_pk>/slots/", views.booking_slots, name="booking_slots"),
    path("booking/<int:patient_pk>/confirm/", views.booking_confirm, name="booking_confirm"),
    path("appointments/", views.appointments_day, name="appointments_day"),
    path("appointments/<int:pk>/confirm/", views.appointment_confirm, name="appointment_confirm"),
    path("appointments/<int:pk>/cancel/", views.appointment_cancel, name="appointment_cancel"),
    path("appointments/affected/", views.affected_appointments, name="affected"),
    path("appointments/affected/<int:pk>/reschedule/", views.affected_reschedule, name="affected_reschedule"),
]
