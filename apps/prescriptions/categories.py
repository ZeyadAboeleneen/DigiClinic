"""Drug categories per doctor: seeded once from the doctor's specialty, then freely edited (hide/reorder/add)."""

from django.db import transaction
from django.db.models import Max

from .models import CategoryLevel, DoctorCategory, DrugCategory
from .specialties import SPECIALTIES


def category(org, name: str) -> DrugCategory:
    name = " ".join((name or "").split())[:100]
    found = DrugCategory.objects.filter(organization=org, name__iexact=name).first()
    return found or DrugCategory.objects.create(organization=org, name=name)


def for_doctor(doctor, *, include_hidden=False):
    qs = DoctorCategory.objects.filter(doctor=doctor).select_related("category").order_by("order", "id")
    return list(qs if include_hidden else qs.filter(is_active=True))


@transaction.atomic
def apply_specialty(doctor, key: str):
    """Replace the doctor's category list with the specialty preset (in its order). Drug tags are untouched."""
    if key not in SPECIALTIES:
        raise ValueError(f"unknown specialty {key!r}")
    DoctorCategory.objects.filter(doctor=doctor).delete()
    for order, (name, level) in enumerate(SPECIALTIES[key][1]):
        DoctorCategory.objects.create(
            organization=doctor.organization,
            doctor=doctor,
            category=category(doctor.organization, name),
            order=order,
            level=level,
        )
    doctor.drug_specialty = key
    doctor.save(update_fields=["drug_specialty", "updated_at"])


def add(doctor, name: str, level=CategoryLevel.PRIMARY) -> DoctorCategory:
    cat = category(doctor.organization, name)
    entry, created = DoctorCategory.objects.get_or_create(
        organization=doctor.organization, doctor=doctor, category=cat, defaults={"level": level}
    )
    if created:
        last = DoctorCategory.objects.filter(doctor=doctor).aggregate(m=Max("order"))["m"] or 0
        entry.order = last + 1
        entry.save(update_fields=["order", "updated_at"])
    elif not entry.is_active:
        entry.is_active = True
        entry.save(update_fields=["is_active", "updated_at"])
    return entry


def move(entry: DoctorCategory, direction: str):
    entries = for_doctor(entry.doctor, include_hidden=True)
    i = next(n for n, e in enumerate(entries) if e.pk == entry.pk)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(entries):
        entries[i], entries[j] = entries[j], entries[i]
        for n, e in enumerate(entries):
            if e.order != n:
                DoctorCategory.objects.filter(pk=e.pk).update(order=n)
