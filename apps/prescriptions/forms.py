from django import forms
from django.utils.translation import gettext_lazy as _

from apps.notifications.models import NotificationSettings

from .models import Drug, PrescriptionSettings


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
        fields = ["name", "generic_name", "form", "strength", "default_instructions", "default_duration"]
        widgets = {
            "name": forms.TextInput(attrs={"dir": "ltr"}),
            "generic_name": forms.TextInput(attrs={"dir": "ltr"}),
            "form": forms.TextInput(attrs={"list": "drug-forms"}),
        }

    def save(self, commit=True):
        drug = super().save(commit=False)
        text = self.cleaned_data.get("aliases_text") or ""
        drug.aliases_ar = [a.strip() for a in text.replace("،", ",").split(",") if a.strip()]
        if commit:
            drug.save()
        return drug
