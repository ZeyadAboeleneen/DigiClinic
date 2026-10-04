import json

from django.contrib import messages
from django.http import HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.permissions import require_perm
from apps.audit import services as audit
from apps.customers.models import ChannelType, Contact
from apps.organizations.models import OrganizationSettings
from apps.quotations.models import Quotation

from . import services, whatsapp
from .forms import EmailSettingsForm, SendForm, TemplatesForm, WhatsAppTestForm
from .models import ChannelKind, ChannelStatus, Delivery, SendingChannelConfig


def _quote(request, pk):
    return get_object_or_404(
        Quotation.objects.for_org(request.organization).select_related("customer", "contact", "organization"), pk=pk
    )


def _recipient_options(request, q):
    """Every channel of every active contact of the customer, grouped by contact."""
    contacts = (
        Contact.objects.for_org(request.organization)
        .filter(customer_id=q.customer_id, is_active=True)
        .prefetch_related("channels")
        .order_by("-is_primary", "name")
    )
    email_on = services.email_ready(q.organization)
    wa_on = services.whatsapp_ready(q.organization)
    options = []
    for c in contacts:
        chans = []
        for ch in c.channels.all():
            if ch.type == ChannelType.EMAIL:
                enabled, wanted = email_on, c.preferred_channel in ("email", "both")
            elif ch.type == ChannelType.WHATSAPP:
                enabled, wanted = wa_on, c.preferred_channel in ("whatsapp", "both")
            else:
                continue  # plain phone numbers can't receive documents
            default = enabled and wanted and c.pk == q.contact_id and ch.is_primary
            chans.append({"ch": ch, "key": f"{ch.type}:{ch.pk}", "enabled": enabled, "default": default})
        options.append({"contact": c, "channels": chans})
    # Preferred channel unavailable? Fall back to whatever the main contact can receive.
    for opt in options:
        if opt["contact"].pk == q.contact_id and not any(x["default"] for x in opt["channels"]):
            for x in opt["channels"]:
                if x["enabled"]:
                    x["default"] = True
                    break
    return options


def _deliveries_ctx(q):
    deliveries = list(q.deliveries.select_related("sent_by"))
    return {"q": q, "deliveries": deliveries, "pending": any(d.is_pending for d in deliveries)}


@require_perm("quotation.send")
def send_page(request, pk):
    q = _quote(request, pk)
    if q.status not in services.SENDABLE_STATUSES:
        messages.error(request, _("العرض ده مينفعش يتبعت في حالته الحالية."))
        return redirect("quotations:detail", pk=q.pk)
    subject_tpl, body_tpl = services.email_templates(q.organization)
    variables = services.variables_for(q, q.contact, request.user)
    form = SendForm(
        initial={
            "subject": services.render_template(subject_tpl, variables),
            "body": services.render_template(body_tpl, variables),
            "wa_body": services.render_template(services.whatsapp_templates(q.organization), variables),
        }
    )
    ctx = {
        "q": q,
        "form": form,
        "options": _recipient_options(request, q),
        "reviewed": services.has_reviewed(q, request.user),
        "email_ready": services.email_ready(q.organization),
        "whatsapp_ready": services.whatsapp_ready(q.organization),
        **_deliveries_ctx(q),
    }
    return render(request, "messaging/send.html", ctx)


@require_POST
@require_perm("quotation.send")
def send_submit(request, pk):
    q = _quote(request, pk)
    form = SendForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("اكتب عنوان ونص الرسالة."))
        return redirect("messaging:send", pk=q.pk)

    wanted = set(request.POST.getlist("recipients"))
    recipients = []
    for opt in _recipient_options(request, q):
        for x in opt["channels"]:
            if x["key"] in wanted:
                recipients.append(
                    services.Recipient(
                        channel=ChannelKind.EMAIL if x["ch"].type == ChannelType.EMAIL else ChannelKind.WHATSAPP,
                        value=x["ch"].value,
                        name=str(opt["contact"]),
                        contact_channel_id=x["ch"].pk,
                    )
                )
    try:
        services.create_deliveries(
            q,
            request.user,
            recipients,
            subject=form.cleaned_data["subject"],
            body=form.cleaned_data["body"],
            wa_body=form.cleaned_data["wa_body"],
            confirmed=form.cleaned_data["confirm"],
        )
    except services.SendError as e:
        messages.error(request, str(e))
        return redirect("messaging:send", pk=q.pk)
    audit.log(
        "delivery.queued",
        request=request,
        target=q,
        summary=f"{q.display_number} → " + "، ".join(f"{r.name} ({r.channel})" for r in recipients),
    )
    messages.success(request, _("الإرسال بدأ. الحالة بتتحدث هنا لوحدها."))
    return redirect("messaging:send", pk=q.pk)


