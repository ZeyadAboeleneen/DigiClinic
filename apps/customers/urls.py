from django.urls import path

from . import views

app_name = "customers"

urlpatterns = [
    path("", views.customer_list, name="list"),
    path("new/", views.customer_create, name="create"),
    path("<int:pk>/", views.customer_detail, name="detail"),
    path("<int:pk>/edit/", views.customer_edit, name="edit"),
    path("<int:pk>/toggle/", views.customer_toggle, name="toggle"),
    path("<int:pk>/delete/", views.customer_delete, name="delete"),
    path("<int:pk>/history/", views.customer_history, name="history"),
    path("<int:customer_pk>/contacts/", views.contacts_partial, name="contacts"),
    path("<int:customer_pk>/contacts/new/", views.contact_create, name="contact_create"),
    path("contacts/<int:pk>/edit/", views.contact_edit, name="contact_edit"),
    path("contacts/<int:pk>/toggle/", views.contact_toggle, name="contact_toggle"),
    path("contacts/<int:pk>/delete/", views.contact_delete, name="contact_delete"),
    path("contacts/<int:contact_pk>/channels/new/", views.channel_add, name="channel_add"),
    path("channels/<int:pk>/delete/", views.channel_delete, name="channel_delete"),
]
