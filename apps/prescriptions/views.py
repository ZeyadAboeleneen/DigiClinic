"""Prescription builder (03 §3.6) inside the doctor desk, PDFs, catalog and settings screens."""

from datetime import date

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.accounts.permissions import has_perm, require_membership, require_perm
from apps.audit import services as audit
from apps.clinical.models import Visit
from apps.clinical.views import desk_unlocked
from apps.doctors.services import get_doctor
from apps.documents import pdf as engine
from apps.notifications.models import NotificationSettings

from . import categories as cats
from . import extraction, matching, pdf, safety, services
from .forms import DrugForm, PrescriptionSettingsForm, RxNotifyForm
from .models import (
    DRUG_FORMS,
    CategoryLevel,
    DoctorCategory,
    DosePhrase,
    Drug,
    DrugCategory,
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


_current_for_visit = services.current_for_visit


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
        "categories": cats.for_doctor(rx.doctor),
        "recent": services.recent_for_patient(rx.patient, exclude_rx=rx) if rx.is_editable else [],
        "favorites": services.favorites_for_doctor(rx.doctor) if rx.is_editable else [],
        "drug_forms": DRUG_FORMS,
        "revisions": Prescription.objects.filter(visit=rx.visit).exclude(pk=rx.pk).order_by("-created_at")
        if rx.visit_id
        else [],
    }
    # quick-add chips: hide what's already on this prescription
    have = {(it.drug_id or it.drug_name.strip().lower()) for it in ctx["items"]}
    ctx["recent"] = [it for it in ctx["recent"] if (it.drug_id or it.drug_name.strip().lower()) not in have]
    ctx["favorites"] = [it for it in ctx["favorites"] if it.drug_id not in have]
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
    category = _category(request)
    drugs = services.search_drugs(request.organization, q, limit=12, category=category) if q or category else []
    ctx = {"rx": rx, "drugs": drugs, "q": q, "category": category}
    return render(request, "prescriptions/partials/drug_results.html", ctx)


def _category(request):
    raw = request.GET.get("category") or request.POST.get("category") or ""
    if not raw.isdigit():
        return None
    return DrugCategory.objects.filter(organization=request.organization, pk=int(raw)).first()


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
        q = request.POST.get("drug_name", "")
        matches = services.search_drugs(request.organization, q, limit=1, category=_category(request)) if q else []
        drug = matches[0] if matches else None
    # Defaults: what the chip carried (the patient's previous line), else how this doctor last prescribed the drug,
    # else the catalog's default (inside add_item). A workflow shortcut — every cell stays editable.
    instructions = request.POST.get("instructions")
    duration = request.POST.get("duration")
    form = request.POST.get("form")
    if drug is not None and instructions is None:
        last = services.last_usage(rx.doctor, drug.pk)
        if last is not None:
            instructions, duration, form = last.instructions, last.duration, last.form or None
    try:
        services.add_item(rx, drug=drug, drug_name=request.POST.get("drug_name", ""), instructions=instructions,
                          duration=duration, form=form)  # fmt: skip
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
    category = _category(request)
    if category is not None:
        drug.categories.add(category)
    if item.prescription.is_editable:
        PrescriptionItem.objects.filter(pk=item.pk).update(drug=drug)
    return _builder(request, item.prescription, notice=_("%(d)s اتضاف للكتالوج") % {"d": drug.name})


# --- voice dictation (13 §13.3) ---------------------------------------------------------------


