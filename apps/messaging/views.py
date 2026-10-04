import json

from django.contrib import messages
from django.http import HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.permissions import require_perm
from apps.audit import services as audit

from . import services, whatsapp
from .forms import EmailSettingsForm, WhatsAppTestForm
from .models import ChannelKind, ChannelStatus, SendingChannelConfig

# --- email settings --------------------------------------------------------------------------


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
        form.cleaned_data["to"], _("رسالة تجربة من DigiClinic ✓")
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
