"""Scheduling messages into the outbox (06 §6.1). Nothing here sends — `dispatcher.py` does, after re-checking
freshness. Every function is idempotent thanks to `ScheduledMessage.dedupe_key`."""

import uuid
from datetime import datetime, timedelta

from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.timeutils import CAIRO
from apps.patients.models import PreferredChannel
from apps.scheduling.models import BookingSource

from .models import (
    DefaultChannel,
    Event,
    MessageChannel,
    MessageStatus,
    NotificationSettings,
    NotificationTemplate,
    QuietPolicy,
    ScheduledMessage,
    SchedulerHeartbeat,
    TemplateChannel,
)

URGENT_EVENTS = {Event.RESCHEDULED, Event.CANCELLED}
HEARTBEAT_NAME = "run_scheduler"
HEARTBEAT_STALE_AFTER = timedelta(minutes=2)


# --- channels & recipients -------------------------------------------------------------------


def channels_for(template, patient, ns) -> list[str]:
    choice = template.channel if template and template.channel != TemplateChannel.DEFAULT else ns.default_channel
    if choice == DefaultChannel.PREFERRED:
        if patient.preferred_channel == PreferredChannel.NONE:
            return []
        choice = patient.preferred_channel
    if choice == DefaultChannel.BOTH:
        return [MessageChannel.WHATSAPP, MessageChannel.EMAIL]
    return [choice]


def recipient_for(patient, channel) -> str:
    if channel == MessageChannel.WHATSAPP:
        return patient.whatsapp or patient.phone or ""
    return patient.email or ""


# --- quiet hours (06 §6.5) -------------------------------------------------------------------


def in_quiet_hours(dt: datetime, ns) -> bool:
    t = dt.astimezone(CAIRO).time()
    start, end = ns.quiet_start, ns.quiet_end
    if start == end:
        return False
    if start < end:
        return start <= t < end
    return t >= start or t < end  # window wraps midnight (22:00 → 09:00)


def quiet_end_after(dt: datetime, ns) -> datetime:
    local = dt.astimezone(CAIRO)
    candidate = datetime.combine(local.date(), ns.quiet_end, tzinfo=CAIRO)
    if candidate <= local:
        candidate = datetime.combine(local.date() + timedelta(days=1), ns.quiet_end, tzinfo=CAIRO)
    return candidate


def is_urgent(event, appt, dt: datetime, ns) -> bool:
    """Rescheduled/cancelled messages about today/tomorrow go out in quiet hours, but only before `urgent_until`."""
    if event not in URGENT_EVENTS or appt is None:
        return False
    local = dt.astimezone(CAIRO)
    soon = appt.date <= local.date() + timedelta(days=1)
    # urgent_until is an evening cut-off; the early-morning part of the quiet window is never "urgent".
    return soon and ns.quiet_start <= local.time() < ns.urgent_until


def apply_quiet(send_at: datetime, ns, *, event=None, appt=None) -> tuple[datetime, str]:
    """Returns (send_at, skip_reason). An empty reason means send at the returned time."""
    if not in_quiet_hours(send_at, ns) or is_urgent(event, appt, send_at, ns):
        return send_at, ""
    if ns.quiet_policy == QuietPolicy.SKIP:
        return send_at, _("ساعات الهدوء")
    return quiet_end_after(send_at, ns), ""


# --- enqueueing ------------------------------------------------------------------------------


def _blocking_reason(ns, template, patient, recipient) -> str:
    if not ns.is_enabled:
        return _("الرسايل التلقائية مقفولة")
    if template is not None and not template.is_enabled:
        return _("القالب مقفول")
    if not patient.messaging_consent:
        return _("مفيش موافقة")
    if not recipient:
        return _("مفيش رقم/إيميل للقناة دي")
    return ""


def enqueue(*, appt, template, event, send_at, context=None, by=None, ns=None, key_extra=""):
    """Create one outbox row per channel (or one `skipped` row explaining why nothing will be sent)."""
    ns = ns or NotificationSettings.for_org(appt.organization)
    patient = appt.patient
    base_key = f"{appt.pk}:{event}:{template.pk if template else 0}:{appt.version}"
    if key_extra:
        base_key += f":{key_extra}"
    channels = channels_for(template, patient, ns)
    if not channels:
        channels, forced_reason = [MessageChannel.WHATSAPP], _("المريض مختار من غير رسايل")
    else:
        forced_reason = ""

    send_at, quiet_reason = apply_quiet(send_at, ns, event=event, appt=appt)
    lead = template.min_lead_minutes if template else 0
    if not quiet_reason and event == Event.REMINDER and appt.start_at - send_at < timedelta(minutes=lead):
        quiet_reason = _("فات وقتها")  # deferred past quiet hours into the "too late" zone

    rows = []
    for channel in channels:
        recipient = recipient_for(patient, channel)
        reason = forced_reason or _blocking_reason(ns, template, patient, recipient) or quiet_reason
        row, _created = ScheduledMessage.objects.get_or_create(
            dedupe_key=f"{base_key}:{channel}",
            defaults={
                "organization": appt.organization,
                "patient": patient,
                "appointment": appt,
                "template": template,
                "event": event,
                "channel": channel,
                "recipient": recipient,
                "send_at": send_at,
                "next_attempt_at": send_at,
                "appointment_version": appt.version,
                "status": MessageStatus.SKIPPED if reason else MessageStatus.PENDING,
                "status_reason": reason,
                "context": context or {},
                "created_by": by,
            },
        )
        rows.append(row)
    return rows


