from datetime import date, datetime, timedelta

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.accounts.permissions import has_perm, require_perm
from apps.audit import services as audit
from apps.core.ratelimit import search_limit
from apps.core.timeutils import CAIRO, WEEKDAY_LABELS, weekday_egypt
from apps.doctors.models import BookingMode, ScheduleException, ScheduleExceptionKind, VisitType
from apps.doctors.services import get_doctor
from apps.notifications.models import Event, MessageStatus
from apps.notifications.templating import fmt_time
from apps.patients import services as patient_services
from apps.patients.forms import PatientForm
from apps.patients.models import Patient

from . import services
from .availability import periods_for
from .models import ACTIVE_STATUSES, Appointment, AppointmentStatus


def _parse_day(raw) -> date:
    if raw:
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            pass
    return date.today()


# --- booking screen --------------------------------------------------------------------------


@require_perm("appointment.book")
def booking_home(request):
    return render(request, "scheduling/booking_home.html", {})


@require_perm("appointment.book")
@search_limit
def booking_search(request):
    q = request.GET.get("q", "").strip()
    patients = Patient.objects.for_org(request.organization).filter(is_active=True).search(q)[:8] if q else []
    digits = sum(ch.isdigit() for ch in q)
    looks_like_phone = bool(q) and digits >= max(len(q) - 2, 1)
    initial = {"phone": q} if looks_like_phone else {"full_name": q}
    form = PatientForm(initial=initial) if q else None
    return render(request, "scheduling/partials/search_results.html", {"q": q, "patients": patients, "form": form})


@require_perm("appointment.book")
def booking_new_patient(request):
    form = PatientForm(request.POST)
    if form.is_valid():
        patient = patient_services.create_patient(request.organization, form.save(commit=False), by=request.user)
        audit.log("patient.created", request=request, target=patient, summary=patient.full_name)
        return redirect("scheduling:booking_patient", patient_pk=patient.pk)
    return render(request, "scheduling/partials/new_patient_form.html", {"form": form})


def _patient(request, pk):
    return get_object_or_404(Patient.objects.for_org(request.organization), pk=pk)


@require_perm("appointment.book")
def booking_patient(request, patient_pk):
    patient = _patient(request, patient_pk)
    doctor = get_doctor(request.organization)
    visit_types = VisitType.objects.filter(doctor=doctor, is_active=True)
    upcoming = (
        Appointment.objects.for_org(request.organization)
        .filter(patient=patient, status__in=ACTIVE_STATUSES, start_at__gte=datetime.now(CAIRO))
        .select_related("visit_type")
        .order_by("start_at")[:3]
    )
    day = _parse_day(request.GET.get("day"))
    ctx = {
        "patient": patient,
        "doctor": doctor,
        "visit_types": visit_types,
        "upcoming": upcoming,
        "visit_type_id": request.GET.get("visit_type") or (visit_types.first().pk if visit_types.first() else ""),
        "day": day.isoformat(),
        "prev_day": (day - timedelta(days=1)).isoformat(),
        "next_day": (day + timedelta(days=1)).isoformat(),
    }
    return render(request, "scheduling/booking_patient.html", ctx)


@require_perm("appointment.book")
def booking_slots(request, patient_pk):
    patient = _patient(request, patient_pk)
    doctor = get_doctor(request.organization)
    day = _parse_day(request.GET.get("day"))
    visit_type = get_object_or_404(VisitType.objects.filter(doctor=doctor), pk=request.GET.get("visit_type"))
    ctx = {"patient": patient, "doctor": doctor, "visit_type": visit_type, "day": day}
    if doctor.booking_mode == BookingMode.SLOTS:
        ctx["slots"] = services.free_slots(doctor, visit_type, day)
    else:
        ctx["periods"] = services.queue_availability(doctor, day)
    return render(request, "scheduling/partials/slots.html", ctx)


