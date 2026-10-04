from django import forms
from django.conf import settings
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm
from django.utils.translation import gettext_lazy as _

from apps.organizations.models import Role

from .models import User


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(
        label=_("الإيميل"),
        widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email", "dir": "ltr"}),
    )
    password = forms.CharField(
        label=_("كلمة المرور"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "dir": "ltr"}),
    )

    error_messages = {
        "invalid_login": _("الإيميل أو كلمة المرور غلط."),
        "inactive": _("الحساب ده متوقف."),
    }

    def clean_username(self):
        return self.cleaned_data["username"].lower()


class InviteForm(forms.Form):
    full_name = forms.CharField(label=_("الاسم"), max_length=150)
    email = forms.EmailField(label=_("الإيميل"), widget=forms.EmailInput(attrs={"dir": "ltr"}))
    role = forms.ChoiceField(label=_("الدور"), choices=())

    def __init__(self, *args, organization, allowed_roles, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        self.fields["role"].choices = [(r.value, r.label) for r in allowed_roles]
        self.fields["role"].initial = Role.SALES

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if self.organization.memberships.filter(user__email=email).exists():
            raise forms.ValidationError(_("المستخدم ده موجود بالفعل في الشركة."))
        return email


class AcceptInviteForm(forms.Form):
    full_name = forms.CharField(label=_("الاسم"), max_length=150)
    password1 = forms.CharField(
        label=_("كلمة المرور"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "dir": "ltr"}),
    )
    password2 = forms.CharField(
        label=_("تأكيد كلمة المرور"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "dir": "ltr"}),
    )

    def __init__(self, *args, email, **kwargs):
        super().__init__(*args, **kwargs)
        self.email = email
        self.fields["password1"].help_text = _("%(n)s حروف أو أرقام على الأقل.") % {"n": settings.PASSWORD_MIN_LENGTH}

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", _("كلمتين المرور مش زي بعض."))
        if p1:
            user = User(email=self.email, full_name=cleaned.get("full_name", ""))
            try:
                password_validation.validate_password(p1, user)
            except forms.ValidationError as e:
                self.add_error("password1", e)
        return cleaned


class RoleForm(forms.Form):
    role = forms.ChoiceField(label=_("الدور"), choices=())

    def __init__(self, *args, allowed_roles, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].choices = [(r.value, r.label) for r in allowed_roles]
