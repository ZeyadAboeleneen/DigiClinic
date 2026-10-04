"""Doctor desk (03 §3.5) and the medical file. Every view here is clinical: `clinical.view` / `clinical.edit`
(reception gets 403), opening a file is audited, and the desk locks after 15 idle minutes (04 §4.1)."""

from functools import wraps

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.accounts.permissions import has_perm, require_perm
from apps.audit import services as audit
from apps.core.ratelimit import search_limit
from apps.core.timeutils import CAIRO
from apps.doctors.services import get_doctor
from apps.patients import custom_fields
from apps.patients.models import FieldScope, Patient
from apps.scheduling.models import Appointment

from . import services
from .forms import AttachmentForm
from .models import Attachment, Visit, VisitStatus, Vitals

LOCK_KEY = "desk_locked"


def desk_unlocked(view):
    """Server-side half of the idle lock: while the session is locked, desk pages show the lock screen."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.session.get(LOCK_KEY):
            if request.htmx:
                resp = HttpResponse(status=204)
                resp["HX-Redirect"] = request.get_full_path() if request.method == "GET" else "/desk/"
                return resp
            return render(request, "clinical/lock.html", {"next": request.get_full_path()}, status=423)
        return view(request, *args, **kwargs)

    return wrapper


def _patient(request, pk):
    return get_object_or_404(Patient.objects.for_org(request.organization), pk=pk)


def _visit(request, pk):
    return get_object_or_404(
        Visit.objects.for_org(request.organization).select_related("patient", "appointment__visit_type", "doctor"),
        pk=pk,
    )


def _age(patient):
    if not patient.date_of_birth:
        return None
    today = timezone.localtime(timezone.now(), CAIRO).date()
    dob = patient.date_of_birth
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _header_ctx(patient, current=None):
    visits = Visit.objects.filter(patient=patient).order_by("created_at")
    previous = visits.filter(status=VisitStatus.FINISHED)
    if current is not None:
        previous = previous.exclude(pk=current.pk)
    return {
        "patient": patient,
        "age": _age(patient),
        "allergies": list(patient.allergies.all()),
        "conditions": list(patient.chronic_conditions.all()),
        "last_visit": previous.order_by("-finished_at").first(),
        "visit_number": (visits.filter(created_at__lte=current.created_at).count() if current else None),
        "vitals": Vitals.objects.filter(visit=current).first() if current else None,
    }


def _queue_ctx(request):
    doctor = get_doctor(request.organization)
    current, waiting, not_arrived = services.today_queue(doctor)
    from apps.prescriptions.models import PrescriptionSettings

    voice = PrescriptionSettings.for_org(request.organization).voice_dictation_enabled
    return {
        "doctor": doctor,
        "q_current": current,
        "q_waiting": waiting,
        "q_not_arrived": not_arrived,
        "voice_enabled": voice,
    }


# --- desk ------------------------------------------------------------------------------------


@require_perm("clinical.view")
@desk_unlocked
def desk(request):
    ctx = _queue_ctx(request)
    if ctx["q_current"]:
        # Called in from reception (or another tab): the visit may not exist yet — start_visit creates it.
        visit = services.start_visit(ctx["q_current"][0], by=request.user)
        return redirect("clinical:visit", pk=visit.pk)
    return render(request, "clinical/desk.html", ctx)


@require_perm("clinical.view")
@desk_unlocked
def desk_queue(request):
    return render(request, "clinical/partials/queue.html", _queue_ctx(request))


@require_POST
@require_perm("clinical.edit")
@desk_unlocked
def desk_call(request, appt_pk):
    appt = get_object_or_404(Appointment.objects.for_org(request.organization), pk=appt_pk)
    try:
        visit = services.start_visit(appt, by=request.user)
    except (services.VisitError, services.booking.InvalidTransition) as e:
        messages.error(request, str(e))
        return redirect("clinical:desk")
    return redirect("clinical:visit", pk=visit.pk)


@require_perm("clinical.view")
@desk_unlocked
@search_limit
def desk_search(request):
    q = request.GET.get("q", "").strip()
    patients = Patient.objects.for_org(request.organization).filter(is_active=True).search(q)[:10] if q else []
    return render(request, "clinical/partials/search_results.html", {"patients": patients, "q": q})


# --- visit -----------------------------------------------------------------------------------


def _visit_form_ctx(visit):
    defs = custom_fields.definitions(visit.organization, FieldScope.VISIT)
    return {
        "visit": visit,
        "custom": [(custom_fields.PREFIX + d.key, d, visit.custom_fields.get(d.key)) for d in defs],
        "diagnoses": services.diagnosis_suggestions(visit.doctor),
    }


@require_perm("clinical.view")
@desk_unlocked
def visit_view(request, pk):
    visit = _visit(request, pk)
    services.log_clinical_view(request, visit.patient)
    ctx = {**_queue_ctx(request), **_header_ctx(visit.patient, visit), **_visit_form_ctx(visit), "tab": "visit"}
    return render(request, "clinical/visit.html", ctx)


@require_POST
@require_perm("clinical.edit")
@desk_unlocked
def visit_field(request, pk):
    visit = _visit(request, pk)
    name = request.POST.get("field", "")
    try:
        services.save_field(visit, name, request.POST.get("value", ""), by=request.user)
    except ValidationError as e:
        return render(request, "clinical/partials/autosave.html", {"error": " ".join(e.messages)})
    return render(request, "clinical/partials/autosave.html", {"saved_at": timezone.now()})


@require_POST
@require_perm("clinical.edit")
@desk_unlocked
def visit_finish(request, pk):
    visit = _visit(request, pk)
    try:
        services.finish_visit(visit, by=request.user)
    except services.VisitError as e:
        messages.error(request, str(e))
        return redirect("clinical:visit", pk=visit.pk)
    audit.log("visit.finished", request=request, target=visit, summary=visit.patient.full_name)
    if visit.followup_after_days:
        messages.info(request, _("الاستقبال هيشوف طلب حجز الإعادة."))
    if request.POST.get("next") == "1":
        nxt = services.call_next(visit.doctor, by=request.user)
        if nxt:
            return redirect("clinical:visit", pk=nxt.pk)
        messages.info(request, _("مفيش حد مستني."))
    return redirect("clinical:desk")


# --- patient file (any patient, from search) --------------------------------------------------


@require_perm("clinical.view")
@desk_unlocked
def patient_record(request, pk):
    patient = _patient(request, pk)
    services.log_clinical_view(request, patient)
    ctx = {**_queue_ctx(request), **_header_ctx(patient), "tab": "history"}
    return render(request, "clinical/record.html", ctx)


@require_perm("clinical.view")
@desk_unlocked
def patient_history(request, pk):
    patient = _patient(request, pk)
    visits = list(
        Visit.objects.filter(patient=patient)
        .select_related("appointment__visit_type", "vitals")
        .prefetch_related("attachments")
        .order_by("-created_at")
    )
    series = [v.vitals for v in reversed(visits) if hasattr(v, "vitals")]
    weights = [(v.recorded_at, float(v.weight_kg)) for v in series if v.weight_kg]
    systolic = [(v.recorded_at, v.bp_systolic) for v in series if v.bp_systolic]
    return render(
        request,
        "clinical/partials/tab_history.html",
        {"patient": patient, "visits": visits, "weight_chart": _chart(weights), "bp_chart": _chart(systolic)},
    )


def _chart(points, width=280, height=60):
    """Tiny inline-SVG sparkline: list of (x, y) → polyline points + min/max labels (None if < 2 values)."""
    if len(points) < 2:
        return None
    ys = [y for _x, y in points]
    lo, hi = min(ys), max(ys)
    span = (hi - lo) or 1
    step = width / (len(points) - 1)
    coords = " ".join(f"{i * step:.1f},{height - (y - lo) / span * (height - 8) - 4:.1f}" for i, y in enumerate(ys))
    return {"points": coords, "min": lo, "max": hi, "last": ys[-1], "width": width, "height": height}


@require_perm("clinical.view")
@desk_unlocked
def patient_attachments(request, pk):
    patient = _patient(request, pk)
    return render(request, "clinical/partials/tab_attachments.html", _attachments_ctx(request, patient))


def _attachments_ctx(request, patient, form=None):
    return {
        "patient": patient,
        "attachments": Attachment.objects.filter(patient=patient, is_archived=False),
        "form": form or AttachmentForm(),
        "visit_id": request.GET.get("visit") or request.POST.get("visit") or "",
    }


@require_perm("clinical.view")
@desk_unlocked
def patient_data(request, pk):
    patient = _patient(request, pk)
    defs = custom_fields.definitions(request.organization, FieldScope.PATIENT)
    extra = [(d.label_ar, patient.custom_fields.get(d.key)) for d in defs]
    return render(request, "clinical/partials/tab_data.html", {"patient": patient, "extra": extra})


# --- attachments -----------------------------------------------------------------------------


@require_POST
@require_perm("attachment.upload")
def attachment_upload(request, pk):
    """Reception may upload (e.g. a lab result the patient brought) but only clinical roles see the list."""
    patient = _patient(request, pk)
    form = AttachmentForm(request.POST, request.FILES)
    visit = None
    if request.POST.get("visit", "").isdigit():
        visit = Visit.objects.for_org(request.organization).filter(patient=patient, pk=request.POST["visit"]).first()
    if form.is_valid():
        try:
            att = services.add_attachment(patient=patient, f=form.cleaned_data["file"], kind=form.cleaned_data["kind"],
                                          title=form.cleaned_data["title"], taken_on=form.cleaned_data["taken_on"],
                                          visit=visit, by=request.user)  # fmt: skip
        except ValidationError as e:
            form.add_error("file", e)
        else:
            audit.log("attachment.uploaded", request=request, target=patient, summary=str(att))
            form = None
    if has_perm(request.membership, "clinical.view"):
        return render(request, "clinical/partials/tab_attachments.html", _attachments_ctx(request, patient, form))
    if form is None:
        messages.success(request, _("الملف اترفع."))
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect("patients:detail", pk=patient.pk)


@require_perm("clinical.view")
def attachment_file(request, pk):
    att = get_object_or_404(Attachment.objects.for_org(request.organization), pk=pk)
    services.log_clinical_view(request, att.patient)
    resp = FileResponse(att.file.open("rb"), content_type=att.content_type)
    resp["Content-Disposition"] = "inline"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


# --- idle lock -------------------------------------------------------------------------------


@require_POST
@require_perm("clinical.view")
def desk_lock(request):
    request.session[LOCK_KEY] = True
    return HttpResponse(status=204)


@require_POST
@require_perm("clinical.view")
def desk_unlock(request):
    nxt = request.POST.get("next") or "/desk/"
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/desk/"
    if request.user.check_password(request.POST.get("password", "")):
        request.session.pop(LOCK_KEY, None)
        return redirect(nxt)
    audit.log("desk.unlock_failed", request=request)
    return render(request, "clinical/lock.html", {"next": nxt, "error": _("كلمة المرور غلط.")}, status=423)


@require_perm("clinical.view")
@desk_unlocked
def patient_export(request, pk):
    """04 §4.4: the patient's file as one PDF (when the patient asks for their data). Audited."""
    from django.template.loader import render_to_string

    from apps.documents import pdf as engine
    from apps.prescriptions.models import Prescription, RxStatus

    patient = _patient(request, pk)
    ctx = {
        **_header_ctx(patient),
        "visits": Visit.objects.filter(patient=patient).select_related("vitals").order_by("created_at"),
        "prescriptions": Prescription.objects.filter(patient=patient, status=RxStatus.FINAL).prefetch_related("items"),
        "attachments": Attachment.objects.filter(patient=patient, is_archived=False),
        "fonts": engine.font_css(),
        "clinic": request.organization,
        "generated_at": timezone.now(),
    }
    try:
        data = engine.render_pdf(render_to_string("pdf/patient_summary.html", ctx), print_background=True,
                                 prefer_css_page_size=True)  # fmt: skip
    except Exception:
        return HttpResponse(_("مش قادر أعمل الـPDF دلوقتي — جرب تاني."), status=503)
    audit.log("patient.exported", request=request, target=patient, summary=patient.full_name)
    resp = HttpResponse(data, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="patient-{patient.file_number}.pdf"'
    return resp
