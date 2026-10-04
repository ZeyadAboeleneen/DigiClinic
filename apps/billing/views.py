from datetime import date

from django.shortcuts import render
from django.utils import timezone

from apps.accounts.permissions import require_perm
from apps.core.timeutils import CAIRO

from . import services


@require_perm("payment.record")
def cashbox(request):
    try:
        day = date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        day = timezone.localtime(timezone.now(), CAIRO).date()
    return render(request, "billing/cashbox.html", {"box": services.cashbox(request.organization, day)})
