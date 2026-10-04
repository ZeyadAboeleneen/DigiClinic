"""Clinic-defined extra fields (`PatientFieldDefinition`, 02 §2.3): turned into form fields and validated values
stored in a JSON `custom_fields` dict (on `Patient` for scope=patient, on `clinical.Visit` for scope=visit)."""

from datetime import date

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from .models import FieldScope, FieldType, PatientFieldDefinition

PREFIX = "cf_"


def definitions(org, scope):
    if org is None:
        return []
    return list(PatientFieldDefinition.objects.filter(organization=org, scope=scope, is_active=True))


def form_field(d) -> forms.Field:
    common = {"label": d.label_ar, "required": False}
    if d.type == FieldType.NUMBER:
        return forms.DecimalField(widget=forms.NumberInput(attrs={"dir": "ltr", "step": "any"}), **common)
    if d.type == FieldType.DATE:
        return forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), **common)
    if d.type == FieldType.CHOICE:
        return forms.ChoiceField(choices=[("", "—")] + [(c, c) for c in d.choices or []], **common)
    if d.type == FieldType.BOOL:
        return forms.BooleanField(**common)
    return forms.CharField(max_length=500, **common)


def to_json(d, value):
    """Cleaned form value → JSON-safe value (None for empty)."""
    if value in (None, "") or (d.type == FieldType.BOOL and value is False):
        return None if d.type != FieldType.BOOL else False
    if d.type == FieldType.NUMBER:
        return float(value)
    if d.type == FieldType.DATE:
        return value.isoformat()
    return value


def from_json(d, value):
    if d.type == FieldType.DATE and value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return value


def clean_value(d, raw):
    """Validate a single raw string (autosave) against its definition."""
    field = form_field(d)
    if d.type == FieldType.BOOL:
        raw = raw in ("1", "true", "on", True)
    try:
        return to_json(d, field.clean(raw))
    except ValidationError as e:
        raise ValidationError(_("%(f)s: %(e)s") % {"f": d.label_ar, "e": " ".join(e.messages)}) from e


def add_to_form(form, defs, values):
    for d in defs:
        name = PREFIX + d.key
        form.fields[name] = form_field(d)
        form.initial.setdefault(name, from_json(d, (values or {}).get(d.key)))


def collect(form, defs, existing=None) -> dict:
    data = dict(existing or {})
    for d in defs:
        data[d.key] = to_json(d, form.cleaned_data.get(PREFIX + d.key))
    return data


def bound_fields(form, defs):
    return [form[PREFIX + d.key] for d in defs]


__all__ = ["PREFIX", "FieldScope", "add_to_form", "bound_fields", "clean_value", "collect", "definitions"]
