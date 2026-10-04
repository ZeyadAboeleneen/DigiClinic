"""Settings → "ملف المريض": the clinic's extra fields (03 §3.8 tab 7)."""

from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_POST

from apps.accounts.permissions import require_perm
from apps.audit import services as audit

from .models import FieldType, PatientFieldDefinition


class FieldDefinitionForm(forms.ModelForm):
    choices_text = forms.CharField(
        label=gettext_lazy("الاختيارات"),
        required=False,
        help_text=gettext_lazy("للنوع اختيار بس — افصل بفاصلة: A+, A-, B+"),
    )

    class Meta:
        model = PatientFieldDefinition
        fields = ["label_ar", "type", "scope"]

    def clean(self):
        data = super().clean()
        choices = [c.strip() for c in (data.get("choices_text") or "").replace("،", ",").split(",") if c.strip()]
        if data.get("type") == FieldType.CHOICE and len(choices) < 2:
            raise forms.ValidationError(_("اكتب اختيارين على الأقل."))
        data["choices"] = choices if data.get("type") == FieldType.CHOICE else []
        return data


def _unique_key(org, label):
    base = slugify(label, allow_unicode=False)[:40] or "field"
    key, n = base, 1
    while PatientFieldDefinition.objects.filter(organization=org, key=key).exists():
        n += 1
        key = f"{base}_{n}"
    return key


@require_perm("settings.manage")
def fields_settings(request):
    org = request.organization
    form = FieldDefinitionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.save(commit=False)
        d.organization = org
        d.key = _unique_key(org, d.label_ar)
        d.choices = form.cleaned_data["choices"]
        last = PatientFieldDefinition.objects.filter(organization=org).order_by("-order").first()
        d.order = (last.order + 1) if last else 0
        d.save()
        audit.log("settings.patient_field", request=request, summary=d.label_ar)
        messages.success(request, _("الحقل اتضاف."))
        return redirect("patients:fields_settings")
    defs = PatientFieldDefinition.objects.filter(organization=org)
    return render(request, "patients/settings_fields.html", {"form": form, "defs": defs, "tab": "patient_fields"})


@require_POST
@require_perm("settings.manage")
def field_toggle(request, pk):
    """Fields are never deleted (stored values would lose their label) — only switched off."""
    d = get_object_or_404(PatientFieldDefinition.objects.filter(organization=request.organization), pk=pk)
    d.is_active = not d.is_active
    d.save(update_fields=["is_active", "updated_at"])
    return redirect("patients:fields_settings")
