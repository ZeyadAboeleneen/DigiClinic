from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include("apps.dashboard.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("org/", include("apps.organizations.urls")),
    path("customers/", include("apps.customers.urls")),
    path("products/", include("apps.catalog.urls")),
    path("", include("apps.messaging.urls")),
    path("quotations/", include("apps.quotations.urls")),
    path("activity/", include("apps.audit.urls")),
    path("django-admin/", admin.site.urls),
]

handler403 = "apps.core.views.permission_denied"
