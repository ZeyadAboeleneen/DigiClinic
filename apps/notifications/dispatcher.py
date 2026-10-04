"""Sends due outbox rows (06 §6.2). Called by `manage.py run_scheduler` every tick; the only code that sends.

Before each send the row is re-validated (freshness guard), so catching up after the machine was off never
produces a "your appointment is in an hour" message for an appointment that already passed.
"""

import logging
import random
import time
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.phones import mask
from apps.messaging import services as messaging
from apps.messaging import whatsapp
from apps.messaging.models import ChannelKind, Delivery, DeliveryStatus, WhatsAppNumber
from apps.messaging.providers import ProviderResult
from apps.scheduling.models import AppointmentStatus

from . import templating
from .models import Event, MessageChannel, MessageStatus, NotificationSettings, ScheduledMessage
from .services import apply_quiet, recipient_for

logger = logging.getLogger(__name__)

BATCH_SIZE = 20
WA_NUMBER_CACHE_DAYS = 30
# Appointment statuses in which each event still makes sense; anything else → cancelled at send time.
VALID_STATUSES = {
    Event.BOOKING_CONFIRMED: {AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED},
    Event.REMINDER: {AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED},
    Event.RESCHEDULED: {AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED},
    Event.NEAR_TURN: {AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED},
    Event.CANCELLED: {AppointmentStatus.CANCELLED},
}