@require_POST
@require_perm("prescription.write")
@desk_unlocked
def dictate(request, pk):
    """Dictated speech → one row per medicine mentioned (name + الجرعة + المدة), ignoring the rest of the
    conversation. If no medicine is recognised at all, fall back to one row per spoken line. Nothing is saved here."""
    rx = _rx(request, pk)
    text = request.POST.get("text", "")[:4000]
    lines = extraction.extract(request.organization, text)
    extracted = bool(lines)
    if not extracted:
        lines = matching.match_text(request.organization, text[:2000])
    ctx = {"rx": rx, "lines": lines, "extracted": extracted}
    return render(request, "prescriptions/partials/dictation_results.html", ctx)


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
            duration=request.POST.get("duration", "").strip() or None,
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
    doctor = get_doctor(org)
    form = DrugForm(request.POST or None, org=org, doctor=doctor)
    if request.method == "POST" and form.is_valid():
        drug = form.save(commit=False)
        drug.organization = org
        if Drug.objects.filter(organization=org, name=drug.name).exists():
            form.add_error("name", _("الدوا ده موجود."))
        else:
            drug.save()
            form.save_m2m()
            messages.success(request, _("%(d)s اتضاف.") % {"d": drug.name})
            return redirect("prescriptions:drugs")
    q = request.GET.get("q", "").strip()
    category = _category(request)
    if q or category:
        drugs = services.search_drugs(org, q, limit=300, category=category)
    else:
        drugs = list(Drug.objects.filter(organization=org).prefetch_related("categories")[:300])
    ctx = {"form": form, "drugs": drugs, "q": q, "drug_forms": DRUG_FORMS, "category": category,
           "doctor_categories": cats.for_doctor(doctor)}  # fmt: skip
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


@require_perm("drug.manage")
def drug_edit(request, pk):
    drug = get_object_or_404(Drug.objects.filter(organization=request.organization), pk=pk)
    form = DrugForm(request.POST or None, instance=drug, org=request.organization,
                    doctor=get_doctor(request.organization))  # fmt: skip
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("%(d)s اتحفظ.") % {"d": drug.name})
        return redirect("prescriptions:drugs")
    return render(request, "prescriptions/drug_edit.html", {"form": form, "drug": drug, "drug_forms": DRUG_FORMS})


# --- drug categories per doctor (Settings → تصنيفات الأدوية) ---------------------------------


@require_perm("drug.manage")
def categories_settings(request):
    from .specialties import SPECIALTIES, SPECIALTY_CHOICES

    doctor = get_doctor(request.organization)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "specialty":
            key = request.POST.get("specialty", "")
            try:
                cats.apply_specialty(doctor, key)
            except ValueError:
                messages.error(request, _("اختار تخصص."))
            else:
                audit.log("settings.drug_categories", request=request, summary=key)
                messages.success(request, _("التصنيفات اتظبطت على التخصص."))
        elif action == "add":
            name = request.POST.get("name", "").strip()
            level = request.POST.get("level") or CategoryLevel.PRIMARY
            if name:
                cats.add(doctor, name, level if level in CategoryLevel.values else CategoryLevel.PRIMARY)
                messages.success(request, _("%(n)s اتضاف.") % {"n": name})
        elif action in ("up", "down", "remove"):
            entry = get_object_or_404(DoctorCategory.objects.filter(doctor=doctor), pk=request.POST.get("entry"))
            if action == "remove":
                entry.delete()  # the category name and its drug tags stay; it can be added back any time
            else:
                cats.move(entry, action)
        return redirect("prescriptions:categories")
    entries = cats.for_doctor(doctor, include_hidden=True)
    counts = dict(
        DrugCategory.objects.filter(organization=request.organization).annotate(n=Count("drugs")).values_list("pk", "n")
    )
    for e in entries:
        e.drug_count = counts.get(e.category_id, 0)
    ctx = {
        "doctor": doctor,
        "entries": entries,
        "specialties": SPECIALTY_CHOICES,
        "levels": CategoryLevel.choices,
        # Suggestions for "add": every name the presets or this clinic use, so the doctor reuses existing names
        # (drug tags match by category, not by spelling variants).
        "known_categories": sorted(
            {n for _name, items in SPECIALTIES.values() for n, _lvl in items}
            | set(DrugCategory.objects.filter(organization=request.organization).values_list("name", flat=True)),
            key=str.lower,
        ),
        "tab": "drug_categories",
    }
    return render(request, "prescriptions/categories.html", ctx)
