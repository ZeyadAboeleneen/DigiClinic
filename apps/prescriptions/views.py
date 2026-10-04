"""Prescription builder (03 §3.6) inside the doctor desk, PDFs, catalog and settings screens."""

from datetime import date

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.accounts.permissions import has_perm, require_membership, require_perm
from apps.audit import services as audit
from apps.clinical.models import Visit
from apps.clinical.views import desk_unlocked
from apps.documents import pdf as engine
from apps.notifications.models import NotificationSettings

from . import matching, pdf, safety, services
from .forms import DrugForm, PrescriptionSettingsForm, RxNotifyForm
from .models import (
    DRUG_FORMS,
    DosePhrase,
    Drug,
    Prescription,
    PrescriptionItem,
    PrescriptionSettings,
    PrescriptionTemplate,
    PrintMode,
    RxStatus,
)

# --- helpers ---------------------------------------------------------------------------------


def _rx(request, pk):
    return get_object_or_404(
        Prescription.objects.for_org(request.organization).select_related("patient", "doctor", "visit"), pk=pk
    )


def _item(request, pk):
    return get_object_or_404(
        PrescriptionItem.objects.filter(organization=request.organization).select_related("prescription"), pk=pk
    )


def can_print(request) -> bool:
    """Doctor/owner always; reception only when the clinic allows reprints (04 §4.3 ⚙)."""
    if has_perm(request.membership, "prescription.print"):
        return True
    return (
        has_perm(request.membership, "reception.operate")
        and PrescriptionSettings.for_org(request.organization).reception_can_reprint
    )


def _current_for_visit(visit):
    draft = Prescription.objects.filter(visit=visit, status=RxStatus.DRAFT).order_by("-created_at").first()
    if draft:
        return draft
    final = Prescription.objects.filter(visit=visit, status=RxStatus.FINAL).order_by("-issued_at").first()
    return final or services.draft_for_visit(visit)


def _builder_ctx(request, rx, **extra):
    org = request.organization
    ns = NotificationSettings.for_org(org)
    ctx = {
        "rx": rx,
        "items": list(rx.items.select_related("drug")),
        "warnings": safety.warnings_for(rx) if rx.is_editable else [],
        "phrases": DosePhrase.objects.filter(organization=org),
        "templates": PrescriptionTemplate.objects.filter(organization=org, doctor=rx.doctor)[:30],
        "has_previous": Prescription.objects.filter(patient=rx.patient, status=RxStatus.FINAL)
        .exclude(pk=rx.pk)
        .exists(),
        "send_enabled": ns.send_prescription_after_visit,
        "rx_settings": PrescriptionSettings.for_org(org),
        "drug_forms": DRUG_FORMS,
        "revisions": Prescription.objects.filter(visit=rx.visit).exclude(pk=rx.pk).order_by("-created_at")
        if rx.visit_id
        else [],
    }
    ctx.update(extra)
    return ctx


def _builder(request, rx, **extra):
    return render(request, "prescriptions/partials/builder.html", _builder_ctx(request, rx, **extra))


def _error(request, rx, exc):
    return _builder(request, rx, error=str(exc))


# --- builder ---------------------------------------------------------------------------------


@require_perm("prescription.write")
@desk_unlocked
def builder(request, visit_pk):
    visit = get_object_or_404(Visit.objects.for_org(request.organization), pk=visit_pk)
    if not settings.TESTING:
        engine.warm_up()  # Chromium starts while the doctor writes, so "اعتماد وطباعة" is fast
    return _builder(request, _current_for_visit(visit))


@require_perm("prescription.write")
@desk_unlocked
def drug_search(request, pk):
    rx = _rx(request, pk)
    q = (request.GET.get("q") or request.GET.get("drug_name") or "").strip()
    drugs = services.search_drugs(request.organization, q) if q else []
    return render(request, "prescriptions/partials/drug_results.html", {"rx": rx, "drugs": drugs, "q": q})


@require_POST
@require_perm("prescription.write")
@desk_unlocked
def item_add(request, pk):
    rx = _rx(request, pk)
    drug = None
    if request.POST.get("drug", "").isdigit():
        drug = Drug.objects.filter(organization=request.organization, pk=request.POST["drug"]).first()
    elif request.POST.get("free") != "1":
        # Enter in the search box (possibly before the suggestions arrived): take the best catalog match, the same
        # one the dropdown would list first. "+ إضافة" (free=1) always adds the typed text as-is.
        matches = services.search_drugs(request.organization, request.POST.get("drug_name", ""), limit=1)
        drug = matches[0] if matches else None
    try:
        services.add_item(rx, drug=drug, drug_name=request.POST.get("drug_name", ""))
    except services.PrescriptionError as e:
        return _error(request, rx, e)
    return _builder(request, rx, focus_last=True)