def _templates(org, event, booking_mode):
    return [
        t
        for t in NotificationTemplate.objects.filter(organization=org, event=event).order_by("order", "id")
        if t.applies_to(booking_mode)
    ]


def _first_template(org, event, booking_mode):
    tpls = _templates(org, event, booking_mode)
    return next((t for t in tpls if t.is_enabled), tpls[0] if tpls else None)


def schedule_reminders(appt, *, by=None, ns=None, now=None):
    now = now or timezone.now()
    rows = []
    for tpl in _templates(appt.organization, Event.REMINDER, appt.doctor.booking_mode):
        if not tpl.is_enabled or tpl.offset_minutes is None:
            continue
        send_at = appt.start_at - timedelta(minutes=tpl.offset_minutes)
        # Already too late for this reminder → don't create it at all (06 §6.1).
        if send_at < now or appt.start_at - now < timedelta(minutes=tpl.min_lead_minutes):
            continue
        rows += enqueue(appt=appt, template=tpl, event=Event.REMINDER, send_at=send_at, by=by, ns=ns)
    return rows


def schedule_for(appt, *, by=None, now=None):
    """After a booking commits: instant confirmation (not for walk-ins) + every applicable reminder."""
    now = now or timezone.now()
    ns = NotificationSettings.for_org(appt.organization)
    rows = []
    if appt.source != BookingSource.WALK_IN:
        tpl = _first_template(appt.organization, Event.BOOKING_CONFIRMED, appt.doctor.booking_mode)
        if tpl:
            rows += enqueue(appt=appt, template=tpl, event=Event.BOOKING_CONFIRMED, send_at=now, by=by, ns=ns)
    rows += schedule_reminders(appt, by=by, ns=ns, now=now)
    return rows


def cancel_pending(appt, reason):
    return ScheduledMessage.objects.filter(appointment=appt, status=MessageStatus.PENDING).update(
        status=MessageStatus.CANCELLED, status_reason=reason, updated_at=timezone.now()
    )


def on_rescheduled(old, new, *, by=None, now=None):
    from .templating import fmt_date, fmt_time

    now = now or timezone.now()
    cancel_pending(old, _("اتعدل الميعاد"))
    ns = NotificationSettings.for_org(new.organization)
    rows = []
    tpl = _first_template(new.organization, Event.RESCHEDULED, new.doctor.booking_mode)
    if tpl:
        ctx = {"old_date": fmt_date(old.date), "old_time": fmt_time(old.start_at)}
        rows += enqueue(appt=new, template=tpl, event=Event.RESCHEDULED, send_at=now, context=ctx, by=by, ns=ns)
    rows += schedule_reminders(new, by=by, ns=ns, now=now)
    return rows


def on_cancelled(appt, *, by=None, now=None):
    now = now or timezone.now()
    cancel_pending(appt, _("الحجز اتلغى"))
    tpl = _first_template(appt.organization, Event.CANCELLED, appt.doctor.booking_mode)
    if not tpl:
        return []
    return enqueue(appt=appt, template=tpl, event=Event.CANCELLED, send_at=now, by=by)


def create_test_message(org, *, channel, recipient, body, subject="", by=None):
    """A one-off test row (templates screen "ابعت تجربة"); the dispatcher sends it right away."""
    now = timezone.now()
    return ScheduledMessage.objects.create(
        organization=org,
        event=Event.TEST,
        channel=channel,
        recipient=recipient,
        send_at=now,
        next_attempt_at=now,
        context={"body": body, "subject": subject},
        dedupe_key=f"test:{uuid.uuid4().hex}",
        created_by=by,
    )


def retry(row, *, by=None):
    row.status = MessageStatus.PENDING
    row.status_reason = ""
    row.attempts = 0
    row.next_attempt_at = timezone.now()
    if row.send_at > row.next_attempt_at:
        row.next_attempt_at = row.send_at
    row.save(update_fields=["status", "status_reason", "attempts", "next_attempt_at", "updated_at"])


def cancel_message(row, *, by=None):
    if row.status != MessageStatus.PENDING:
        return False
    row.status = MessageStatus.CANCELLED
    row.status_reason = _("اتلغت يدويًا")
    row.save(update_fields=["status", "status_reason", "updated_at"])
    return True


# --- inbound (06 §6.7: stored only, never acted on in v1) -----------------------------------


def record_inbound(org_id, *, provider_message_id, from_number, body):
    from django.db.models import Q

    from apps.patients.models import Patient

    from .models import InboundMessage

    e164 = "+" + "".join(ch for ch in str(from_number) if ch.isdigit())
    patient = (
        Patient.objects.filter(organization_id=org_id, is_active=True)
        .filter(Q(whatsapp=e164) | Q(phone=e164))
        .order_by("id")
        .first()
    )
    msg, _created = InboundMessage.objects.get_or_create(
        provider_message_id=provider_message_id,
        defaults={"organization_id": org_id, "patient": patient, "from_number": e164, "body": body[:4000]},
    )
    return msg


# --- scheduler health ------------------------------------------------------------------------


def last_heartbeat():
    return SchedulerHeartbeat.objects.filter(name=HEARTBEAT_NAME).first()


def scheduler_alive(now=None) -> bool:
    hb = last_heartbeat()
    return bool(hb) and (now or timezone.now()) - hb.beat_at < HEARTBEAT_STALE_AFTER
