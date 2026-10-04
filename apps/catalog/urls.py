from django.urls import path

from . import views

app_name = "catalog"

urlpatterns = [
    path("", views.catalog, name="index"),
    path("reorder/", views.reorder, name="reorder"),
    path("groups/new/", views.group_create, name="group_create"),
    path("groups/<int:pk>/edit/", views.group_edit, name="group_edit"),
    path("groups/<int:pk>/toggle/", views.group_toggle, name="group_toggle"),
    path("groups/<int:group_pk>/categories/new/", views.category_create, name="category_create"),
    path("categories/<int:pk>/edit/", views.category_edit, name="category_edit"),
    path("categories/<int:pk>/toggle/", views.category_toggle, name="category_toggle"),
    path("categories/<int:category_pk>/products/new/", views.product_create, name="product_create"),
    path("<int:pk>/", views.product_row, name="product_row"),
    path("<int:pk>/edit/", views.product_edit, name="product_edit"),
    path("<int:pk>/toggle/", views.product_toggle, name="product_toggle"),
    path("<int:pk>/delete/", views.product_delete, name="product_delete"),
    path("categories/<int:pk>/delete/", views.category_delete, name="category_delete"),
    path("groups/<int:pk>/delete/", views.group_delete, name="group_delete"),
]
