from django.urls import path

from . import views

app_name = "reception"

urlpatterns = [
    path("reception/", views.today, name="today"),
    path("reception/rows/", views.rows, name="rows"),
    path("reception/walk-in/", views.walk_in, name="walk_in"),
    path("reception/followups/<int:visit_pk>/dismiss/", views.followup_dismiss, name="followup_dismiss"),
    path("reception/<int:pk>/arrive/", views.arrive, name="arrive"),
    path("reception/<int:pk>/vitals/", views.vitals, name="vitals"),
    path("reception/<int:pk>/payment/", views.payment, name="payment"),
    path("reception/<int:pk>/<str:action>/", views.step, name="step"),
]