@require_perm("appointment.book")
def booking_confirm(request, patient_pk):
    patient = _patient(request, patient_pk)
    doctor = get_doctor(request.organization)
    visit_type = get_object_or_404(VisitType.objects.filter(doctor=doctor), pk=request.POST.get("visit_type"))
    overbook = request.POST.get("overbook") == "1" and has_perm(request.membership, "appointment.overbook")
    try:
        if doctor.booking_mode == BookingMode.SLOTS:
            start_at = datetime.fromisoformat(request.POST["start_at"])
            if start_at.tzinfo is None:
                start_at = start_at.replace(tzinfo=CAIRO)
            appt = services.book(
                patient=patient,
                doctor=doctor,
                visit_type=visit_type,
                by=request.user,
                start_at=start_at,
                overbook=overbook,
            )
        else:
            day = _parse_day(request.POST.get("day"))
            appt = services.book(
                patient=patient,
                doctor=doctor,
                visit_type=visit_type,
                by=request.user,
                day=day,
                period_key=request.POST.get("period_key"),
                overbook=overbook,
            )
    except services.BookingError as e:
        messages.error(request, str(e))
        return redirect("scheduling:booking_patient", patient_pk=patient.pk)

    audit.log(
        "appointment.booked", request=request, target=appt, summary=f"{patient.full_name} — {appt.visit_type.name_ar}"
    )
    label = f"#{appt.queue_number}" if appt.queue_number else appt.start_at.astimezone(CAIRO).strftime("%Y/%m/%d %H:%M")
    messages.success(request, _("اتحجز لـ%(p)s — %(w)s") % {"p": patient.full_name, "w": label})
    scheduled = appt.messages.filter(status=MessageStatus.PENDING).order_by("send_at")
    if scheduled:
        parts = [
            _("تأكيد الآن")
            if m.event == Event.BOOKING_CONFIRMED
            else _("تذكير %(t)s") % {"t": m.send_at.astimezone(CAIRO).strftime("%m/%d %H:%M")}
            for m in scheduled
        ]
        messages.info(request, _("الرسايل: %(l)s") % {"l": " · ".join(parts)})
    elif not patient.messaging_consent:
        messages.warning(request, _("مفيش رسايل هتتبعت — المريض مش موافق على الرسايل."))
    return redirect("scheduling:booking_home")


# --- day calendar ------------------------------------------------------------------------------


@require_perm("appointment.view")
def appointments_day(request):
    doctor = get_doctor(request.organization)
    day = _parse_day(request.GET.get("date"))
    appts = (
        Appointment.objects.for_org(request.organization)
        .filter(doctor=doctor, date=day)
        .exclude(status__in=[AppointmentStatus.CANCELLED, AppointmentStatus.RESCHEDULED])
        .select_related("patient", "visit_type")
        .order_by("start_at")
    )
    ctx = {
        "doctor": doctor,
        "day": day,
        "prev_day": day - timedelta(days=1),
        "next_day": day + timedelta(days=1),
        "appointments": appts,
    }
    return render(request, "scheduling/appointments_day.html", ctx)


def _appt(request, pk):
    return get_object_or_404(Appointment.objects.for_org(request.organization), pk=pk)


@require_perm("appointment.reschedule")
def appointment_confirm(request, pk):
    appt = _appt(request, pk)
    try:
        services.transition(appt, AppointmentStatus.CONFIRMED, by=request.user)
    except services.InvalidTransition as e:
        messages.error(request, str(e))
    return redirect(f"{reverse('scheduling:appointments_day')}?date={appt.date}")


@require_perm("appointment.cancel")
def appointment_cancel(request, pk):
    appt = _appt(request, pk)
    try:
        services.cancel(appt, by=request.user, reason=request.POST.get("reason", ""))
        audit.log("appointment.cancelled", request=request, target=appt, summary=appt.patient.full_name)
        messages.success(request, _("اتلغى الحجز."))
    except services.BookingError as e:
        messages.error(request, str(e))
    return redirect(f"{reverse('scheduling:appointments_day')}?date={appt.date}")


# --- affected appointments (closing a day) ------------------------------------------------------


@require_perm("schedule.manage")
def affected_appointments(request):
    doctor = get_doctor(request.organization)
    day = _parse_day(request.GET.get("date"))
    affected = services.affected_by_closing(doctor, day)
    return render(request, "scheduling/affected.html", {"doctor": doctor, "day": day, "affected": affected})


@require_perm("schedule.manage")
def affected_reschedule(request, pk):
    appt = _appt(request, pk)
    try:
        new_appt = services.reschedule_to_suggestion(appt, by=request.user)
        audit.log("appointment.rescheduled", request=request, target=new_appt, summary=appt.patient.full_name)
        messages.success(request, _("اتأجل حجز %(p)s.") % {"p": appt.patient.full_name})
    except services.BookingError as e:
        messages.error(request, str(e))
    return redirect(f"{reverse('scheduling:affected')}?date={appt.date}")


# --- week calendar (03 §3.3) -----------------------------------------------------------------

STATUS_ICONS = {
    AppointmentStatus.BOOKED: "○",
    AppointmentStatus.CONFIRMED: "✓",
    AppointmentStatus.ARRIVED: "●",
    AppointmentStatus.IN_CONSULTATION: "▶",
    AppointmentStatus.COMPLETED: "✔",
    AppointmentStatus.NO_SHOW: "✕",
}


def week_start(day: date) -> date:
    """Egyptian week: Saturday → Friday."""
    return day - timedelta(days=weekday_egypt(day))


