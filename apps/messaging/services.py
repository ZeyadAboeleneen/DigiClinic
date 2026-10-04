"""Channel configuration (email/WhatsApp) and gateway webhook handling.

The actual send pipeline (create a Delivery, dispatch it, retries) is rebuilt in Phase 4 around
`notifications.ScheduledMessage` — this module only keeps the infrastructure every phase needs:
provider lookup, channel status tracking and applying gateway acks/webhooks.
"""

import logging

from django.utils import timezone

from .models import ChannelKind, ChannelStatus, Delivery, DeliveryStatus, SendingChannelConfig
from .providers import SmtpEmailProvider

logger = logging.getLogger(__name__)


def email_provider(org):
    cfg = SendingChannelConfig.objects.for_org(org).filter(kind=ChannelKind.EMAIL, is_active=True).first()
    if not cfg or not cfg.config.get("host"):
        return None, cfg
    return SmtpEmailProvider(cfg.config, cfg.display_name or org.name_ar), cfg


def email_ready(org) -> bool:
    return email_provider(org)[0] is not None


def whatsapp_ready(org) -> bool:
    from . import whatsapp

    cfg = SendingChannelConfig.objects.for_org(org).filter(kind=ChannelKind.WHATSAPP, is_active=True).first()
    return bool(cfg) and whatsapp.status(org).get("state") == "ready"


def _update_channel_status(cfg, result):
    if cfg is None:
        return
    cfg.status = ChannelStatus.CONNECTED if result.ok else ChannelStatus.ERROR
    cfg.last_error = "" if result.ok else result.error
    cfg.last_checked_at = timezone.now()
    cfg.save(update_fields=["status", "last_error", "last_checked_at", "updated_at"])


# --- gateway webhooks ----------------------------------------------------------------------

ACK_STATUS = {2: DeliveryStatus.DELIVERED, 3: DeliveryStatus.READ, 4: DeliveryStatus.READ}
_STATUS_RANK = {DeliveryStatus.SENT: 1, DeliveryStatus.DELIVERED: 2, DeliveryStatus.READ: 3}


def apply_ack(message_id: str, ack: int):
    new = ACK_STATUS.get(ack)
    if not new or not message_id:
        return 0
    updated = 0
    for d in Delivery.objects.filter(channel=ChannelKind.WHATSAPP, provider_message_id=message_id):
        if _STATUS_RANK.get(d.status, 0) >= _STATUS_RANK[new]:
            continue
        d.status = new
        now = timezone.now()
        if new == DeliveryStatus.DELIVERED:
            d.delivered_at = now
        else:
            d.read_at = now
            d.delivered_at = d.delivered_at or now
        d.save(update_fields=["status", "delivered_at", "read_at", "updated_at"])
        updated += 1
    return updated


def apply_gateway_status(org_id, state: str, me: str | None = None, error: str = ""):
    cfg = SendingChannelConfig.objects.filter(organization_id=org_id, kind=ChannelKind.WHATSAPP).first()
    if cfg is None:
        return
    cfg.status = {"ready": ChannelStatus.CONNECTED, "disconnected": ChannelStatus.DISCONNECTED}.get(
        state, ChannelStatus.ERROR
    )
    if me:
        cfg.sender_identity = "0" + me[2:] if me.startswith("20") else me
    cfg.last_error = error
    cfg.last_checked_at = timezone.now()
    cfg.save(update_fields=["status", "sender_identity", "last_error", "last_checked_at", "updated_at"])
