"""Reception "today" screen (03 §3.4). Thin views: every state change goes through scheduling/billing services."""

import hashlib
import json
from collections import Counter

from django.contrib import messages
from django.db.models import Prefetch
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.accounts.permissions import has_perm, require_perm
from apps.audit import services as audit
from apps.billing import services as billing
from apps.billing.models import Payment
from apps.clinical.models import Visit, Vitals
from apps.core.timeutils import CAIRO
from apps.doctors.models import BookingMode, VisitType
from apps.doctors.services import get_doctor
from apps.notifications.templating import fmt_date, fmt_time
from apps.patients.models import Patient
from apps.scheduling import services as booking
from apps.scheduling.availability import periods_for
from apps.scheduling.models import Appointment, AppointmentStatus

from .forms import PaymentForm, VitalsForm

SHOWN_STATUSES = [
    AppointmentStatus.BOOKED,
    AppointmentStatus.CONFIRMED,
    AppointmentStatus.ARRIVED,
    AppointmentStatus.IN_CONSULTATION,
    AppointmentStatus.COMPLETED,
    AppointmentStatus.NO_SHOW,
]
LATE_AFTER_MINUTES = 5


def _today():
    return timezone.localtime(timezone.now(), CAIRO).date()


def _appointments(org, doctor, day):
    qs = (
        Appointment.objects.for_org(org)
        .filter(doctor=doctor, date=day, status__in=SHOWN_STATUSES)
        .select_related("patient", "visit_type")
        .prefetch_related(Prefetch("payments", queryset=Payment.objects.order_by("id")))
    )
    order = ("queue_number", "start_at") if doctor.booking_mode == BookingMode.QUEUE else ("start_at", "id")
    return list(qs.order_by(*order))


def _signature(appts) -> str:
    raw = "|".join(f"{a.pk}:{a.status}:{a.updated_at.timestamp()}:{len(a.payments.all())}" for a in appts)
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()[:16]


