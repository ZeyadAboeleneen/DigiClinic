from datetime import date, datetime, time, timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.accounts.permissions import require_perm
from apps.audit import services as audit
from apps.core.timeutils import CAIRO

from . import dispatcher, services, templating
from .forms import NotificationSettingsForm, TemplateForm, TestSendForm
from .models import (
    Event,
    MessageStatus,
    NotificationSettings,
    NotificationTemplate,
    ScheduledMessage,
    TemplateChannel,
)

# --- settings + templates (settings.manage) -------------------------------------------------


def _settings_ctx(request, form=None):
    org = request.organization
    ns = NotificationSettings.for_org(org)
    return {
        "form": form or NotificationSettingsForm(instance=ns),
        "templates": NotificationTemplate.objects.for_org(org).order_by("order", "id"),
        "heartbeat": services.last_heartbeat(),
        "scheduler_alive": services.scheduler_alive(),
        "tab": "messages",
    }


@require_perm("settings.manage")
def settings_messages(request):
    if request.method == "POST":
        form = NotificationSettingsForm(request.POST, instance=NotificationSettings.for_org(request.organization))
        if form.is_valid():
            form.save()
            audit.log("settings.notifications", request=request)
            messages.success(request, _("إعدادات الرسايل اتحفظت."))
            return redirect("notifications:settings")
        return render(request, "notifications/settings.html", _settings_ctx(request, form))
    return render(request, "notifications/settings.html", _settings_ctx(request))


def _template(request, pk):
    return get_object_or_404(NotificationTemplate.objects.for_org(request.organization), pk=pk)


def _edit_ctx(request, tpl, form):
    return {
        "tpl": tpl,
        "form": form,
        "variables": templating.VARIABLES,
        "preview": _preview_text(request.organization, tpl.body),
        "test_form": TestSendForm(initial={"to": request.user.phone or "", "channel": "whatsapp"}),
        "tab": "messages",
    }


@require_perm("settings.manage")
def template_edit(request, pk):
    tpl = _template(request, pk)
    form = TemplateForm(request.POST or None, instance=tpl)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit.log("settings.template", request=request, target=tpl, summary=tpl.name_ar)
        messages.success(request, _("القالب اتحفظ."))
        return redirect("notifications:settings")
    return render(request, "notifications/template_edit.html", _edit_ctx(request, tpl, form))


@require_POST
@require_perm("settings.manage")
def template_add_reminder(request):
    org = request.organization
    last = NotificationTemplate.objects.for_org(org).order_by("-order").first()
    tpl = NotificationTemplate.objects.create(
        organization=org,
        event=Event.REMINDER,
        name_ar=_("تذكير إضافي"),
        offset_minutes=180,
        min_lead_minutes=30,
        channel=TemplateChannel.DEFAULT,
        body=_("تذكير: ميعادك مع {doctor_name} النهارده {time_label}."),
        is_enabled=False,  # off until the clinic reviews the text
        order=(last.order + 1) if last else 0,
    )
    return redirect("notifications:template_edit", pk=tpl.pk)


@require_POST
@require_perm("settings.manage")
def template_delete(request, pk):
    tpl = _template(request, pk)
    if tpl.event != Event.REMINDER:
        messages.error(request, _("القوالب الأساسية مينفعش تتمسح — تقدر تقفلها بس."))
    else:
        tpl.delete()
        messages.success(request, _("التذكير اتمسح."))
    return redirect("notifications:settings")


def _preview_text(org, body):
    try:
        templating.validate_body(body)
    except ValidationError as e:
        return {"error": " ".join(e.messages)}
    return {"text": templating.render(body, templating.sample_ctx(org))}


@require_POST
@require_perm("settings.manage")
def template_preview(request):
    return render(
        request,
        "notifications/partials/preview.html",
        {"preview": _preview_text(request.organization, request.POST.get("body", ""))},
    )


@require_POST
@require_perm("settings.manage")
def template_test(request, pk):
    tpl = _template(request, pk)
    form = TestSendForm(request.POST)
    body = request.POST.get("body", tpl.body)
    preview = _preview_text(request.organization, body)
    if not form.is_valid() or "error" in preview:
        errors = form.errors.get("to") or form.non_field_errors() or [preview.get("error", "")]
        return render(request, "notifications/partials/test_result.html", {"ok": False, "error": " ".join(errors)})
    row = services.create_test_message(
        request.organization,
        channel=form.cleaned_data["channel"],
        recipient=form.cleaned_data["to"],
        body=preview["text"],
        subject=tpl.email_subject,
        by=request.user,
    )
    row = dispatcher.send_now(row)
    ok = row.status == MessageStatus.SENT
    return render(request, "notifications/partials/test_result.html", {"ok": ok, "error": row.status_reason})


# --- message log (messages.view / messages.retry) --------------------------------------------


def _parse_day(raw):
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


@require_perm("messages.view")
def message_log(request):
    org = request.organization
    qs = ScheduledMessage.objects.for_org(org).select_related("patient", "appointment").order_by("-send_at", "-id")
    status = request.GET.get("status", "")
    event = request.GET.get("event", "")
    day = _parse_day(request.GET.get("day"))
    appointment = request.GET.get("appointment", "")
    if status in MessageStatus.values:
        qs = qs.filter(status=status)
    if event in Event.values:
        qs = qs.filter(event=event)
    if day:
        start = datetime.combine(day, time.min, tzinfo=CAIRO)
        qs = qs.filter(send_at__gte=start, send_at__lt=start + timedelta(days=1))
    if appointment.isdigit():
        qs = qs.filter(appointment_id=int(appointment))
    page = Paginator(qs.prefetch_related("deliveries"), 50).get_page(request.GET.get("page"))
    ctx = {
        "page": page,
        "status": status,
        "event": event,
        "day": day,
        "appointment": appointment,
        "statuses": MessageStatus.choices,
        "events": Event.choices,
        "scheduler_alive": services.scheduler_alive(),
    }
    return render(request, "notifications/log.html", ctx)


def _message(request, pk):
    return get_object_or_404(ScheduledMessage.objects.for_org(request.organization), pk=pk)


def _row_response(request, row):
    if request.htmx:
        return render(request, "notifications/partials/log_row.html", {"m": row})
    return redirect("notifications:log")


@require_POST
@require_perm("messages.retry")
def message_retry(request, pk):
    row = _message(request, pk)
    if row.can_retry:
        services.retry(row, by=request.user)
        audit.log("message.retry", request=request, target=row)
    return _row_response(request, row)


@require_POST
@require_perm("messages.retry")
def message_cancel(request, pk):
    row = _message(request, pk)
    if services.cancel_message(row, by=request.user):
        audit.log("message.cancel", request=request, target=row)
    return _row_response(request, row)
