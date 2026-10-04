"""Sending quotations: template variables, the self-review gate, deliveries and dispatch."""

import logging
import re
import threading
import time
from dataclasses import dataclass

from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.audit import services as audit
from apps.core.phones import mask
from apps.documents import pdf as pdfdoc
from apps.quotations.models import QuotationStatus

from . import whatsapp
from .models import (
    SUCCESS_STATUSES,
    ChannelKind,
    ChannelStatus,
    Delivery,
    DeliveryStatus,
    QuotationReview,
    SendingChannelConfig,
)
from .providers import ProviderResult, SmtpEmailProvider

logger = logging.getLogger(__name__)

SENDABLE_STATUSES = {
    QuotationStatus.FINALIZED,
    QuotationStatus.SENT,
    QuotationStatus.ACCEPTED,
    QuotationStatus.REJECTED,
}
VARIABLES = (
    "contact_name",
    "customer_name",
    "quote_number",
    "valid_until",
    "items_count",
    "sender_name",
    "company_name",
)
_VAR_RE = re.compile(r"\{(\w+)\}")

DEFAULT_EMAIL_SUBJECT = "عرض أسعار رقم {quote_number} — {company_name}"
DEFAULT_EMAIL_BODY = (
    "السادة / {customer_name}\n"
    "عناية أ/ {contact_name}\n\n"
    "تحية طيبة وبعد،\n"
    "مرفق لسيادتكم عرض أسعار رقم {quote_number} ({items_count} صنف)، ساري حتى {valid_until}.\n"
    "يسعدنا الرد على أي استفسار.\n\n"
    "مع خالص التحية،\n"
    "{sender_name}\n"
    "{company_name}"
)
DEFAULT_WHATSAPP = (
    "أهلًا أ/ {contact_name}،\n"
    "مرفق عرض أسعار رقم {quote_number} من {company_name}، ساري حتى {valid_until}.\n"
    "لأي استفسار أنا متاح.\n"
    "{sender_name}"
)


class SendError(Exception):
    pass


# --- templates -----------------------------------------------------------------------------


def variables_for(q, contact, sender) -> dict:
    return {
        "contact_name": " ".join(x for x in (contact.salutation, contact.name) if x) if contact else "",
        "customer_name": q.customer_snapshot.get("name") or (q.customer.name if q.customer_id else ""),
        "quote_number": q.display_number,
        "valid_until": q.valid_until.strftime("%Y/%m/%d"),
        "items_count": str(q.items_count),
        "sender_name": sender.full_name if sender else "",
        "company_name": q.organization.name_ar,
    }


def render_template(text: str, variables: dict) -> str:
    """Replace {known_variable}; leave anything else (including stray braces) untouched."""
    return _VAR_RE.sub(lambda m: variables.get(m.group(1), m.group(0)), text or "")


def email_templates(org):
    s = getattr(org, "settings", None)
    subject = (s.default_email_subject if s else "") or DEFAULT_EMAIL_SUBJECT
    body = (s.default_email_body if s else "") or DEFAULT_EMAIL_BODY
    return subject, body


# --- review gate ---------------------------------------------------------------------------


def record_review(q, user):
    QuotationReview.objects.update_or_create(organization_id=q.organization_id, quotation=q, user=user, defaults={})
    q.reviewed_by, q.reviewed_at = user, timezone.now()
    q.save(update_fields=["reviewed_by", "reviewed_at", "updated_at"])


def has_reviewed(q, user) -> bool:
    return QuotationReview.objects.filter(quotation=q, user=user).exists()


# --- channels ------------------------------------------------------------------------------


def email_provider(org):
    cfg = SendingChannelConfig.objects.for_org(org).filter(kind=ChannelKind.EMAIL, is_active=True).first()
    if not cfg or not cfg.config.get("host"):
        return None, cfg
    return SmtpEmailProvider(cfg.config, cfg.display_name or org.name_ar), cfg


def email_ready(org) -> bool:
    return email_provider(org)[0] is not None


def whatsapp_ready(org) -> bool:
    cfg = SendingChannelConfig.objects.for_org(org).filter(kind=ChannelKind.WHATSAPP, is_active=True).first()
    return bool(cfg) and whatsapp.status(org).get("state") == "ready"


def whatsapp_templates(org):
    s = getattr(org, "settings", None)
    return (s.default_whatsapp_message if s else "") or DEFAULT_WHATSAPP


# --- sending -------------------------------------------------------------------------------


