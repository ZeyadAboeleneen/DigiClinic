from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.core.phones import to_e164, to_local

from .codes import CODE_RE
from .models import ChannelType, Contact, ContactChannel, Customer


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["name", "code", "kind", "parent", "city", "area", "address", "tax_id", "notes"]
        widgets = {
            "code": forms.TextInput(attrs={"dir": "ltr", "style": "text-transform:uppercase", "maxlength": 5}),
            "address": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "tax_id": forms.TextInput(attrs={"dir": "ltr", "inputmode": "numeric"}),
        }

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.organization = organization
        parents = Customer.objects.for_org(organization).filter(is_active=True).select_related("parent")
        if self.instance.pk:
            parents = parents.exclude(pk__in={self.instance.pk, *self.instance.descendant_ids()})
        self.fields["parent"].queryset = parents
        self.fields["parent"].label_from_instance = lambda c: c.path_display
        self.fields["parent"].empty_label = _("— مش تابع لحد —")

    def clean_code(self):
        code = self.cleaned_data["code"].strip().upper()
        if not code:
            return code  # auto-suggested on save
        if not CODE_RE.match(code):
            raise ValidationError(_("الكود لازم يكون من 2 لـ5 حروف إنجليزي."))
        dup = Customer.objects.filter(organization=self.instance.organization, code=code).exclude(pk=self.instance.pk)
        if dup.exists():
            raise ValidationError(_("الكود ده مستخدم لعميل تاني: %(name)s"), params={"name": dup.first().name})
        return code

    def clean_tax_id(self):
        value = "".join(ch for ch in self.cleaned_data["tax_id"] if ch.isdigit())
        if value and len(value) < 9:
            value = value.zfill(9)
        return value

    def validate_unique(self):
        # organization isn't a form field, so ModelForm skips the constraint; check it explicitly.
        super().validate_unique()
        qs = Customer.objects.filter(
            organization=self.instance.organization,
            name=" ".join(self.cleaned_data.get("name", "").split()),
            parent=self.cleaned_data.get("parent"),
        ).exclude(pk=self.instance.pk)
        if qs.exists():
            self.add_error("name", _("فيه عميل بنفس الاسم في نفس المكان."))


class ContactForm(forms.ModelForm):
    """Contact + its primary WhatsApp / phone / email in one quick form."""

    whatsapp = forms.CharField(
        label=_("واتساب"), required=False, widget=forms.TextInput(attrs={"dir": "ltr", "inputmode": "tel"})
    )
    phone = forms.CharField(
        label=_("تليفون"), required=False, widget=forms.TextInput(attrs={"dir": "ltr", "inputmode": "tel"})
    )
    email = forms.CharField(label=_("إيميل"), required=False, widget=forms.EmailInput(attrs={"dir": "ltr"}))

    class Meta:
        model = Contact
        fields = ["name", "salutation", "job_title", "department", "preferred_channel", "is_primary"]

    CHANNEL_FIELDS = (("whatsapp", ChannelType.WHATSAPP), ("phone", ChannelType.PHONE), ("email", ChannelType.EMAIL))

    def __init__(self, *args, customer, **kwargs):
        super().__init__(*args, **kwargs)
        self.customer = customer
        self.instance.customer = customer
        self.instance.organization_id = customer.organization_id
        if self.instance.pk:
            for field, type_ in self.CHANNEL_FIELDS:
                ch = self.instance.primary_channel(type_)
                if ch:
                    self.fields[field].initial = ch.value if type_ == ChannelType.EMAIL else to_local(ch.value)
        elif not customer.contacts.exists():
            self.fields["is_primary"].initial = True

    def _clean_phone(self, field):
        raw = self.cleaned_data.get(field, "").strip()
        return to_e164(raw) if raw else ""

    def clean_whatsapp(self):
        return self._clean_phone("whatsapp")

    def clean_phone(self):
        return self._clean_phone("phone")

    def clean_email(self):
        value = self.cleaned_data.get("email", "").strip().lower()
        if value:
            validate_email(value)
        return value

    def clean(self):
        cleaned = super().clean()
        pref = cleaned.get("preferred_channel")
        if pref in ("whatsapp", "both") and not cleaned.get("whatsapp") and "whatsapp" not in self.errors:
            self.add_error("whatsapp", _("اكتب رقم الواتساب، أو غيّر طريقة الإرسال المفضلة."))
        if pref in ("email", "both") and not cleaned.get("email") and "email" not in self.errors:
            self.add_error("email", _("اكتب الإيميل، أو غيّر طريقة الإرسال المفضلة."))
        return cleaned

    @transaction.atomic
    def save(self, commit=True):
        others = Contact.objects.filter(customer=self.customer, is_active=True, is_primary=True)
        if self.instance.pk:
            others = others.exclude(pk=self.instance.pk)
        if not others.exists() and self.instance.is_active:
            self.instance.is_primary = True  # a customer always has a default contact
        contact = super().save(commit=True)
        for field, type_ in self.CHANNEL_FIELDS:
            value = self.cleaned_data.get(field, "")
            current = contact.primary_channel(type_)
            if not value:
                if current:
                    current.delete()
                continue
            if current and current.value == value:
                continue
            existing = contact.channels.filter(type=type_, value=value).first()
            if existing:
                existing.is_primary = True
                existing.save()
                if current:
                    current.delete()
            elif current:
                current.value = value
                current.is_verified = False
                current.save()
            else:
                ContactChannel.objects.create(
                    organization_id=contact.organization_id, contact=contact, type=type_, value=value, is_primary=True
                )
        return contact


class ChannelForm(forms.Form):
    """Extra (non-primary) channel, e.g. a second WhatsApp number."""

    type = forms.ChoiceField(label=_("النوع"), choices=ChannelType.choices)
    value = forms.CharField(label=_("الرقم / الإيميل"), widget=forms.TextInput(attrs={"dir": "ltr"}))
    label = forms.CharField(label=_("وصف"), required=False, max_length=50)

    def __init__(self, *args, contact, **kwargs):
        super().__init__(*args, **kwargs)
        self.contact = contact

    def clean(self):
        cleaned = super().clean()
        type_, value = cleaned.get("type"), (cleaned.get("value") or "").strip()
        if not type_ or not value:
            return cleaned
        try:
            if type_ == ChannelType.EMAIL:
                value = value.lower()
                validate_email(value)
            else:
                value = to_e164(value)
        except ValidationError as e:
            self.add_error("value", e)
            return cleaned
        if self.contact.channels.filter(type=type_, value=value).exists():
            self.add_error("value", _("الوسيلة دي متسجلة بالفعل."))
        cleaned["value"] = value
        return cleaned

    def save(self):
        contact = self.contact
        has_primary = contact.channels.filter(type=self.cleaned_data["type"], is_primary=True).exists()
        return ContactChannel.objects.create(
            organization_id=contact.organization_id,
            contact=contact,
            type=self.cleaned_data["type"],
            value=self.cleaned_data["value"],
            label=self.cleaned_data["label"],
            is_primary=not has_primary,
        )
