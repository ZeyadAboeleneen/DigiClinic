from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.core.phones import to_e164

from . import templating
from .models import Event, NotificationSettings, NotificationTemplate

TIME_WIDGET = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")


class NotificationSettingsForm(forms.ModelForm):
    class Meta:
        model = NotificationSettings
        fields = [
            "is_enabled",
            "default_channel",
            "email_fallback",
            "quiet_start",
            "quiet_end",
            "quiet_policy",
            "urgent_until",
            "wa_min_gap_seconds",
        ]
        widgets = {"quiet_start": TIME_WIDGET, "quiet_end": TIME_WIDGET, "urgent_until": TIME_WIDGET}

    def clean_wa_min_gap_seconds(self):
        value = self.cleaned_data["wa_min_gap_seconds"]
        if value < 5:
            raise ValidationError(_("أقل من 5 ثواني بين الرسايل بيعرّض رقم العيادة للحظر."))
        return value


class TemplateForm(forms.ModelForm):
    class Meta:
        model = NotificationTemplate
        fields = [
            "name_ar",
            "is_enabled",
            "channel",
            "applies_to_mode",
            "offset_minutes",
            "min_lead_minutes",
            "body",
            "email_subject",
        ]
        widgets = {"body": forms.Textarea(attrs={"rows": 6, "dir": "auto"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.event != Event.REMINDER:
            del self.fields["offset_minutes"]

    def clean_body(self):
        body = self.cleaned_data["body"]
        templating.validate_body(body)
        return body

    def clean_email_subject(self):
        subject = self.cleaned_data["email_subject"]
        templating.validate_body(subject)
        return subject

    def clean_offset_minutes(self):
        value = self.cleaned_data.get("offset_minutes")
        if not value:
            raise ValidationError(_("لازم توقيت للتذكير."))
        return value


class TestSendForm(forms.Form):
    channel = forms.ChoiceField(label=_("القناة"), choices=[("whatsapp", _("واتساب")), ("email", _("إيميل"))])
    to = forms.CharField(label=_("ابعت لـ"), max_length=254)

    def clean(self):
        data = super().clean()
        to = (data.get("to") or "").strip()
        if data.get("channel") == "email":
            forms.EmailField().clean(to)
            data["to"] = to
        elif to:
            data["to"] = to_e164(to)
        return data
