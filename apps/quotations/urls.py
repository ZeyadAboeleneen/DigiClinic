from django.urls import path

from . import views

app_name = "quotations"

urlpatterns = [
    path("", views.quotation_list, name="list"),
    path("new/", views.quotation_new, name="new"),
    path("<int:pk>/", views.quotation_detail, name="detail"),
    path("<int:pk>/edit/", views.quotation_edit, name="edit"),
    path("<int:pk>/picker/", views.picker, name="picker"),
    path("<int:pk>/customers/", views.customer_search, name="customer_search"),
    path("<int:pk>/customer/", views.set_customer, name="set_customer"),
    path("<int:pk>/contact/", views.set_contact, name="set_contact"),
    path("<int:pk>/quick-contact/", views.quick_contact, name="quick_contact"),
    path("<int:pk>/quick-customer/", views.quick_customer, name="quick_customer"),
    path("<int:pk>/copy-last/", views.copy_last, name="copy_last"),
    path("<int:pk>/items/add/", views.add_item, name="add_item"),
    path("<int:pk>/items/add-category/", views.add_category, name="add_category"),
    path("<int:pk>/items/add-custom/", views.add_custom, name="add_custom"),
    path("<int:pk>/items/clear/", views.clear_items, name="clear_items"),
    path("<int:pk>/items/<int:item_pk>/", views.update_item, name="update_item"),
    path("<int:pk>/items/<int:item_pk>/delete/", views.delete_item, name="delete_item"),
    path("<int:pk>/summary/", views.summary, name="summary"),
    path("<int:pk>/meta/", views.update_meta, name="meta"),
    path("<int:pk>/finalize/", views.finalize, name="finalize"),
    path("<int:pk>/preview.pdf", views.preview_pdf, name="preview"),
    path("<int:pk>/pdf/", views.final_pdf, name="pdf"),
    path("<int:pk>/delete/", views.delete_draft, name="delete"),
    path("<int:pk>/revise/", views.revise, name="revise"),
    path("<int:pk>/reopen/", views.reopen, name="reopen"),
    path("<int:pk>/duplicate/", views.duplicate, name="duplicate"),
    path("<int:pk>/status/", views.set_status, name="status"),
]
