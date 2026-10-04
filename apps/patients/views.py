from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from apps.accounts.permissions import has_perm, require_perm
from apps.audit import services as audit
from apps.core.ratelimit import search_limit
from apps.prescriptions import views as rx_views

from . import services
from .forms import AllergyForm, ChronicConditionForm, PatientForm
from .models import Allergy, ChronicCondition, Patient


@require_perm("patient.view_basic")
@search_limit
def patient_list(request):
    q = request.GET.get("q", "")
    patients = Patient.objects.for_org(request.organization).filter(is_active=True).search(q)[:50]
    ctx = {"patients": patients, "q": q}
    if request.htmx:
        return render(request, "patients/partials/list_rows.html", ctx)
    return render(request, "patients/list.html", ctx)


@require_perm("patient.edit_basic")
def patient_add(request):
    form = PatientForm(request.POST or None, org=request.organization)
    duplicates = []
    if request.method == "POST" and form.is_valid():
        patient = services.create_patient(request.organization, form.save(commit=False), by=request.user)
        audit.log("patient.created", request=request, target=patient, summary=patient.full_name)
        messages.success(request, _("اتضاف رقم ملف %(n)s.") % {"n": patient.file_number})
        return redirect("patients:detail", pk=patient.pk)
    if request.method == "POST" and form.data.get("phone"):
        duplicates = services.possible_duplicates(request.organization, form.data["phone"])
    return render(request, "patients/form.html", {"form": form, "duplicates": duplicates, "mode": "add"})


@require_perm("patient.edit_basic")
def duplicates_check(request):
    phone = request.GET.get("phone", "")
    duplicates = services.possible_duplicates(request.organization, phone) if phone else Patient.objects.none()
    return render(request, "patients/partials/duplicates.html", {"duplicates": duplicates})


def _patient(request, pk):
    return get_object_or_404(Patient.objects.for_org(request.organization), pk=pk)


@require_perm("patient.view_basic")
def patient_detail(request, pk):
    patient = _patient(request, pk)
    can_view_medical = has_perm(request.membership, "patient.view_medical")
    ctx = {
        "patient": patient,
        "allergies": patient.allergies.all(),
        "can_view_medical": can_view_medical,
        "allergy_form": AllergyForm(),
        "condition_form": ChronicConditionForm(),
        "can_reprint": rx_views.can_print(request),
    }
    if can_view_medical:
        ctx["chronic_conditions"] = patient.chronic_conditions.all()
    # 03 §3.7: bookings (upcoming + past), messages (sent + received, 06 §6.7) and payments.
    ctx["appointments"] = patient.appointments.select_related("visit_type").order_by("-start_at")[:20]
    if has_perm(request.membership, "messages.view"):
        ctx["sent_messages"] = patient.scheduled_messages.order_by("-send_at")[:10]
        ctx["inbound_messages"] = patient.inbound_messages.all()[:10]
    if has_perm(request.membership, "payment.record"):
        from apps.billing.models import Payment

        ctx["payments"] = Payment.objects.filter(appointment__patient=patient).select_related("appointment")[:20]
    return render(request, "patients/detail.html", ctx)


@require_perm("patient.edit_basic")
def patient_edit(request, pk):
    patient = _patient(request, pk)
    form = PatientForm(request.POST or None, instance=patient, org=request.organization)
    if request.method == "POST" and form.is_valid():
        had_consent = Patient.objects.filter(pk=patient.pk, messaging_consent=True).exists()
        patient = form.save(commit=False)
        if patient.messaging_consent and not patient.consent_recorded_by_id:
            patient.consent_recorded_by = request.user
        patient.save()
        if patient.messaging_consent and not had_consent:
            from apps.notifications.services import on_consent_granted

            revived = on_consent_granted(patient)
            if revived:
                messages.info(request, _("%(n)s رسالة كانت متوقفة عشان الموافقة رجعت تتبعت.") % {"n": revived})
        audit.log("patient.edited", request=request, target=patient, summary=patient.full_name)
        messages.success(request, _("البيانات اتحفظت."))
        return redirect("patients:detail", pk=patient.pk)
    return render(request, "patients/form.html", {"form": form, "duplicates": [], "mode": "edit", "patient": patient})


@require_perm("patient.edit_basic")
def allergy_add(request, pk):
    patient = _patient(request, pk)
    form = AllergyForm(request.POST)
    if form.is_valid():
        a = form.save(commit=False)
        a.organization = request.organization
        a.patient = patient
        a.recorded_by = request.user
        a.save()
        audit.log("patient.allergy_added", request=request, target=patient, summary=a.name)
    return render(
        request, "patients/partials/allergies.html", {"patient": patient, "allergies": patient.allergies.all()}
    )


@require_perm("patient.edit_basic")
def allergy_delete(request, pk, allergy_pk):
    patient = _patient(request, pk)
    get_object_or_404(
        Allergy.objects.filter(organization=request.organization), pk=allergy_pk, patient=patient
    ).delete()
    return render(
        request, "patients/partials/allergies.html", {"patient": patient, "allergies": patient.allergies.all()}
    )


@require_perm("patient.edit_medical")
def condition_add(request, pk):
    patient = _patient(request, pk)
    form = ChronicConditionForm(request.POST)
    if form.is_valid():
        c = form.save(commit=False)
        c.organization = request.organization
        c.patient = patient
        c.recorded_by = request.user
        c.save()
        audit.log("patient.condition_added", request=request, target=patient, summary=c.name)
    return render(
        request,
        "patients/partials/conditions.html",
        {"patient": patient, "chronic_conditions": patient.chronic_conditions.all()},
    )


@require_perm("patient.edit_medical")
def condition_delete(request, pk, condition_pk):
    patient = _patient(request, pk)
    get_object_or_404(
        ChronicCondition.objects.filter(organization=request.organization), pk=condition_pk, patient=patient
    ).delete()
    return render(
        request,
        "patients/partials/conditions.html",
        {"patient": patient, "chronic_conditions": patient.chronic_conditions.all()},
    )


@require_perm("patient.merge")
def merge_search(request, pk):
    primary = _patient(request, pk)
    q = request.GET.get("q", "")
    candidates = (
        Patient.objects.for_org(request.organization).filter(is_active=True).exclude(pk=primary.pk).search(q)[:10]
    )
    return render(request, "patients/partials/merge_results.html", {"primary": primary, "candidates": candidates})


@require_perm("patient.merge")
def merge_confirm(request, pk, duplicate_pk):
    primary = _patient(request, pk)
    duplicate = _patient(request, duplicate_pk)
    try:
        services.merge_patients(primary, duplicate, by=request.user)
    except services.MergeError as e:
        messages.error(request, str(e))
        return redirect("patients:detail", pk=primary.pk)
    audit.log("patient.merged", request=request, target=primary, summary=f"{duplicate.full_name} → {primary.full_name}")
    messages.success(request, _("اتدمج %(d)s في %(p)s.") % {"d": duplicate.full_name, "p": primary.full_name})
    return redirect("patients:detail", pk=primary.pk)
