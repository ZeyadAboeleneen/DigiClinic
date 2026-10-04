"""python manage.py import_drugs docs/plan/seed/drugs.csv [--org demo-clinic] [--update]

Upsert by name. Existing drugs are left alone (edits made in the UI win) unless --update. Also seeds the default
dose phrases ("كل 8 ساعات"، "بعد الأكل"...) the first time.
"""

import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.organizations.models import Organization
from apps.prescriptions.models import DosePhrase, Drug, PhraseKind

DEFAULT_CSV = Path(settings.BASE_DIR) / "docs" / "plan" / "seed" / "drugs.csv"
DEFAULT_PHRASES = [
    (PhraseKind.DOSE, ["قرص", "½ قرص", "كبسولة", "5 مل", "10 مل", "نقطتين", "بخة"]),
    (PhraseKind.FREQUENCY, ["مرة يوميًا", "كل 12 ساعة", "كل 8 ساعات", "كل 6 ساعات", "عند اللزوم"]),
    (PhraseKind.TIMING, ["قبل الأكل", "بعد الأكل", "قبل النوم", "على الريق"]),
    (PhraseKind.DURATION, ["لمدة 3 أيام", "لمدة 5 أيام", "لمدة 7 أيام", "لمدة 10 أيام", "لمدة شهر", "باستمرار"]),
]


def seed_phrases(org) -> int:
    if DosePhrase.objects.filter(organization=org).exists():
        return 0
    n = 0
    for kind, texts in DEFAULT_PHRASES:
        for order, text in enumerate(texts):
            DosePhrase.objects.create(organization=org, kind=kind, text=text, order=order)
            n += 1
    return n


class Command(BaseCommand):
    help = "Import the drug catalog from a CSV (name, generic_name, form, strength, aliases_ar, ...)."

    def add_arguments(self, parser):
        parser.add_argument("csv", nargs="?", default=str(DEFAULT_CSV))
        parser.add_argument("--org", help="Organization slug (default: all)")
        parser.add_argument("--update", action="store_true", help="Overwrite drugs that already exist.")

    @transaction.atomic
    def handle(self, *args, **o):
        path = Path(o["csv"])
        if not path.exists():
            raise CommandError(f"{path} not found")
        rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
        orgs = Organization.objects.all()
        if o["org"]:
            orgs = orgs.filter(slug=o["org"])
        for org in orgs:
            created = updated = 0
            for row in rows:
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                values = {
                    "generic_name": (row.get("generic_name") or "").strip(),
                    "form": (row.get("form") or "").strip(),
                    "strength": (row.get("strength") or "").strip(),
                    "aliases_ar": [a.strip() for a in (row.get("aliases_ar") or "").split("|") if a.strip()],
                    "default_instructions": (row.get("default_instructions") or "").strip(),
                    "default_duration": (row.get("default_duration") or "").strip(),
                }
                drug = Drug.objects.filter(organization=org, name=name).first()
                if drug is None:
                    Drug.objects.create(organization=org, name=name, **values)
                    created += 1
                elif o["update"]:
                    for k, v in values.items():
                        setattr(drug, k, v)
                    drug.save()
                    updated += 1
            phrases = seed_phrases(org)
            self.stdout.write(f"{org.slug}: {created} drug(s) added, {updated} updated, {phrases} phrase(s) added")
