import re

from django import forms
from django.utils.translation import gettext_lazy as _
from PIL import Image

from .models import Organization, OrganizationSettings

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
MAX_LOGO_BYTES = 5 * 1024 * 1024


class OrganizationForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ["name_ar", "name_en"]
        widgets = {"name_en": forms.TextInput(attrs={"dir": "ltr"})}


class CompanySettingsForm(forms.ModelForm):
    phones_text = forms.CharField(
        label=_("أرقام التليفون"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "dir": "ltr"}),
        help_text=_("رقم في كل سطر، بنفس الترتيب اللي هيظهر في العرض."),
    )

    class Meta:
        model = OrganizationSettings
        fields = [
            "logo",
            "clinic_name_ar",
            "clinic_name_en",
            "address_ar",
            "map_link",
            "working_hours_text",
            "primary_color",
            "dark_color",
            "neutral_color",
        ]
        widgets = {
            "logo": forms.FileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}),
            "clinic_name_en": forms.TextInput(attrs={"dir": "ltr"}),
            "map_link": forms.URLInput(attrs={"dir": "ltr"}),
            "primary_color": forms.TextInput(attrs={"type": "color"}),
            "dark_color": forms.TextInput(attrs={"type": "color"}),
            "neutral_color": forms.TextInput(attrs={"type": "color"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phones_text"].initial = "\n".join(self.instance.phones or [])

    def clean_logo(self):
        logo = self.cleaned_data.get("logo")
        if logo and hasattr(logo, "size") and "logo" in self.changed_data:
            if logo.size > MAX_LOGO_BYTES:
                raise forms.ValidationError(_("الحد الأقصى للصورة 5MB."))
            try:
                Image.open(logo).verify()
            except Exception as e:
                raise forms.ValidationError(_("الملف ده مش صورة سليمة.")) from e
            logo.seek(0)
        return logo

    def _clean_color(self, name):
        value = self.cleaned_data[name]
        if not HEX.match(value):
            raise forms.ValidationError(_("اللون لازم يكون بالشكل ده: ‎#AE171C"))
        return value.upper()

    def clean_primary_color(self):
        return self._clean_color("primary_color")

    def clean_dark_color(self):
        return self._clean_color("dark_color")

    def clean_neutral_color(self):
        return self._clean_color("neutral_color")

    def save(self, commit=True):
        self.instance.phones = [p.strip() for p in self.cleaned_data["phones_text"].splitlines() if p.strip()]
        return super().save(commit)
