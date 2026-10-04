from django.urls import path

from . import views

app_name = "clinical"

urlpatterns = [
    path("desk/", views.desk, name="desk"),
    path("desk/queue/", views.desk_queue, name="queue"),
    path("desk/search/", views.desk_search, name="search"),
    path("desk/lock/", views.desk_lock, name="lock"),
    path("desk/unlock/", views.desk_unlock, name="unlock"),
    path("desk/call/<int:appt_pk>/", views.desk_call, name="call"),
    path("desk/visits/<int:pk>/", views.visit_view, name="visit"),
    path("desk/visits/<int:pk>/field/", views.visit_field, name="visit_field"),
    path("desk/visits/<int:pk>/finish/", views.visit_finish, name="visit_finish"),
    path("desk/patients/<int:pk>/", views.patient_record, name="record"),
    path("desk/patients/<int:pk>/history/", views.patient_history, name="history"),
    path("desk/patients/<int:pk>/attachments/", views.patient_attachments, name="attachments"),
    path("desk/patients/<int:pk>/data/", views.patient_data, name="data"),
    path("patients/<int:pk>/attachments/upload/", views.attachment_upload, name="attachment_upload"),
    path("attachments/<int:pk>/", views.attachment_file, name="attachment_file"),
]
