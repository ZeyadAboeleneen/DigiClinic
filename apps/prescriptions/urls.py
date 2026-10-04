from django.urls import path

from . import views

app_name = "prescriptions"

urlpatterns = [
    path("desk/visits/<int:visit_pk>/rx/", views.builder, name="builder"),
    path("rx/<int:pk>/drugs/", views.drug_search, name="drug_search"),
    path("rx/<int:pk>/items/add/", views.item_add, name="item_add"),
    path("rx/<int:pk>/fields/", views.rx_fields, name="fields"),
    path("rx/<int:pk>/pdf/", views.rx_pdf, name="pdf"),
    path("rx/<int:pk>/dictate/", views.dictate, name="dictate"),
    path("rx/<int:pk>/dictate/add/", views.dictate_add, name="dictate_add"),
    path("rx/<int:pk>/<str:action>/", views.rx_action, name="action"),
    path("rx/items/<int:item_pk>/", views.item_update, name="item_update"),
    path("rx/items/<int:item_pk>/to-catalog/", views.item_to_catalog, name="item_to_catalog"),
    path("rx/items/<int:item_pk>/<str:action>/", views.item_action, name="item_action"),
    path("patients/<int:patient_pk>/prescriptions/", views.patient_prescriptions, name="patient_list"),
    path("org/settings/prescription/", views.settings_prescription, name="settings"),
    path("org/settings/prescription/calibration.pdf", views.calibration_pdf, name="calibration"),
    path("prescriptions/drugs/", views.drug_catalog, name="drugs"),
    path("prescriptions/drugs/<int:pk>/toggle/", views.drug_toggle, name="drug_toggle"),
    path("prescriptions/templates/", views.template_list, name="templates"),
    path("prescriptions/templates/<int:pk>/delete/", views.template_delete, name="template_delete"),
]