@require_perm("quotation.view")
def deliveries_partial(request, pk):
    q = _quote(request, pk)
    return render(request, "messaging/partials/deliveries.html", _deliveries_ctx(q))


@require_POST
@require_perm("quotation.send")
def retry_delivery(request, pk):
    d = get_object_or_404(Delivery.objects.for_org(request.organization).select_related("quotation"), pk=pk)
    try:
        services.retry(d, request.user)
        messages.success(request, _("بنحاول نبعت تاني."))
    except services.SendError as e:
        messages.error(request, str(e))
    return redirect("messaging:send", pk=d.quotation_id)


@require_POST
@require_perm("quotation.send")
def fallback_email(request, pk):
    d = get_object_or_404(
        Delivery.objects.for_org(request.organization).select_related("quotation", "contact_channel"), pk=pk
    )
    try:
        services.fallback_to_email(d, request.user)
        messages.success(request, _("اتبعت بالإيميل بدل الواتساب."))
    except services.SendError as e:
        messages.error(request, str(e))
    return redirect("messaging:send", pk=d.quotation_id)


# --- settings ------------------------------------------------------------------------------


@require_perm("settings.manage")
def email_settings(request):
    cfg = SendingChannelConfig.for_org(request.organization, ChannelKind.EMAIL)
    current = cfg.config
    initial = {
        "is_active": cfg.is_active,
        "display_name": cfg.display_name or request.organization.name_ar,
        "from_email": current.get("from_email", ""),
        "host": current.get("host", "smtp.gmail.com"),
        "port": current.get("port", 587),
        "security": current.get("security", "tls"),
        "smtp_user": current.get("username", ""),
    }
    form = EmailSettingsForm(request.POST or None, initial=initial, has_password=bool(current.get("password")))
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        new = {k: d[k] for k in ("from_email", "host", "port", "security")}
        new["username"] = d["smtp_user"]
        new["password"] = d["smtp_secret"] or current.get("password", "")
        cfg.config = new
        cfg.is_active = d["is_active"]
        cfg.display_name = d["display_name"]
        cfg.sender_identity = d["from_email"]
        cfg.status = ChannelStatus.UNKNOWN
        cfg.save()
        audit.log("settings.email", request=request, summary=d["from_email"])
        messages.success(request, _("إعدادات الإيميل اتحفظت. ابعت إيميل تجربة للتأكد."))
        return redirect("messaging:email_settings")
    return render(
        request,
        "messaging/settings_email.html",
        {"form": form, "cfg": cfg, "has_password": bool(current.get("password")), "tab": "email"},
    )


@require_POST
@require_perm("settings.manage")
def email_test(request):
    provider, cfg = services.email_provider(request.organization)
    if provider is None:
        cfg = SendingChannelConfig.for_org(request.organization, ChannelKind.EMAIL)
        if not cfg.config.get("host"):
            messages.error(request, _("احفظ الإعدادات الأول."))
            return redirect("messaging:email_settings")
        provider = services.SmtpEmailProvider(cfg.config, cfg.display_name)
    to = request.POST.get("to") or request.user.email
    result = provider.send_test(to)
    cfg.status = ChannelStatus.CONNECTED if result.ok else ChannelStatus.ERROR
    cfg.last_error = "" if result.ok else result.error
    cfg.last_checked_at = timezone.now()
    cfg.save(update_fields=["status", "last_error", "last_checked_at", "updated_at"])
    if result.ok:
        messages.success(request, _("إيميل التجربة اتبعت لـ %(to)s. شوف الـinbox.") % {"to": to})
    else:
        messages.error(request, _("فشل: %(e)s") % {"e": result.error})
    return redirect("messaging:email_settings")


