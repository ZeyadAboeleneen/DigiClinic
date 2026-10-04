from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from django.utils.dateparse import parse_date

from apps.accounts.models import User
from apps.accounts.permissions import require_perm

from .models import ACTION_GROUPS, AuditEvent

PAGE_SIZE = 50


@require_perm("audit.view")
def activity(request):
    qs = AuditEvent.objects.for_org(request.organization).select_related("actor")
    f = {k: request.GET.get(k, "").strip() for k in ("group", "user", "q", "from", "to")}
    if f["group"] in ACTION_GROUPS:
        qs = qs.filter(action__startswith=f["group"] + ".")
    if f["user"].isdigit():
        qs = qs.filter(actor_id=f["user"])
    if f["q"]:
        qs = qs.filter(Q(summary__icontains=f["q"]) | Q(actor__full_name__icontains=f["q"]))
    if d := parse_date(f["from"]):
        qs = qs.filter(created_at__date__gte=d)
    if d := parse_date(f["to"]):
        qs = qs.filter(created_at__date__lte=d)
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
    ctx = {
        "page": page,
        "f": f,
        "groups": ACTION_GROUPS.items(),
        "users": User.objects.filter(memberships__organization=request.organization).order_by("full_name"),
    }
    if request.htmx and request.htmx.target == "activity-rows":
        return render(request, "audit/partials/rows.html", ctx)
    return render(request, "audit/activity.html", ctx)
