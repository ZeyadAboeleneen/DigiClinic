from datetime import date

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.phones import to_e164

from . import custom_fields
from .models import Allergy, ChronicCondition, FieldScope, Patient


class PatientForm(forms.ModelForm):
    age_years = forms.IntegerField(
        label=_("السن (تقريبي)"),
        required=False,
        min_value=0,
        max_value=130,
        help_text=_("لو مش عارفة تاريخ الميلاد بالظبط، اكتبي السن بس."),
    )
    whatsapp_same_as_phone = forms.BooleanField(label=_("نفس رقم الموبايل"), required=False, initial=True)

    class Meta:
        model = Patient
        fields = [
            "full_name",
            "gender",
            "date_of_birth",
            "phone",
            "whatsapp",
            "email",
            "guardian_name",
            "address",
            "occupation",
            "preferred_channel",
            "messaging_consent",
            "notes",
        ]
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "phone": forms.TextInput(attrs={"dir": "ltr"}),
            "whatsapp": forms.TextInput(attrs={"dir": "ltr"}),
            "email": forms.EmailInput(attrs={"dir": "ltr"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, org=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Clinic-defined extra fields (scope=patient); quick-add forms built without `org` skip them.
        self.custom_defs = custom_fields.definitions(org, FieldScope.PATIENT)
        custom_fields.add_to_form(self, self.custom_defs, self.instance.custom_fields)
        self.fields["whatsapp"].required = False
        if self.instance.pk and self.instance.whatsapp == self.instance.phone:
            self.fields["whatsapp_same_as_phone"].initial = True

    def clean_phone(self):
        return to_e164(self.cleaned_data["phone"])

    def clean_whatsapp(self):
        value = self.cleaned_data.get("whatsapp")
        return to_e164(value) if value else ""

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("whatsapp_same_as_phone"):
            cleaned["whatsapp"] = cleaned.get("phone", "")
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        age = self.cleaned_data.get("age_years")
        if age is not None and not self.cleaned_data.get("date_of_birth"):
            today = date.today()
            try:
                instance.date_of_birth = today.replace(year=today.year - age)
            except ValueError:  # today is Feb 29 and the target year has no such day
                instance.date_of_birth = today.replace(month=2, day=28, year=today.year - age)
            instance.dob_is_estimated = True
        elif self.cleaned_data.get("date_of_birth"):
            instance.dob_is_estimated = False
        if self.custom_defs:
            instance.custom_fields = custom_fields.collect(self, self.custom_defs, instance.custom_fields)
        if commit:
            instance.save()
        return instance

    @property
    def custom_bound_fields(self):
        return custom_fields.bound_fields(self, self.custom_defs)


class AllergyForm(forms.ModelForm):
    class Meta:
        model = Allergy
        fields = ["name", "severity", "notes"]


class ChronicConditionForm(forms.ModelForm):
    class Meta:
        model = ChronicCondition
        fields = ["name", "notes"]


class MergeForm(forms.Form):
    duplicate_id = forms.IntegerField(widget=forms.HiddenInput)
