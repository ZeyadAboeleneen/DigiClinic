from datetime import date

from django.shortcuts import render

from apps.accounts.permissions import has_perm, require_membership, require_perm
from apps.messaging import whatsapp
from apps.messaging.models import ChannelKind, SendingChannelConfig
from apps.notifications import services as notifications
from apps.notifications.models import MessageStatus, ScheduledMessage

from . import services


def _wa_state(org):
    cfg = SendingChannelConfig.objects.filter(organization=org, kind=ChannelKind.WHATSAPP, is_active=True).first()
    if cfg is None:
        return {"state": "off", "label": "لسه متربطش"}
    st = whatsapp.status(org).get("state")
    return {"state": st, "label": whatsapp.STATE_LABELS.get(st, st), "number": cfg.sender_identity}


@require_membership
def home(request):
    """03 §3.9: reception sees today + tasks; doctor/owner also see the month's numbers."""
    org = request.organization
    m = request.membership
    today = services.today()
    ctx = {
        "wa_state": _wa_state(org),
        "scheduler_alive": notifications.scheduler_alive(),
        "today": today,
        "counts": services.day_counts(org, today),
    }
    if has_perm(m, "messages.view"):
        ctx["failed_messages"] = ScheduledMessage.objects.for_org(org).filter(status=MessageStatus.FAILED).count()
    if has_perm(m, "reception.operate"):
        from apps.clinical.services import followups_due

        ctx["followups"] = len(followups_due(org))
    if has_perm(m, "report.finance"):
        ctx["month"] = services.report(org, *services.month_bounds(today))
    return render(request, "dashboard/home.html", ctx)


def _parse(raw, default):
    try:
        return date.fromisoformat(raw) if raw else default
    except ValueError:
        return default


@require_perm("report.finance")
def reports(request):
    first, last = services.month_bounds(services.today())
    start = _parse(request.GET.get("from"), first)
    end = _parse(request.GET.get("to"), last)
    if end < start:
        start, end = end, start
    if (end - start).days > 366:
        start = end.replace(year=end.year - 1)
    return render(request, "dashboard/reports.html", {"r": services.report(request.organization, start, end)})