@require_perm("settings.manage")
def templates_settings(request):
    s, _created = OrganizationSettings.objects.get_or_create(organization=request.organization)
    if not s.default_email_subject:
        s.default_email_subject = services.DEFAULT_EMAIL_SUBJECT
    if not s.default_email_body:
        s.default_email_body = services.DEFAULT_EMAIL_BODY
    if not s.default_whatsapp_message:
        s.default_whatsapp_message = services.DEFAULT_WHATSAPP
    form = TemplatesForm(request.POST or None, instance=s)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit.log("settings.templates", request=request)
        messages.success(request, _("القوالب اتحفظت."))
        return redirect("messaging:templates")
    return render(
        request,
        "messaging/settings_templates.html",
        {"form": form, "variables": services.VARIABLES, "tab": "templates"},
    )


# --- WhatsApp settings -----------------------------------------------------------------------


def _wa_ctx(request):
    org = request.organization
    cfg = SendingChannelConfig.for_org(org, ChannelKind.WHATSAPP)
    st = whatsapp.status(org)
    if st.get("state") == "ready" and st.get("me"):
        services.apply_gateway_status(org.pk, "ready", st["me"])
        cfg.refresh_from_db()
    expected = getattr(getattr(org, "settings", None), "whatsapp_sender", "") or ""
    connected = cfg.sender_identity if st.get("state") == "ready" else ""
    return {
        "cfg": cfg,
        "st": st,
        "state_label": whatsapp.STATE_LABELS.get(st.get("state"), st.get("state")),
        "expected": expected,
        "mismatch": bool(connected and expected and connected != expected),
        "tab": "whatsapp",
        "test_form": WhatsAppTestForm(),
    }


@require_perm("settings.manage")
def whatsapp_settings(request):
    return render(request, "messaging/settings_whatsapp.html", _wa_ctx(request))


@require_perm("settings.manage")
def whatsapp_status(request):
    return render(request, "messaging/partials/whatsapp_status.html", _wa_ctx(request))


@require_POST
@require_perm("settings.manage")
def whatsapp_connect(request):
    cfg = SendingChannelConfig.for_org(request.organization, ChannelKind.WHATSAPP)
    cfg.is_active = True
    cfg.save(update_fields=["is_active", "updated_at"])
    audit.log("whatsapp.connect", request=request)
    try:
        whatsapp.start(request.organization)
    except Exception:  # gateway down: the status panel explains it
        messages.error(request, _("بوابة الواتساب مش شغالة. شغّل run.bat تاني."))
    return redirect("messaging:whatsapp")


@require_POST
@require_perm("settings.manage")
def whatsapp_disconnect(request):
    try:
        whatsapp.logout(request.organization)
    except Exception:
        messages.error(request, _("بوابة الواتساب مش شغالة."))
    services.apply_gateway_status(request.organization.pk, "disconnected")
    SendingChannelConfig.objects.filter(organization=request.organization, kind=ChannelKind.WHATSAPP).update(
        is_active=False, sender_identity=""
    )
    audit.log("whatsapp.disconnect", request=request)
    messages.success(request, _("الواتساب اتفصل."))
    return redirect("messaging:whatsapp")


@require_POST
@require_perm("settings.manage")
def whatsapp_test(request):
    form = WhatsAppTestForm(request.POST)
    if not form.is_valid():
        messages.error(request, " ".join(form.errors.get("to", [])) or _("رقم مش صحيح."))
        return redirect("messaging:whatsapp")
    result = whatsapp.WhatsAppProvider(request.organization).send_text(
        form.cleaned_data["to"], _("رسالة تجربة من مرسول البرق ✓")
    )
    if result.ok:
        messages.success(request, _("رسالة التجربة اتبعتت."))
    else:
        messages.error(request, result.error)
    return redirect("messaging:whatsapp")


@csrf_exempt
@require_POST
def whatsapp_webhook(request):
    """Events from the local gateway, authenticated by an HMAC of the raw body (no session/CSRF)."""
    if not whatsapp.verify_signature(request.body, request.headers.get("X-Signature", "")):
        return HttpResponseForbidden("bad signature")
    try:
        payload = json.loads(request.body)
    except ValueError:
        return HttpResponseBadRequest("bad json")
    event = payload.get("event")
    if event == "ack":
        services.apply_ack(str(payload.get("id", "")), int(payload.get("ack") or 0))
    elif event == "status":
        session = str(payload.get("session", ""))
        if session.isdigit():
            services.apply_gateway_status(
                int(session), payload.get("state", ""), payload.get("me"), payload.get("error", "")
            )
    return JsonResponse({"ok": True})
