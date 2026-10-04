from django import forms
from django.utils.translation import gettext_lazy as _

from .models import AttachmentKind


class AttachmentForm(forms.Form):
    file = forms.FileField(
        label=_("الملف"),
        widget=forms.ClearableFileInput(attrs={"accept": "application/pdf,image/jpeg,image/png"}),
    )
    kind = forms.ChoiceField(label=_("النوع"), choices=AttachmentKind.choices, initial=AttachmentKind.LAB)
    title = forms.CharField(label=_("العنوان"), max_length=150, required=False)
    taken_on = forms.DateField(label=_("التاريخ"), required=False, widget=forms.DateInput(attrs={"type": "date"}))
