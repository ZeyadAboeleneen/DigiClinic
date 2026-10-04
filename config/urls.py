from django.contrib import admin
from django.urls import include, path

from apps.core.health import healthz

urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("", include("apps.dashboard.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("org/", include("apps.organizations.urls")),
    path("", include("apps.messaging.urls")),
    path("", include("apps.doctors.urls")),
    path("", include("apps.patients.urls")),
    path("", include("apps.scheduling.urls")),
    path("", include("apps.notifications.urls")),
    path("", include("apps.reception.urls")),
    path("", include("apps.billing.urls")),
    path("", include("apps.clinical.urls")),
    path("", include("apps.prescriptions.urls")),
    path("activity/", include("apps.audit.urls")),
    path("django-admin/", admin.site.urls),
]

handler403 = "apps.core.views.permission_denied"
