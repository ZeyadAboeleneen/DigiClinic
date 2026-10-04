from django.urls import path

from . import views

app_name = "patients"

urlpatterns = [
    path("patients/", views.patient_list, name="list"),
    path("patients/duplicates/", views.duplicates_check, name="duplicates_check"),
    path("patients/add/", views.patient_add, name="add"),
    path("patients/<int:pk>/", views.patient_detail, name="detail"),
    path("patients/<int:pk>/edit/", views.patient_edit, name="edit"),
    path("patients/<int:pk>/allergies/add/", views.allergy_add, name="allergy_add"),
    path("patients/<int:pk>/allergies/<int:allergy_pk>/delete/", views.allergy_delete, name="allergy_delete"),
    path("patients/<int:pk>/conditions/add/", views.condition_add, name="condition_add"),
    path("patients/<int:pk>/conditions/<int:condition_pk>/delete/", views.condition_delete, name="condition_delete"),
    path("patients/<int:pk>/merge/", views.merge_search, name="merge_search"),
    path("patients/<int:pk>/merge/<int:duplicate_pk>/", views.merge_confirm, name="merge_confirm"),
]
