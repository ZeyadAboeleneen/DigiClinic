"""30 fake patients for trying the system, 3 of them sharing one phone number (a family).

python manage.py seed_demo            # add
python manage.py seed_demo --remove   # remove it again
"""

import random

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.organizations.models import Organization
from apps.patients import services
from apps.patients.models import Gender, Patient

MARKER = "[seed_demo]"

FIRST_MALE = ["محمد", "أحمد", "محمود", "علي", "عمر", "يوسف", "خالد", "كريم", "حسن", "سيد"]
FIRST_FEMALE = ["سارة", "منى", "فاطمة", "نور", "ياسمين", "هبة", "مريم", "دينا", "رنا", "آية"]
LAST = ["إبراهيم", "السيد", "عبدالله", "حسين", "فتحي", "صلاح", "جمال", "رمضان", "عطية", "الشريف"]


def _phone(n: int) -> str:
    return f"+2010{n:08d}"


class Command(BaseCommand):
    help = "Create (or remove) 30 demo patients, 3 of them on the same phone number."

    def add_arguments(self, parser):
        parser.add_argument("--org", default="demo-clinic")
        parser.add_argument("--remove", action="store_true")

    @transaction.atomic
    def handle(self, *args, **o):
        try:
            org = Organization.objects.get(slug=o["org"])
        except Organization.DoesNotExist as e:
            raise CommandError(f"Organization '{o['org']}' not found. Run `manage.py seed_org` first.") from e

        if o["remove"]:
            count, _ = Patient.objects.filter(organization=org, notes=MARKER).delete()
            self.stdout.write(self.style.SUCCESS(f"{count} اتمسحوا."))
            return

        rng = random.Random(42)  # noqa: S311 — deterministic fake demo data, not security-sensitive
        created = 0

        # A family of 3 sharing the same phone number (the DoD scenario).
        family_phone = _phone(90000001)
        for i, name in enumerate(["محمد إبراهيم السيد", "منى إبراهيم السيد", "أحمد إبراهيم السيد (الأب)"]):
            services.create_patient(
                org,
                Patient(
                    organization=org,
                    full_name=name,
                    gender=Gender.FEMALE if i == 1 else Gender.MALE,
                    phone=family_phone,
                    preferred_channel="whatsapp",
                    notes=MARKER,
                ),
            )
            created += 1

        for i in range(27):
            is_female = rng.random() < 0.5
            full_name = f"{rng.choice(FIRST_FEMALE if is_female else FIRST_MALE)} {rng.choice(LAST)}"
            services.create_patient(
                org,
                Patient(
                    organization=org,
                    full_name=full_name,
                    gender=Gender.FEMALE if is_female else Gender.MALE,
                    phone=_phone(90000010 + i),
                    preferred_channel=rng.choice(["whatsapp", "email", "none"]),
                    messaging_consent=rng.random() < 0.8,
                    notes=MARKER,
                ),
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(f"{created} مريض اتضافوا لـ'{org.slug}' (منهم 3 على نفس الرقم)."))