@dataclass
class Recipient:
    channel: str
    value: str
    name: str
    contact_channel_id: int | None = None


def create_deliveries(
    q, user, recipients: list[Recipient], *, subject: str, body: str, confirmed: bool, wa_body: str = ""
):
    """Validate the gate and create queued deliveries. Dispatch happens after commit."""
    if q.status not in SENDABLE_STATUSES:
        raise SendError(_("العرض ده مينفعش يتبعت في حالته الحالية."))
    if not confirmed:
        raise SendError(_("لازم تعلّم على «راجعت الأصناف والأسعار بنفسي»."))
    if not has_reviewed(q, user):
        raise SendError(_("لازم تفتح الـPDF النهائي وتراجعه بنفسك قبل الإرسال."))
    if not recipients:
        raise SendError(_("اختار مستلم واحد على الأقل."))
    if any(r.channel == ChannelKind.EMAIL for r in recipients) and not email_ready(q.organization):
        raise SendError(_("إعدادات الإيميل مش متظبطة. كلّم مدير النظام."))
    if any(r.channel == ChannelKind.WHATSAPP for r in recipients) and not whatsapp_ready(q.organization):
        raise SendError(_("الواتساب مش متصل. اربطه من الإعدادات، أو ابعت بالإيميل."))

    deliveries = []
    with transaction.atomic():
        for r in recipients:
            deliveries.append(
                Delivery.objects.create(
                    organization_id=q.organization_id,
                    quotation=q,
                    channel=r.channel,
                    recipient=r.value,
                    recipient_name=r.name,
                    contact_channel_id=r.contact_channel_id,
                    subject=subject if r.channel == ChannelKind.EMAIL else "",
                    message_text=body if r.channel == ChannelKind.EMAIL else (wa_body or body),
                    sent_by=user,
                    review_confirmed=True,
                )
            )
    _schedule([d.pk for d in deliveries])
    return deliveries


def _schedule(ids):
    if getattr(settings, "MESSAGING_SYNC", False):
        dispatch(ids)
    else:
        transaction.on_commit(lambda: dispatch(ids))


def dispatch(delivery_ids):
    """Local dev: a background thread. Production: Celery (Phase 10)."""
    if getattr(settings, "MESSAGING_SYNC", False):
        for pk in delivery_ids:
            process_delivery(pk)
        return
    threading.Thread(target=_run_in_thread, args=(list(delivery_ids),), daemon=True).start()


def _run_in_thread(ids):
    try:
        for pk in ids:
            process_delivery(pk)
    finally:
        close_old_connections()


def process_delivery(delivery_id: int):
    d = Delivery.objects.select_related("quotation__organization__settings", "quotation__customer", "sent_by").get(
        pk=delivery_id
    )
    if d.status not in (DeliveryStatus.QUEUED, DeliveryStatus.FAILED):
        return d
    q = d.quotation
    delays = getattr(settings, "MESSAGING_RETRY_DELAYS", (60, 300, 900))
    for attempt in range(len(delays) + 1):
        d.status, d.attempts = DeliveryStatus.SENDING, d.attempts + 1
        d.save(update_fields=["status", "attempts", "updated_at"])
        result = _send_once(d, q)
        if result.ok:
            now = timezone.now()
            d.status, d.sent_at, d.error_message = DeliveryStatus.SENT, now, ""
            d.provider_message_id = result.provider_message_id[:250]
            d.save(update_fields=["status", "sent_at", "error_message", "provider_message_id", "updated_at"])
            _mark_quote_sent(q, now)
            audit.log(
                "delivery.sent",
                org=q.organization,
                actor=d.sent_by,
                target=q,
                summary=f"{q.display_number} → {d.recipient_name} ({d.get_channel_display()}: {d.recipient})",
            )
            logger.info("Delivery %s sent via %s to %s", d.pk, d.channel, _masked(d))
            return d
        d.status, d.error_message = DeliveryStatus.FAILED, result.error
        d.save(update_fields=["status", "error_message", "updated_at"])
        logger.warning("Delivery %s failed (attempt %s): %s", d.pk, d.attempts, result.error)
        if not result.temporary or attempt >= len(delays):
            audit.log(
                "delivery.failed",
                org=q.organization,
                actor=d.sent_by,
                target=q,
                summary=f"{q.display_number} → {d.recipient_name} ({d.get_channel_display()}): {result.error}"[:300],
            )
            return d
        time.sleep(delays[attempt])
    return d


