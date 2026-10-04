from datetime import timedelta

from django.shortcuts import render
from django.utils import timezone

from apps.accounts.permissions import require_membership
from apps.messaging import whatsapp
from apps.messaging.models import ChannelKind, Delivery, DeliveryStatus, SendingChannelConfig
from apps.quotations.models import Quotation, QuotationStatus
from apps.quotations.services import expire_overdue


def _wa_state(org):
    cfg = SendingChannelConfig.objects.filter(organization=org, kind=ChannelKind.WHATSAPP, is_active=True).first()
    if cfg is None:
        return {"state": "off", "label": "لسه متربطش"}
    st = whatsapp.status(org).get("state")
    return {"state": st, "label": whatsapp.STATE_LABELS.get(st, st), "number": cfg.sender_identity}


@require_membership
def home(request):
    expire_overdue(request.organization)
    quotes = Quotation.objects.for_org(request.organization)
    today = timezone.localdate()
    month = quotes.filter(issue_date__year=today.year, issue_date__month=today.month).exclude(
        status__in=[QuotationStatus.DRAFT, QuotationStatus.CANCELLED]
    )
    ctx = {
        "my_drafts": quotes.filter(status=QuotationStatus.DRAFT, created_by=request.user)
        .select_related("customer")
        .order_by("-updated_at")[:5],
        "recent": quotes.issued().select_related("customer").order_by("-finalized_at")[:5],
        "expiring": quotes.filter(
            status__in=[QuotationStatus.FINALIZED, QuotationStatus.SENT],
            valid_until__gte=today,
            valid_until__lte=today + timedelta(days=7),
        )
        .select_related("customer")
        .order_by("valid_until")[:8],
        "month_count": month.count(),
        "month_accepted": month.filter(status=QuotationStatus.ACCEPTED).count(),
        "wa_state": _wa_state(request.organization),
        "failed": Delivery.objects.for_org(request.organization)
        .filter(status=DeliveryStatus.FAILED)
        .select_related("quotation")
        .order_by("-queued_at")[:5],
    }
    return render(request, "dashboard/home.html", ctx)
