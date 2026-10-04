"""Business logic for patients: file numbers, duplicate warnings, merging."""

from django.db import transaction
from django.utils.translation import gettext as _

from apps.core.phones import to_e164

from .models import Patient, PatientSequence


class MergeError(Exception):
    pass


def next_file_number(org) -> int:
    """Must run inside `transaction.atomic()`."""
    PatientSequence.objects.get_or_create(organization=org)
    seq = PatientSequence.objects.select_for_update().get(organization=org)
    seq.last_value += 1
    seq.save(update_fields=["last_value", "updated_at"])
    return seq.last_value


@transaction.atomic
def create_patient(org, instance: Patient, *, by=None) -> Patient:
    """`instance` is an unsaved Patient (e.g. `PatientForm.save(commit=False)`)."""
    instance.organization = org
    instance.file_number = next_file_number(org)
    instance.full_clean(exclude=["file_number", "name_normalized", "organization"])
    if instance.messaging_consent and by is not None:
        instance.consent_recorded_by = by
    instance.save()
    return instance


def possible_duplicates(org, phone: str):
    """Active patients already on this phone number, for the "already exists?" warning (never blocks)."""
    try:
        e164 = to_e164(phone)
    except Exception:
        return Patient.objects.none()
    return Patient.objects.for_org(org).filter(phone=e164, is_active=True)


@transaction.atomic
def merge_patients(primary: Patient, duplicate: Patient, *, by=None) -> Patient:
    if primary.organization_id != duplicate.organization_id:
        raise MergeError(_("المريضين لازم يكونوا من نفس العيادة."))
    if primary.pk == duplicate.pk:
        raise MergeError(_("مينفعش تدمج المريض في نفسه."))
    primary = Patient.objects.select_for_update().get(pk=primary.pk)
    duplicate = Patient.objects.select_for_update().get(pk=duplicate.pk)
    if duplicate.merged_into_id:
        raise MergeError(_("المريض ده متدمج بالفعل."))

    duplicate.allergies.update(patient=primary)
    duplicate.chronic_conditions.update(patient=primary)
    # Appointments/visits/attachments move here too once those apps exist (Phase 3/6).

    duplicate.merged_into = primary
    duplicate.is_active = False
    duplicate.save(update_fields=["merged_into", "is_active", "updated_at"])
    return primary