class Dispatcher:
    def __init__(self, *, sleep=time.sleep, monotonic=time.monotonic, jitter=lambda: random.uniform(0, 10)):  # noqa: S311 - anti-ban jitter, not crypto
        self.sleep = sleep
        self.monotonic = monotonic
        self.jitter = jitter
        self._last_wa = {}  # org_id → monotonic time of the last WhatsApp send from this process

    # --- main loop entry ---------------------------------------------------------------------

    def run_once(self, now=None) -> int:
        """Claim up to BATCH_SIZE due rows and process them. Returns how many were processed."""
        now = now or timezone.now()
        with transaction.atomic():
            rows = list(
                ScheduledMessage.objects.select_for_update(skip_locked=True)
                .filter(status=MessageStatus.PENDING, send_at__lte=now, next_attempt_at__lte=now)
                .order_by("send_at", "id")[:BATCH_SIZE]
            )
            ScheduledMessage.objects.filter(pk__in=[r.pk for r in rows]).update(status=MessageStatus.SENDING)
        for row in rows:
            row.status = MessageStatus.SENDING
            try:
                self.process(row, now=timezone.now())
            except Exception:  # never let one bad row stop the loop
                logger.exception("dispatch failed for scheduled message %s", row.pk)
                self._finish(row, MessageStatus.FAILED, _("خطأ داخلي — اتسجل في الـlogs"))
        return len(rows)

    # --- one row -----------------------------------------------------------------------------

    def process(self, row, *, now):
        ns = NotificationSettings.for_org(row.organization)
        if row.event != Event.TEST:
            verdict = self.freshness(row, ns, now)
            if verdict:
                status, reason = verdict
                if status == MessageStatus.PENDING:  # deferred out of quiet hours
                    return
                return self._finish(row, status, reason)

        self.render(row)
        result = self.send(row, ns)
        self._record_delivery(row, result)
        if result.ok:
            row.sent_at = timezone.now()
            return self._finish(row, MessageStatus.SENT, "")
        row.attempts += 1
        delays = settings.MESSAGING_RETRY_DELAYS
        if result.temporary and row.attempts <= len(delays) and row.event != Event.TEST:
            row.next_attempt_at = now + timedelta(seconds=delays[row.attempts - 1])
            return self._finish(row, MessageStatus.PENDING, result.error)
        self._finish(row, MessageStatus.FAILED, result.error)
        if ns.email_fallback and row.channel == MessageChannel.WHATSAPP:
            self._email_fallback(row, now)

    def freshness(self, row, ns, now):
        """None if the row should be sent now, else (new_status, reason). PENDING means "deferred, try later"."""
        appt = row.appointment
        if not ns.is_enabled:
            return MessageStatus.SKIPPED, _("الرسايل التلقائية مقفولة")
        if row.patient and not row.patient.messaging_consent:
            return MessageStatus.SKIPPED, _("مفيش موافقة")
        if appt is not None:
            if row.appointment_version is not None and row.appointment_version != appt.version:
                return MessageStatus.CANCELLED, _("اتعدل الميعاد")
            valid = VALID_STATUSES.get(row.event)
            if valid is not None and appt.status not in valid:
                if appt.status == AppointmentStatus.ARRIVED and row.event == Event.NEAR_TURN:
                    return MessageStatus.SKIPPED, _("المريض وصل")
                return MessageStatus.CANCELLED, _("الحجز بقى: %(s)s") % {"s": appt.get_status_display()}
            if row.event in (Event.REMINDER, Event.BOOKING_CONFIRMED, Event.NEAR_TURN):
                lead = row.template.min_lead_minutes if row.template else 0
                if appt.start_at - now < timedelta(minutes=lead) or appt.start_at < now:
                    return MessageStatus.SKIPPED, _("فات وقتها")
        # Catching up inside quiet hours (e.g. the scheduler restarted at night): re-apply the policy.
        new_at, reason = apply_quiet(now, ns, event=row.event, appt=appt)
        if reason:
            return MessageStatus.SKIPPED, reason
        if new_at > now:
            row.send_at = row.next_attempt_at = new_at
            row.status = MessageStatus.PENDING
            row.save(update_fields=["send_at", "next_attempt_at", "status", "updated_at"])
            return MessageStatus.PENDING, ""
        if row.patient and not row.recipient:
            row.recipient = recipient_for(row.patient, row.channel)
        return None

    def render(self, row):
        if row.event == Event.TEST:
            row.rendered_text = row.context.get("body", "")
            row.rendered_subject = row.context.get("subject", "") or _("رسالة تجربة من DigiClinic")
            return
        ctx = templating.appointment_ctx(row.appointment) if row.appointment else {}
        ctx.update(row.context or {})
        row.rendered_text = templating.render(row.template.body if row.template else "", ctx)
        subject = (row.template.email_subject if row.template else "") or row.get_event_display()
        clinic = ctx.get("clinic_name", "")
        row.rendered_subject = templating.render(subject, ctx) + (f" — {clinic}" if clinic else "")

    # --- providers ---------------------------------------------------------------------------

    def send(self, row, ns) -> ProviderResult:
        if not row.recipient:
            return ProviderResult(ok=False, error=_("مفيش رقم/إيميل"))
        if row.channel == MessageChannel.EMAIL:
            return self._send_email(row)
        return self._send_whatsapp(row, ns)

    def _send_email(self, row) -> ProviderResult:
        provider, _cfg = messaging.email_provider(row.organization)
        if provider is None:
            return ProviderResult(ok=False, error=_("الإيميل مش متظبط في الإعدادات."))
        try:
            with provider._connection() as conn:
                provider.build_message(
                    to=row.recipient, subject=row.rendered_subject, body=row.rendered_text, connection=conn
                ).send()
            return ProviderResult(ok=True)
        except Exception as e:
            import smtplib

            permanent = isinstance(e, smtplib.SMTPAuthenticationError | smtplib.SMTPRecipientsRefused)
            return ProviderResult(ok=False, error=str(e) or e.__class__.__name__, temporary=not permanent)

    def _send_whatsapp(self, row, ns) -> ProviderResult:
        registered = self._is_registered(row.organization, row.recipient)
        if registered is False:
            return ProviderResult(ok=False, error=whatsapp.ERRORS["not_on_whatsapp"])
        self._wait_gap(row.organization_id, ns)
        result = whatsapp.WhatsAppProvider(row.organization).send_text(row.recipient, row.rendered_text)
        self._last_wa[row.organization_id] = self.monotonic()
        logger.info("whatsapp %s to %s: %s", row.event, mask(row.recipient), "ok" if result.ok else "failed")
        return result

    def _wait_gap(self, org_id, ns):
        if not settings.NOTIFY_SLEEP_BETWEEN_WA or org_id not in self._last_wa:
            return
        wait = self._last_wa[org_id] + ns.wa_min_gap_seconds + self.jitter() - self.monotonic()
        if wait > 0:
            self.sleep(wait)

    def _is_registered(self, org, number) -> bool | None:
        now = timezone.now()
        cached = WhatsAppNumber.objects.filter(organization=org, number=number).first()
        if cached and now - cached.checked_at < timedelta(days=WA_NUMBER_CACHE_DAYS):
            return cached.is_registered
        result = whatsapp.is_registered(org, number)
        if result is not None:
            WhatsAppNumber.objects.update_or_create(
                organization=org, number=number, defaults={"is_registered": result, "checked_at": now}
            )
        return result

    # --- bookkeeping -------------------------------------------------------------------------

    def _record_delivery(self, row, result):
        Delivery.objects.create(
            organization=row.organization,
            scheduled_message=row,
            channel=ChannelKind.WHATSAPP if row.channel == MessageChannel.WHATSAPP else ChannelKind.EMAIL,
            recipient=row.recipient,
            recipient_name=row.patient.full_name if row.patient else "",
            subject=row.rendered_subject if row.channel == MessageChannel.EMAIL else "",
            message_text=row.rendered_text,
            status=DeliveryStatus.SENT if result.ok else DeliveryStatus.FAILED,
            provider_message_id=result.provider_message_id,
            error_message=result.error,
            attempts=row.attempts + 1,
            sent_by=row.created_by,
            sent_at=timezone.now() if result.ok else None,
        )

    def _finish(self, row, status, reason):
        row.status = status
        row.status_reason = reason[:200]
        row.save(
            update_fields=[
                "status",
                "status_reason",
                "recipient",
                "rendered_text",
                "rendered_subject",
                "attempts",
                "next_attempt_at",
                "sent_at",
                "updated_at",
            ]
        )

    def _email_fallback(self, row, now):
        if not row.patient or not row.patient.email:
            return
        ScheduledMessage.objects.get_or_create(
            dedupe_key=f"{row.dedupe_key}:fallback",
            defaults={
                "organization": row.organization,
                "patient": row.patient,
                "appointment": row.appointment,
                "template": row.template,
                "event": row.event,
                "channel": MessageChannel.EMAIL,
                "recipient": row.patient.email,
                "send_at": now,
                "next_attempt_at": now,
                "appointment_version": row.appointment_version,
                "context": row.context,
                "status_reason": _("بديل للواتساب"),
            },
        )


dispatcher = Dispatcher()


def recover_interrupted():
    """Rows left in `sending` by a crash may or may not have gone out — never auto-resend them (no duplicates);
    mark them failed so staff can check the chat and retry by hand."""
    return ScheduledMessage.objects.filter(status=MessageStatus.SENDING).update(
        status=MessageStatus.FAILED,
        status_reason=_("اتقطع أثناء الإرسال — اتأكد من المحادثة قبل إعادة المحاولة"),
        updated_at=timezone.now(),
    )


def send_now(row):
    """Used by the "ابعت تجربة" button: process one test row synchronously."""
    ScheduledMessage.objects.filter(pk=row.pk).update(status=MessageStatus.SENDING)
    row.status = MessageStatus.SENDING
    Dispatcher().process(row, now=timezone.now())
    row.refresh_from_db()
    return row


def cleanup(now=None):
    """Daily: pending rows whose time passed long ago (scheduler was off for days) are expired, not sent."""
    now = now or timezone.now()
    return ScheduledMessage.objects.filter(status=MessageStatus.PENDING, send_at__lt=now - timedelta(days=2)).update(
        status=MessageStatus.SKIPPED, status_reason=_("انتهت صلاحيتها"), updated_at=now
    )