def _rows_ctx(request, doctor, day):
    appts = _appointments(request.organization, doctor, day)
    now = timezone.now()
    rows = []
    for a in appts:
        bal = billing.balance(a, list(a.payments.all()))
        waiting = int((now - a.arrived_at).total_seconds() // 60) if a.status == AppointmentStatus.ARRIVED else None
        late = (
            a.status in (AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED)
            and a.queue_number is None
            and (now - a.start_at).total_seconds() > LATE_AFTER_MINUTES * 60
        )
        rows.append(
            {
                "a": a,
                "balance": bal,
                "waiting": waiting,
                "late_minutes": int((now - a.start_at).total_seconds() // 60) if late else None,
            }
        )
    counts = Counter(a.status for a in appts)
    return {
        "rows": rows,
        "sig": _signature(appts),
        "counts": {
            "booked": counts[AppointmentStatus.BOOKED] + counts[AppointmentStatus.CONFIRMED],
            "arrived": counts[AppointmentStatus.ARRIVED],
            "in_consultation": counts[AppointmentStatus.IN_CONSULTATION],
            "completed": counts[AppointmentStatus.COMPLETED],
            "no_show": counts[AppointmentStatus.NO_SHOW],
        },
        "doctor": doctor,
        "day": day,
    }


@require_perm("reception.operate")
def today(request):
    doctor = get_doctor(request.organization)
    day = _today()
    ctx = _rows_ctx(request, doctor, day)
    periods = [f"{fmt_time(p.start_at)} – {fmt_time(p.end_at)}" for p in periods_for(doctor, day)]
    ctx.update(periods=periods, day_label=fmt_date(day))
    return render(request, "reception/today.html", ctx)


@require_perm("reception.operate")
def rows(request):
    """Polled every 5 s; 204 (no swap) when nothing changed since the client's signature."""
    doctor = get_doctor(request.organization)
    ctx = _rows_ctx(request, doctor, _today())
    if request.GET.get("sig") == ctx["sig"]:
        return HttpResponse(status=204)
    return render(request, "reception/partials/rows.html", ctx)


def _appt(request, pk):
    return get_object_or_404(
        Appointment.objects.for_org(request.organization).select_related("patient", "doctor", "visit_type"), pk=pk
    )


def _refresh(request, toast=None, error=None):
    """Re-render the rows (HTMX) — the modal closes client-side on success."""
    ctx = _rows_ctx(request, get_doctor(request.organization), _today())
    resp = render(request, "reception/partials/rows.html", ctx)
    if toast or error:
        resp["HX-Trigger"] = json.dumps({"toast": {"text": toast or error, "error": bool(error)}})
    return resp


# --- check-in (+ optional vitals) ------------------------------------------------------------


@require_perm("reception.operate")
def arrive(request, pk):
    appt = _appt(request, pk)
    can_vitals = has_perm(request.membership, "vitals.record")
    if request.method == "GET":
        return render(
            request, "reception/partials/arrive_modal.html", {"a": appt, "form": VitalsForm(), "can_vitals": can_vitals}
        )
    form = VitalsForm(request.POST) if can_vitals else None
    if form is not None and not form.is_valid():
        resp = render(request, "reception/partials/arrive_modal.html", {"a": appt, "form": form, "can_vitals": True})
        resp["HX-Retarget"] = "#modal"
        return resp
    try:
        booking.transition(appt, AppointmentStatus.ARRIVED, by=request.user)
    except booking.InvalidTransition as e:
        return _refresh(request, error=str(e))
    if form is not None:
        vitals = form.save(commit=False)
        if not vitals.is_empty:
            visit = Visit.for_appointment(appt)
            vitals.organization, vitals.visit, vitals.recorded_by = request.organization, visit, request.user
            vitals.save()
    return _refresh(request, toast=_("%(p)s وصل") % {"p": appt.patient.full_name})


@require_perm("vitals.record")
def vitals(request, pk):
    appt = _appt(request, pk)
    visit = Visit.for_appointment(appt)
    instance = Vitals.objects.filter(visit=visit).first()
    form = VitalsForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        v = form.save(commit=False)
        v.organization, v.visit, v.recorded_by = request.organization, visit, request.user
        v.save()
        return _refresh(request, toast=_("العلامات الحيوية اتسجلت"))
    resp = render(request, "reception/partials/vitals_modal.html", {"a": appt, "form": form})
    if request.method == "POST":
        resp["HX-Retarget"] = "#modal"
    return resp


# --- status steps ----------------------------------------------------------------------------

STEP_TARGETS = {
    "call": AppointmentStatus.IN_CONSULTATION,
    "complete": AppointmentStatus.COMPLETED,
}
UNDO_TARGETS = {
    AppointmentStatus.ARRIVED: AppointmentStatus.BOOKED,
    AppointmentStatus.IN_CONSULTATION: AppointmentStatus.ARRIVED,
}


@require_POST
@require_perm("reception.operate")
def step(request, pk, action):
    appt = _appt(request, pk)
    try:
        if action in STEP_TARGETS:
            booking.transition(appt, STEP_TARGETS[action], by=request.user)
            return _refresh(request)
        if action == "no_show":
            rebooked = booking.mark_no_show(appt, by=request.user)
            if rebooked:
                when = f"{fmt_date(rebooked.date)} {fmt_time(rebooked.start_at)}"
                return _refresh(request, toast=_("لم يحضر — اتحجزله %(w)s") % {"w": when})
            return _refresh(request, toast=_("لم يحضر"))
        if action == "undo":
            if appt.status == AppointmentStatus.NO_SHOW:
                booking.undo_no_show(appt, by=request.user)
            elif appt.status in UNDO_TARGETS:
                booking.transition(appt, UNDO_TARGETS[appt.status], by=request.user, notes=_("رجوع خطوة"))
            else:
                return _refresh(request, error=_("مفيش خطوة ترجعلها."))
            return _refresh(request, toast=_("رجعنا خطوة"))
    except (booking.InvalidTransition, booking.BookingError) as e:
        return _refresh(request, error=str(e))
    return HttpResponse(status=400)


# --- payments --------------------------------------------------------------------------------


@require_perm("payment.record")
def payment(request, pk):
    appt = _appt(request, pk)
    bal = billing.balance(appt)
    form = PaymentForm(request.POST or None, initial={"amount": bal.remaining or None, "method": "cash"})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            p = billing.record_payment(
                appt, amount=d["amount"], method=d["method"], note=d["note"], is_refund=d["is_refund"], by=request.user
            )
        except billing.PaymentError as e:
            form.add_error("amount", str(e))
        else:
            audit.log("payment.recorded", request=request, target=appt, summary=str(p))
            return _refresh(request, toast=_("اتسجل %(a)s") % {"a": p})
    resp = render(request, "reception/partials/payment_modal.html", {"a": appt, "form": form, "balance": bal})
    if request.method == "POST":
        resp["HX-Retarget"] = "#modal"
    return resp


# --- walk-in (12 §8) -------------------------------------------------------------------------


@require_perm("reception.operate")
def walk_in(request):
    doctor = get_doctor(request.organization)
    visit_types = VisitType.objects.for_org(request.organization).filter(doctor=doctor, is_active=True)
    if request.method == "POST":
        patient = get_object_or_404(Patient.objects.for_org(request.organization), pk=request.POST.get("patient"))
        visit_type = get_object_or_404(visit_types, pk=request.POST.get("visit_type"))
        overbook = request.POST.get("overbook") == "1" and has_perm(request.membership, "appointment.overbook")
        try:
            appt = booking.walk_in(
                patient=patient,
                doctor=doctor,
                visit_type=visit_type,
                by=request.user,
                overbook=overbook,
            )
        except booking.BookingError as e:
            messages.error(request, str(e))
            return redirect(f"{request.path}?q={patient.phone}")
        audit.log("appointment.walk_in", request=request, target=appt, summary=patient.full_name)
        messages.success(request, _("%(p)s اتسجل حضور مباشر") % {"p": patient.full_name})
        return redirect("reception:today")
    q = request.GET.get("q", "").strip()
    patients = Patient.objects.for_org(request.organization).filter(is_active=True).search(q)[:15] if q else []
    ctx = {"q": q, "patients": patients, "visit_types": visit_types}
    if request.htmx:
        return render(request, "reception/partials/walk_in_results.html", ctx)
    return render(request, "reception/walk_in.html", ctx)
