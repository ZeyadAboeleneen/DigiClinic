from decimal import Decimal, InvalidOperation

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Category

from .models import Quotation


def parse_price(raw: str):
    """Accepts '65', '5.2', '5,20', Arabic-Indic digits. Returns Decimal or None; raises ValidationError."""
    text = (raw or "").strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,"))
    if not text:
        return None
    text = text.replace(",", ".")
    try:
        value = Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation as e:
        raise forms.ValidationError(_("السعر لازم يكون رقم.")) from e
    if value <= 0:
        raise forms.ValidationError(_("السعر لازم يكون أكبر من صفر."))
    if value >= Decimal("10000000000"):
        raise forms.ValidationError(_("السعر كبير أوي."))
    return value


class MetaForm(forms.ModelForm):
    terms_text = forms.CharField(
        label=_("الشروط والملاحظات"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text=_("كل شرط في سطر."),
    )

    class Meta:
        model = Quotation
        fields = ["issue_date", "valid_until", "intro_text", "price_note", "internal_notes"]
        widgets = {
            "issue_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "valid_until": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "intro_text": forms.Textarea(attrs={"rows": 3}),
            "internal_notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["terms_text"].initial = "\n".join(self.instance.terms or [])

    def clean(self):
        cleaned = super().clean()
        a, b = cleaned.get("issue_date"), cleaned.get("valid_until")
        if a and b and b < a:
            self.add_error("valid_until", _("السريان لازم يكون بعد تاريخ العرض."))
        return cleaned

    def save(self, commit=True):
        self.instance.terms = [t.strip() for t in self.cleaned_data["terms_text"].splitlines() if t.strip()]
        return super().save(commit)


class CustomItemForm(forms.Form):
    description = forms.CharField(label=_("اسم الصنف"), max_length=250)
    unit_name = forms.CharField(label=_("الوحدة"), max_length=50)
    unit_price = forms.CharField(label=_("السعر"), required=False)
    category = forms.ModelChoiceField(label=_("القسم"), queryset=Category.objects.none(), required=False)
    add_to_catalog = forms.BooleanField(label=_("ضيفه للكتالوج كمان"), required=False)

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.for_org(organization).filter(is_active=True)
        self.fields["category"].empty_label = _("أصناف أخرى (آخر العرض)")

    def clean_unit_price(self):
        return parse_price(self.cleaned_data["unit_price"])

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("add_to_catalog") and not cleaned.get("category"):
            self.add_error("category", _("اختار القسم عشان الصنف يتضاف للكتالوج."))
        return cleaned