@require_perm("appointment.view")
def appointments_week(request):
    doctor = get_doctor(request.organization)
    start = week_start(_parse_day(request.GET.get("date")))
    days = [start + timedelta(days=i) for i in range(7)]
    appts = (
        Appointment.objects.for_org(request.organization)
        .filter(doctor=doctor, date__gte=days[0], date__lte=days[-1])
        .exclude(status__in=[AppointmentStatus.CANCELLED, AppointmentStatus.RESCHEDULED])
        .select_related("patient", "visit_type")
        .order_by("start_at")
    )
    exceptions = {}
    for exc in ScheduleException.objects.filter(doctor=doctor, date__gte=days[0], date__lte=days[-1]):
        exceptions.setdefault(exc.date, []).append(exc)
    by_day = {d: [] for d in days}
    for a in appts:
        a.icon = STATUS_ICONS.get(a.status, "")
        by_day[a.date].append(a)
    today = timezone.localtime(timezone.now(), CAIRO).date()
    columns = [
        {
            "day": d,
            "label": WEEKDAY_LABELS[weekday_egypt(d)],
            "periods": [f"{fmt_time(p.start_at)} – {fmt_time(p.end_at)}" for p in periods_for(doctor, d)],
            "exceptions": exceptions.get(d, []),
            "appointments": by_day[d],
            "is_today": d == today,
            "is_past": d < today,
        }
        for d in days
    ]
    ctx = {
        "doctor": doctor,
        "columns": columns,
        "start": days[0],
        "end": days[-1],
        "prev_week": days[0] - timedelta(days=7),
        "next_week": days[0] + timedelta(days=7),
        "today": today,
    }
    return render(request, "scheduling/appointments_week.html", ctx)


@require_perm("appointment.reschedule")
def reschedule_options(request, pk):
    """Modal for moving an appointment (drag to another day, or the "تأجيل" button): that day's free times."""
    appt = _appt(request, pk)
    if not appt.is_active or appt.status not in (AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED):
        return render(request, "scheduling/partials/reschedule_modal.html",
                      {"a": appt, "error": _("الحجز ده مينفعش يتأجل دلوقتي.")})  # fmt: skip
    doctor = appt.doctor
    day = _parse_day(request.GET.get("day") or appt.date.isoformat())
    ctx = {"a": appt, "day": day, "doctor": doctor}
    if doctor.booking_mode == BookingMode.SLOTS:
        ctx["slots"] = services.free_slots(doctor, appt.visit_type, day)
    else:
        ctx["periods"] = services.queue_availability(doctor, day)
    return render(request, "scheduling/partials/reschedule_modal.html", ctx)


@require_POST
@require_perm("appointment.reschedule")
def reschedule_confirm(request, pk):
    appt = _appt(request, pk)
    week = request.POST.get("week") or appt.date.isoformat()
    try:
        if appt.doctor.booking_mode == BookingMode.SLOTS:
            start_at = datetime.fromisoformat(request.POST["start_at"])
            if start_at.tzinfo is None:
                start_at = start_at.replace(tzinfo=CAIRO)
            new = services.reschedule(appt, by=request.user, new_start_at=start_at, reason=_("تأجيل من التقويم"))
        else:
            new = services.reschedule(appt, by=request.user, new_day=_parse_day(request.POST.get("day")),
                                      new_period_key=request.POST.get("period_key"),
                                      reason=_("تأجيل من التقويم"))  # fmt: skip
    except (KeyError, ValueError, services.BookingError) as e:
        messages.error(request, str(e) or _("اختار ميعاد."))
        return redirect(f"{reverse('scheduling:appointments_week')}?date={week}")
    audit.log("appointment.rescheduled", request=request, target=new, summary=appt.patient.full_name)
    when = f"#{new.queue_number}" if new.queue_number else new.start_at.astimezone(CAIRO).strftime("%Y/%m/%d %H:%M")
    messages.success(request, _("اتأجل لـ%(w)s — والمريض هتوصله رسالة بالتعديل.") % {"w": when})
    return redirect(f"{reverse('scheduling:appointments_week')}?date={new.date.isoformat()}")


@require_POST
@require_perm("schedule.manage")
def close_day(request):
    """ "قفل يوم" from the calendar → closed exception → the affected-bookings screen if anything is booked (12 §5)."""
    doctor = get_doctor(request.organization)
    day = _parse_day(request.POST.get("date"))
    _exc, created = ScheduleException.objects.get_or_create(
        organization=request.organization, doctor=doctor, date=day, kind=ScheduleExceptionKind.CLOSED,
        defaults={"reason": request.POST.get("reason", "")[:150]},
    )  # fmt: skip
    if created:
        audit.log("settings.doctor", request=request, summary=f"{_('قفل يوم')}: {day}")
    if services.affected_by_closing(doctor, day):
        return redirect(f"{reverse('scheduling:affected')}?date={day.isoformat()}")
    messages.success(request, _("اليوم اتقفل."))
    return redirect(f"{reverse('scheduling:appointments_week')}?date={day.isoformat()}")