@require_POST
@require_perm("prescription.write")
@desk_unlocked
def item_update(request, item_pk):
    """Autosave of one cell. Returns the save status + an out-of-band refresh of the warnings box."""
    item = _item(request, item_pk)
    try:
        services.update_item(item, request.POST.get("field", ""), request.POST.get("value", ""))
    except services.PrescriptionError as e:
        return render(request, "prescriptions/partials/cell_status.html", {"error": str(e)})
    rx = item.prescription
    return render(request, "prescriptions/partials/cell_status.html",
                  {"rx": rx, "warnings": safety.warnings_for(rx), "oob": True})  # fmt: skip


@require_POST
@require_perm("prescription.write")
@desk_unlocked
def item_action(request, item_pk, action):
    item = _item(request, item_pk)
    rx = item.prescription
    try:
        if action == "remove":
            services.remove_item(item)
        elif action in ("up", "down"):
            services.move_item(item, action)
        elif action == "confirm":
            services.confirm_item(item)
        else:
            raise Http404
    except services.PrescriptionError as e:
        return _error(request, rx, e)
    return _builder(request, rx)


@require_POST
@require_perm("prescription.write")
@desk_unlocked
def rx_fields(request, pk):
    rx = _rx(request, pk)
    try:
        next_visit = request.POST.get("next_visit_date")
        services.update_fields(
            rx,
            advice=request.POST.get("advice") if "advice" in request.POST else None,
            next_visit_date=(date.fromisoformat(next_visit) if next_visit else "")
            if "next_visit_date" in request.POST
            else None,
            send_to_patient=(request.POST.get("send_to_patient") == "1") if "send_to_patient" in request.POST else None,
        )
    except (services.PrescriptionError, ValueError) as e:
        return render(request, "prescriptions/partials/cell_status.html", {"error": str(e)})
    return render(request, "prescriptions/partials/cell_status.html", {})


@require_POST
@require_perm("prescription.write")
@desk_unlocked
def rx_action(request, pk, action):
    rx = _rx(request, pk)
    try:
        if action == "template":
            tpl = get_object_or_404(PrescriptionTemplate.objects.filter(organization=request.organization),
                                    pk=request.POST.get("template"))  # fmt: skip
            services.apply_template(rx, tpl)
        elif action == "repeat":
            services.repeat_last(rx)
        elif action == "save_template":
            tpl = services.save_as_template(rx, request.POST.get("name", ""))
            return _builder(request, rx, notice=_("اتحفظ كقالب: %(n)s") % {"n": tpl.name})
        elif action == "finalize":
            rx = services.finalize(rx, by=request.user, acknowledged=request.POST.getlist("ack"))
            audit.log("prescription.finalized", request=request, target=rx, summary=rx.display_number)
            return _builder(request, rx, print_now=True)
        elif action == "revise":
            new = services.revise(rx, by=request.user)
            return _builder(request, new)
        elif action == "void":
            services.void(rx, reason=request.POST.get("reason", ""), by=request.user)
            audit.log("prescription.voided", request=request, target=rx, summary=rx.display_number)
            return _builder(request, _current_for_visit(rx.visit) if rx.visit_id else rx)
        elif action == "new":
            if rx.visit_id is None:
                raise Http404
            return _builder(request, services.draft_for_visit(rx.visit))
        else:
            raise Http404
    except services.PrescriptionError as e:
        return _error(request, rx, e)
    return _builder(request, rx)


@require_POST
@require_perm("drug.manage")
@desk_unlocked
def item_to_catalog(request, item_pk):
    """ "ضيفه للكتالوج" for a free-typed line."""
    item = _item(request, item_pk)
    drug, _created = Drug.objects.get_or_create(
        organization=request.organization,
        name=item.drug_name,
        defaults={"form": item.form, "default_instructions": item.instructions, "default_duration": item.duration},
    )
    if item.prescription.is_editable:
        PrescriptionItem.objects.filter(pk=item.pk).update(drug=drug)
    return _builder(request, item.prescription, notice=_("%(d)s اتضاف للكتالوج") % {"d": drug.name})


# --- voice dictation (13 §13.3) ---------------------------------------------------------------


@require_POST
@require_perm("prescription.write")
@desk_unlocked
def dictate(request, pk):
    """Dictated text → one row per spoken line with the top-3 catalog matches. Nothing is saved here."""
    rx = _rx(request, pk)
    lines = matching.match_text(request.organization, request.POST.get("text", "")[:2000])
    return render(request, "prescriptions/partials/dictation_results.html", {"rx": rx, "lines": lines})


@require_POST
@require_perm("prescription.write")
@desk_unlocked
def dictate_add(request, pk):
    """The doctor picked a suggestion (or "as spoken"): add it flagged `needs_review` (13 §13.4) and learn the alias."""
    rx = _rx(request, pk)
    drug = None
    if request.POST.get("drug", "").isdigit():
        drug = Drug.objects.filter(organization=request.organization, pk=request.POST["drug"]).first()
    instructions = request.POST.get("instructions", "").strip()
    try:
        services.add_item(
            rx,
            drug=drug,
            drug_name=request.POST.get("spoken", ""),
            instructions=instructions or None,
            needs_review=True,
        )
    except services.PrescriptionError as e:
        return _error(request, rx, e)
    if drug is not None:
        matching.learn_alias(drug, request.POST.get("spoken", ""))
    return _builder(request, rx)


