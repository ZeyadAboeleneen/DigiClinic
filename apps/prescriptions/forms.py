from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.notifications.models import NotificationSettings

from .models import Drug, DrugCategory, PrescriptionSettings


class PrescriptionSettingsForm(forms.ModelForm):
    class Meta:
        model = PrescriptionSettings
        fields = [
            "page_size",
            "print_mode",
            "margin_top_mm",
            "margin_bottom_mm",
            "margin_right_mm",
            "margin_left_mm",
            "voice_dictation_enabled",
            "reception_can_reprint",
        ]

    def clean(self):
        data = super().clean()
        for f in ("margin_top_mm", "margin_bottom_mm", "margin_right_mm", "margin_left_mm"):
            if (data.get(f) or 0) > 120:
                self.add_error(f, _("الهامش كبير أوي."))
        return data


class RxNotifyForm(forms.ModelForm):
    class Meta:
        model = NotificationSettings
        fields = ["send_prescription_after_visit", "prescription_channel"]


class DrugForm(forms.ModelForm):
    aliases_text = forms.CharField(label=_("أسماء بالعربي"), required=False, help_text=_("افصل بفاصلة"))

    class Meta:
        model = Drug
        fields = ["name", "generic_name", "form", "strength", "default_instructions", "default_duration", "categories"]
        widgets = {
            "name": forms.TextInput(attrs={"dir": "ltr"}),
            "generic_name": forms.TextInput(attrs={"dir": "ltr"}),
            "form": forms.TextInput(attrs={"list": "drug-forms"}),
        }

    def __init__(self, *args, org=None, doctor=None, **kwargs):
        super().__init__(*args, **kwargs)
        # The doctor's own categories (in their order) + whatever the drug already has.
        allowed = DrugCategory.objects.filter(organization=org)
        if doctor is not None:
            current = list(self.instance.categories.values_list("pk", flat=True)) if self.instance.pk else []
            allowed = allowed.filter(Q(doctor_entries__doctor=doctor) | Q(pk__in=current)).distinct()
        self.fields["categories"].queryset = allowed.order_by("name")
        self.fields["categories"].required = False
        self.fields["categories"].widget = forms.CheckboxSelectMultiple()
        self.fields["categories"].widget.choices = self.fields["categories"].choices
        if self.instance.pk:
            self.fields["aliases_text"].initial = "، ".join(self.instance.aliases_ar or [])

    def save(self, commit=True):
        drug = super().save(commit=False)
        text = self.cleaned_data.get("aliases_text") or ""
        drug.aliases_ar = [a.strip() for a in text.replace("،", ",").split(",") if a.strip()]
        if commit:
            drug.save()
            self.save_m2m()
        return drug
