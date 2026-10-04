from .models import Doctor


def get_doctor(org) -> Doctor:
    """Single-doctor v1: the one Doctor row for this org, created on first use."""
    doctor, _created = Doctor.objects.get_or_create(
        organization=org, defaults={"name_ar": org.settings.clinic_name_ar or org.name_ar}
    )
    return doctor