# --- PDFs ------------------------------------------------------------------------------------


@require_membership
def rx_pdf(request, pk):
    """`?mode=print` follows the clinic's print setting; patients always get `full` (stored at finalize)."""
    rx = _rx(request, pk)
    is_writer = has_perm(request.membership, "prescription.write")
    if rx.status == RxStatus.DRAFT:
        if not is_writer:
            raise PermissionDenied
    elif not (is_writer or can_print(request)):
        raise PermissionDenied
    requested = request.GET.get("mode", "full")
    mode = PrescriptionSettings.for_org(request.organization).print_mode if requested == "print" else PrintMode.FULL
    if rx.status == RxStatus.FINAL and mode == PrintMode.FULL and services.ensure_pdf(rx):
        with rx.pdf_full.open("rb") as fh:
            data = fh.read()
    else:
        try:
            data = pdf.render_prescription(rx, mode=mode)
        except Exception:
            return HttpResponse(_("مش قادر أعمل الـPDF دلوقتي — جرب تاني."), status=503)
    audit.log("prescription.printed", request=request, target=rx, summary=f"{rx.display_number} {mode}")
    resp = HttpResponse(data, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{rx.display_number or "draft"}.pdf"'
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


@require_perm("settings.manage")
def calibration_pdf(request):
    try:
        data = pdf.render_calibration(request.organization)
    except Exception:
        return HttpResponse(_("مش قادر أعمل الـPDF دلوقتي — جرب تاني."), status=503)
    resp = HttpResponse(data, content_type="application/pdf")
    resp["Content-Disposition"] = 'inline; filename="calibration.pdf"'
    return resp


# --- patient's prescriptions (desk tab + reception reprint) ----------------------------------


@require_membership
def patient_prescriptions(request, patient_pk):
    is_writer = has_perm(request.membership, "prescription.write")
    if not (is_writer or can_print(request)):
        raise PermissionDenied
    rxs = (
        Prescription.objects.for_org(request.organization).filter(patient_id=patient_pk).exclude(status=RxStatus.DRAFT)
    )
    return render(
        request,
        "prescriptions/partials/patient_list.html",
        # Reception (reprint only) sees numbers and dates — not the drugs (04 §4.3).
        {"prescriptions": rxs.prefetch_related("items") if is_writer else rxs, "show_items": is_writer},
    )


# --- settings & catalog ----------------------------------------------------------------------


@require_perm("settings.manage")
def settings_prescription(request):
    org = request.organization
    s = PrescriptionSettings.for_org(org)
    ns = NotificationSettings.for_org(org)
    form = PrescriptionSettingsForm(request.POST or None, instance=s)
    notify_form = RxNotifyForm(request.POST or None, instance=ns)
    if request.method == "POST" and form.is_valid() and notify_form.is_valid():
        form.save()
        notify_form.save()
        audit.log("settings.prescription", request=request)
        messages.success(request, _("إعدادات الروشتة اتحفظت."))
        return redirect("prescriptions:settings")
    return render(request, "prescriptions/settings.html", {"form": form, "notify_form": notify_form, "tab": "rx"})


@require_perm("drug.manage")
def drug_catalog(request):
    org = request.organization
    form = DrugForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        drug = form.save(commit=False)
        drug.organization = org
        if Drug.objects.filter(organization=org, name=drug.name).exists():
            form.add_error("name", _("الدوا ده موجود."))
        else:
            drug.save()
            messages.success(request, _("%(d)s اتضاف.") % {"d": drug.name})
            return redirect("prescriptions:drugs")
    q = request.GET.get("q", "").strip()
    drugs = services.search_drugs(org, q, limit=200) if q else Drug.objects.filter(organization=org)[:200]
    ctx = {"form": form, "drugs": drugs, "q": q, "drug_forms": DRUG_FORMS}
    return render(request, "prescriptions/drugs.html", ctx)


@require_POST
@require_perm("drug.manage")
def drug_toggle(request, pk):
    drug = get_object_or_404(Drug.objects.filter(organization=request.organization), pk=pk)
    drug.is_active = not drug.is_active
    drug.save(update_fields=["is_active", "updated_at"])
    return redirect("prescriptions:drugs")


@require_perm("rx_template.manage")
def template_list(request):
    tpls = PrescriptionTemplate.objects.filter(organization=request.organization).prefetch_related("items")
    return render(request, "prescriptions/templates.html", {"tpls": tpls})


@require_POST
@require_perm("rx_template.manage")
def template_delete(request, pk):
    tpl = get_object_or_404(PrescriptionTemplate.objects.filter(organization=request.organization), pk=pk)
    tpl.delete()
    messages.success(request, _("القالب اتمسح."))
    return redirect("prescriptions:templates")
