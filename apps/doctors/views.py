from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from apps.accounts.permissions import require_perm
from apps.audit import services as audit
from apps.core.timeutils import WEEKDAY_LABELS

from .forms import DoctorForm, ScheduleExceptionForm, VisitTypeForm, WorkingPeriodForm
from .models import Doctor, ScheduleException, VisitType, WorkingPeriod


def _get_doctor(org):
    """Single-doctor v1: the UI always edits the organization's one Doctor row, created on first visit."""
    doctor, _created = Doctor.objects.get_or_create(
        organization=org, defaults={"name_ar": org.settings.clinic_name_ar or org.name_ar}
    )
    return doctor


def _schedule_ctx(request, doctor, form=None):
    periods = WorkingPeriod.objects.filter(doctor=doctor).order_by("weekday", "start_time")
    by_weekday = [(i, label, [p for p in periods if p.weekday == i]) for i, label in enumerate(WEEKDAY_LABELS)]
    exceptions = ScheduleException.objects.filter(doctor=doctor).order_by("date")
    return {
        "doctor": doctor,
        "form": form or DoctorForm(instance=doctor),
        "by_weekday": by_weekday,
        "exceptions": exceptions,
        "period_form": WorkingPeriodForm(),
        "exception_form": ScheduleExceptionForm(),
        "tab": "schedule",
    }


@require_perm("schedule.manage")
def schedule_settings(request):
    doctor = _get_doctor(request.organization)
    form = DoctorForm(request.POST or None, instance=doctor) if request.method == "POST" else None
    if form and form.is_valid():
        form.save()
        audit.log("settings.doctor", request=request, fields=sorted(form.changed_data))
        messages.success(request, _("بيانات الدكتور اتحفظت."))
        return redirect("doctors:schedule")
    return render(request, "doctors/settings_schedule.html", _schedule_ctx(request, doctor, form))


def _periods_partial(request, doctor):
    return render(request, "doctors/partials/periods.html", _schedule_ctx(request, doctor))


@require_perm("schedule.manage")
def period_add(request):
    doctor = _get_doctor(request.organization)
    form = WorkingPeriodForm(request.POST)
    if form.is_valid():
        period = form.save(commit=False)
        period.organization = request.organization
        period.doctor = doctor
        try:
            period.full_clean()
        except ValidationError as e:
            for field, errs in e.message_dict.items():
                form.add_error(field if field in form.fields else None, errs)
        else:
            period.save()
            audit.log("settings.doctor", request=request, summary=_("إضافة فترة عمل"))
    if not form.is_valid():
        ctx = _schedule_ctx(request, doctor)
        ctx["period_form"] = form
        return render(request, "doctors/partials/periods.html", ctx)
    return _periods_partial(request, doctor)


@require_perm("schedule.manage")
def period_delete(request, pk):
    doctor = _get_doctor(request.organization)
    get_object_or_404(WorkingPeriod.objects.filter(organization=request.organization), pk=pk, doctor=doctor).delete()
    audit.log("settings.doctor", request=request, summary=_("مسح فترة عمل"))
    return _periods_partial(request, doctor)


def _exceptions_partial(request, doctor):
    return render(request, "doctors/partials/exceptions.html", _schedule_ctx(request, doctor))


@require_perm("schedule.manage")
def exception_add(request):
    doctor = _get_doctor(request.organization)
    form = ScheduleExceptionForm(request.POST)
    if form.is_valid():
        exc = form.save(commit=False)
        exc.organization = request.organization
        exc.doctor = doctor
        exc.save()
        audit.log("settings.doctor", request=request, summary=f"{_('استثناء جدول')}: {exc.date}")
    if not form.is_valid():
        ctx = _schedule_ctx(request, doctor)
        ctx["exception_form"] = form
        return render(request, "doctors/partials/exceptions.html", ctx)
    return _exceptions_partial(request, doctor)


@require_perm("schedule.manage")
def exception_delete(request, pk):
    doctor = _get_doctor(request.organization)
    get_object_or_404(
        ScheduleException.objects.filter(organization=request.organization), pk=pk, doctor=doctor
    ).delete()
    audit.log("settings.doctor", request=request, summary=_("مسح استثناء جدول"))
    return _exceptions_partial(request, doctor)


# --- visit types -----------------------------------------------------------------------------


def _visit_types_ctx(request, doctor):
    return {
        "doctor": doctor,
        "visit_types": VisitType.objects.filter(doctor=doctor).order_by("order", "id"),
        "form": VisitTypeForm(),
        "tab": "visit_types",
    }


@require_perm("schedule.manage")
def visit_types_settings(request):
    doctor = _get_doctor(request.organization)
    return render(request, "doctors/settings_visit_types.html", _visit_types_ctx(request, doctor))


def _visit_types_partial(request, doctor):
    return render(request, "doctors/partials/visit_types.html", _visit_types_ctx(request, doctor))


@require_perm("schedule.manage")
def visit_type_add(request):
    doctor = _get_doctor(request.organization)
    form = VisitTypeForm(request.POST)
    if form.is_valid():
        vt = form.save(commit=False)
        vt.organization = request.organization
        vt.doctor = doctor
        vt.order = VisitType.objects.filter(doctor=doctor).count()
        vt.save()
        audit.log("settings.visit_type", request=request, summary=vt.name_ar)
        return _visit_types_partial(request, doctor)
    ctx = _visit_types_ctx(request, doctor)
    ctx["form"] = form
    return render(request, "doctors/partials/visit_types.html", ctx)


@require_perm("schedule.manage")
def visit_type_delete(request, pk):
    doctor = _get_doctor(request.organization)
    get_object_or_404(VisitType.objects.filter(organization=request.organization), pk=pk, doctor=doctor).delete()
    audit.log("settings.visit_type", request=request, summary=_("مسح نوع زيارة"))
    return _visit_types_partial(request, doctor)
