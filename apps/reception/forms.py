from django import forms
from django.utils.translation import gettext_lazy as _

from apps.billing.models import PaymentMethod
from apps.clinical.models import Vitals


class VitalsForm(forms.ModelForm):
    class Meta:
        model = Vitals
        fields = [
            "weight_kg",
            "height_cm",
            "bp_systolic",
            "bp_diastolic",
            "pulse",
            "temperature_c",
            "spo2",
            "blood_glucose",
        ]
        widgets = {f: forms.NumberInput(attrs={"inputmode": "decimal", "dir": "ltr"}) for f in fields}

    def clean(self):
        data = super().clean()
        sys_, dia = data.get("bp_systolic"), data.get("bp_diastolic")
        if bool(sys_) != bool(dia):
            raise forms.ValidationError(_("اكتب رقمين الضغط الاتنين."))
        if sys_ and dia and dia >= sys_:
            raise forms.ValidationError(_("الضغط الانبساطي لازم يكون أقل من الانقباضي."))
        return data


class PaymentForm(forms.Form):
    amount = forms.DecimalField(label=_("المبلغ"), min_value=0.01, max_digits=10, decimal_places=2)
    method = forms.ChoiceField(label=_("طريقة الدفع"), choices=PaymentMethod.choices)
    note = forms.CharField(label=_("ملاحظة"), max_length=200, required=False)
    is_refund = forms.BooleanField(label=_("استرداد"), required=False)
