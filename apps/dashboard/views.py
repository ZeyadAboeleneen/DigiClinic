from django.shortcuts import render

from apps.accounts.permissions import require_membership
from apps.messaging import whatsapp
from apps.messaging.models import ChannelKind, SendingChannelConfig


def _wa_state(org):
    cfg = SendingChannelConfig.objects.filter(organization=org, kind=ChannelKind.WHATSAPP, is_active=True).first()
    if cfg is None:
        return {"state": "off", "label": "لسه متربطش"}
    st = whatsapp.status(org).get("state")
    return {"state": st, "label": whatsapp.STATE_LABELS.get(st, st), "number": cfg.sender_identity}


@require_membership
def home(request):
    ctx = {"wa_state": _wa_state(request.organization)}
    return render(request, "dashboard/home.html", ctx)
