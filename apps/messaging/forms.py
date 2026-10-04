from django import forms
from django.utils.translation import gettext_lazy as _

from apps.organizations.models import OrganizationSettings

from .providers import SECURITY_CHOICES


class EmailSettingsForm(forms.Form):
    is_active = forms.BooleanField(label=_("تفعيل الإرسال بالإيميل"), required=False)
    display_name = forms.CharField(label=_("اسم المرسل اللي هيظهر"), max_length=100, required=False)
    from_email = forms.EmailField(label=_("إيميل المرسل"), widget=forms.EmailInput(attrs={"dir": "ltr"}))
    host = forms.CharField(label=_("سيرفر SMTP"), max_length=200, widget=forms.TextInput(attrs={"dir": "ltr"}))
    port = forms.IntegerField(label=_("Port"), min_value=1, max_value=65535, initial=587)
    security = forms.ChoiceField(label=_("التشفير"), choices=SECURITY_CHOICES, initial="tls")
    # Field names/attributes chosen so the browser doesn't autofill the Marsool login into them.
    smtp_user = forms.CharField(
        label=_("اسم المستخدم (الإيميل كامل)"),
        max_length=200,
        widget=forms.TextInput(attrs={"dir": "ltr", "autocomplete": "off", "data-lpignore": "true"}),
    )
    smtp_secret = forms.CharField(
        label=_("كلمة مرور التطبيق (App Password)"),
        required=False,
        strip=True,
        widget=forms.TextInput(
            attrs={"dir": "ltr", "autocomplete": "off", "data-lpignore": "true", "spellcheck": "false",
                   "placeholder": "xxxx xxxx xxxx xxxx", "style": "-webkit-text-security: disc"}
        ),
        help_text=_("سيبها فاضية لو مش عايز تغيّرها. بتتخزن متشفرة."),
    )  # fmt: skip

    def __init__(self, *args, has_password=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.has_password = has_password

    def clean_smtp_secret(self):
        return "".join(self.cleaned_data["smtp_secret"].split())  # Google shows it in groups of 4

    def clean(self):
        cleaned = super().clean()
        secret, host, user = cleaned.get("smtp_secret", ""), cleaned.get("host", ""), cleaned.get("smtp_user", "")
        if cleaned.get("is_active") and not (secret or self.has_password):
            self.add_error("smtp_secret", _("اكتب كلمة مرور التطبيق عشان تفعّل الإرسال."))
        if "gmail.com" in host:
            if user and "@" not in user:
                self.add_error("smtp_user", _("مع Gmail اسم المستخدم هو الإيميل كامل."))
            if secret and len(secret) != 16:
                self.add_error(
                    "smtp_secret",
                    _("App Password بتاع Gmail بيبقى 16 حرف بالظبط (اللي اتكتب %(n)s). مش كلمة سر الإيميل.")
                    % {"n": len(secret)},
                )
        return cleaned


class TemplatesForm(forms.ModelForm):
    class Meta:
        model = OrganizationSettings
        fields = ["default_email_subject", "default_email_body", "default_whatsapp_message"]
        widgets = {
            "default_email_body": forms.Textarea(attrs={"rows": 9}),
            "default_whatsapp_message": forms.Textarea(attrs={"rows": 6}),
        }


class SendForm(forms.Form):
    subject = forms.CharField(label=_("عنوان الإيميل"), max_length=250)
    body = forms.CharField(label=_("نص الإيميل"), widget=forms.Textarea(attrs={"rows": 9}))
    wa_body = forms.CharField(label=_("رسالة الواتساب"), required=False, widget=forms.Textarea(attrs={"rows": 5}))
    confirm = forms.BooleanField(label=_("راجعت الأصناف والأسعار بنفسي"), required=False)


class WhatsAppTestForm(forms.Form):
    to = forms.CharField(label=_("ابعت رسالة تجربة لرقم"), max_length=20, widget=forms.TextInput(attrs={"dir": "ltr"}))

    def clean_to(self):
        from apps.core.phones import to_e164

        return to_e164(self.cleaned_data["to"])