def _pdf_bytes(q):
    if not q.pdf_file or not q.pdf_file.storage.exists(q.pdf_file.name):
        pdfdoc.generate_final_pdf(q)
    with q.pdf_file.open("rb") as fh:
        return fh.read()


_wa_lock = threading.Lock()


def _wait_for_whatsapp_slot(org):
    """At most one WhatsApp message every WA_RATE_LIMIT_SECONDS from the same number (anti-ban)."""
    gap = getattr(settings, "WA_RATE_LIMIT_SECONDS", 20)
    last = (
        Delivery.objects.filter(organization=org, channel=ChannelKind.WHATSAPP, sent_at__isnull=False)
        .order_by("-sent_at")
        .values_list("sent_at", flat=True)
        .first()
    )
    if last and gap:
        wait = gap - (timezone.now() - last).total_seconds()
        if wait > 0:
            time.sleep(wait)


def _send_once(d, q):
    try:
        return _send_channel(d, q)
    except pdfdoc.PdfRenderError as e:
        return ProviderResult(ok=False, error=str(e))


def _send_channel(d, q):
    if d.channel == ChannelKind.EMAIL:
        provider, cfg = email_provider(q.organization)
        if provider is None:
            return ProviderResult(ok=False, error=_("إعدادات الإيميل مش متظبطة."))
        result = provider.send(d, _pdf_bytes(q), pdfdoc.download_name(q))
        _update_channel_status(cfg, result)
        return result
    if d.channel == ChannelKind.WHATSAPP:
        data = _pdf_bytes(q)
        with _wa_lock:
            _wait_for_whatsapp_slot(q.organization)
            result = whatsapp.WhatsAppProvider(q.organization).send(d, data, pdfdoc.download_name(q))
            if result.ok:  # stamp now so the next message in this process waits its turn
                Delivery.objects.filter(pk=d.pk).update(sent_at=timezone.now())
        return result
    return ProviderResult(ok=False, error=_("القناة دي مش متاحة."))


def _update_channel_status(cfg, result):
    if cfg is None:
        return
    cfg.status = ChannelStatus.CONNECTED if result.ok else ChannelStatus.ERROR
    cfg.last_error = "" if result.ok else result.error
    cfg.last_checked_at = timezone.now()
    cfg.save(update_fields=["status", "last_error", "last_checked_at", "updated_at"])


def _mark_quote_sent(q, when):
    type(q).objects.filter(pk=q.pk, status=QuotationStatus.FINALIZED).update(status=QuotationStatus.SENT)
    type(q).objects.filter(pk=q.pk, sent_at__isnull=True).update(sent_at=when)


def _masked(d):
    if d.channel == ChannelKind.EMAIL:
        local, _sep, domain = d.recipient.partition("@")
        return f"{local[:2]}***@{domain}"
    return mask(d.recipient)


def retry(delivery, user):
    if delivery.status != DeliveryStatus.FAILED:
        raise SendError(_("الإرسال ده مش فاشل."))
    if not has_reviewed(delivery.quotation, user):
        raise SendError(_("لازم تفتح الـPDF النهائي وتراجعه بنفسك قبل الإرسال."))
    delivery.sent_by = user
    delivery.save(update_fields=["sent_by", "updated_at"])
    _schedule([delivery.pk])


def fallback_to_email(delivery, user):
    """A failed WhatsApp message → send the same quotation by email to the same contact."""
    from apps.customers.models import ChannelType, ContactChannel

    if delivery.channel != ChannelKind.WHATSAPP or delivery.status != DeliveryStatus.FAILED:
        raise SendError(_("ده مش واتساب فاشل."))
    contact_id = delivery.contact_channel.contact_id if delivery.contact_channel_id else None
    email = (
        ContactChannel.objects.filter(contact_id=contact_id, type=ChannelType.EMAIL).order_by("-is_primary").first()
        if contact_id
        else None
    )
    if email is None:
        raise SendError(_("المسؤول ده ملوش إيميل."))
    q = delivery.quotation
    subject_tpl, body_tpl = email_templates(q.organization)
    variables = variables_for(q, email.contact, user)
    return create_deliveries(
        q,
        user,
        [Recipient(ChannelKind.EMAIL, email.value, delivery.recipient_name, email.pk)],
        subject=render_template(subject_tpl, variables),
        body=render_template(body_tpl, variables),
        confirmed=True,
    )


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


def any_success(q) -> bool:
    return q.deliveries.filter(status__in=SUCCESS_STATUSES).exists()
