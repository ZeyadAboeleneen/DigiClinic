from django.shortcuts import render

from apps.accounts.permissions import require_membership
from apps.messaging import whatsapp
from apps.messaging.models import ChannelKind, SendingChannelConfig
from apps.notifications import services as notifications
from apps.notifications.models import MessageStatus, ScheduledMessage


def _wa_state(org):
    cfg = SendingChannelConfig.objects.filter(organization=org, kind=ChannelKind.WHATSAPP, is_active=True).first()
    if cfg is None:
        return {"state": "off", "label": "لسه متربطش"}
    st = whatsapp.status(org).get("state")
    return {"state": st, "label": whatsapp.STATE_LABELS.get(st, st), "number": cfg.sender_identity}


@require_membership
def home(request):
    org = request.organization
    ctx = {
        "wa_state": _wa_state(org),
        "scheduler_alive": notifications.scheduler_alive(),
        "failed_messages": ScheduledMessage.objects.for_org(org).filter(status=MessageStatus.FAILED).count(),
    }
    return render(request, "dashboard/home.html", ctx)
