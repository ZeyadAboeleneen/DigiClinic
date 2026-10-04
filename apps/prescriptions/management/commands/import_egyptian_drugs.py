"""python manage.py import_egyptian_drugs [--csv PATH] [--org SLUG] [--update] [--recategorize]

Imports the bundled Egyptian drug database (apps/prescriptions/data/, CC0, ~25k products) into each clinic's catalog,
with categories mapped from the dataset's drug class (apps/prescriptions/eg_classes.py). Idempotent: products already
in the catalog are skipped unless --update; drugs the clinic added itself (source="") are never modified.
--recategorize recomputes the categories of every imported drug from the current mapping (after improving
eg_classes.py); it replaces their categories, so manual category edits on imported drugs are lost.
"""

import csv
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.arabic import normalize_arabic
from apps.organizations.models import Organization
from apps.prescriptions.categories import category as get_category
from apps.prescriptions.eg_classes import ROUTE_FORMS, categories_for
from apps.prescriptions.models import Drug

SOURCE = "eg-db"
DEFAULT_CSV = Path(__file__).resolve().parents[2] / "data" / "egyptian-drugs.csv"
REQUIRED = {"commercial_name_en", "commercial_name_ar", "scientific_name", "manufacturer", "drug_class", "route"}

# Dosage form from the product name first (more precise than the route), e.g. "... SUSP. 80 ML" → شراب.
NAME_FORMS = [  # order matters: specific forms before generic words like "SOLUTION"
    (r"\b(EYE DROPS?|EYE GEL|EYE OINT|OPHTHALMIC|OPHTH)\b", "نقط عين"),
    (r"\b(EAR DROPS?|OTIC)\b", "نقط أذن"),
    (r"\b(NASAL SPRAY|NASAL DROPS?|NASAL)\b", "بخاخ أنف"),
    (r"\b(INHALER|INHAL|NEBULI[SZ]ER|RESPULES?)\b", "بخاخ"),
    (r"\b(AMP|AMPS|AMPOULES?|VIALS?|INJ|INJECTION|PREFILLED|SYRINGES?|PEN)\b", "حقن"),
    (r"\b(VAG|VAGINAL)\b", "لبوس مهبلي"),
    (r"\b(SUPP|SUPPOSITOR(Y|IES))\b", "لبوس"),
    (r"\b(EFF|EFFERVESCENT)\b", "فوار"),
    (r"\b(SACHETS?|SACH|GRANULES?)\b", "أكياس"),
    (r"\b(CAPS?|CAPSULES?)\b", "كبسولة"),
    (r"\b(TABS?|TABLETS?|CAPLETS?)\b", "قرص"),
    (r"\b(SUSP|SYRUP|SYR|ORAL SOL|ELIXIR|SOLUTION|SOL)\b", "شراب"),
    (r"\b(DROPS?)\b", "نقط"),
    (r"\b(OINT|OINTMENT)\b", "مرهم"),
    (r"\b(CREAM|CRM)\b", "كريم"),
    (r"\b(GEL)\b", "جل"),
    (r"\b(LOTION)\b", "لوشن"),
    (r"\b(SPRAY)\b", "بخاخ"),
]


def form_for(name: str, route: str) -> str:
    upper = name.upper()
    for pattern, form in NAME_FORMS:
        if re.search(pattern, upper):
            return form
    return ROUTE_FORMS.get((route or "").upper(), "")


def _clean(text: str, limit: int) -> str:
    return " ".join((text or "").split())[:limit]


class Command(BaseCommand):
    help = "Import the bundled Egyptian drug database (CC0, ~25k products) with categories."

    def add_arguments(self, parser):
        parser.add_argument("--csv", default=str(DEFAULT_CSV))
        parser.add_argument("--org", help="Organization slug (default: all)")
        parser.add_argument("--update", action="store_true", help="Refresh products already imported earlier.")
        parser.add_argument("--recategorize", action="store_true",
                            help="Replace imported drugs' categories with the current mapping.")  # fmt: skip

    def handle(self, *args, **o):
        path = Path(o["csv"])
        if not path.exists():
            raise CommandError(f"{path} not found")
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        if not rows or not REQUIRED <= set(rows[0]):
            raise CommandError(f"Unexpected columns — need {sorted(REQUIRED)}")
        orgs = Organization.objects.all()
        if o["org"]:
            orgs = orgs.filter(slug=o["org"])
        for org in orgs:
            created, updated, skipped = self._import(org, rows, update=o["update"] or o["recategorize"],
                                                     replace_categories=o["recategorize"])  # fmt: skip
            self.stdout.write(f"{org.slug}: {created} added, {updated} updated, {skipped} already there")

    @transaction.atomic
    def _import(self, org, rows, *, update, replace_categories=False):
        existing = {n.lower(): (pk, src) for n, pk, src in Drug.objects.filter(organization=org).values_list(
            "name", "pk", "source")}  # fmt: skip
        cat_cache = {}

        def cats(names):
            out = []
            for n in names:
                if n not in cat_cache:
                    cat_cache[n] = get_category(org, n)
                out.append(cat_cache[n])
            return out

        to_create, to_update, wanted = [], [], {}
        seen = set()
        for r in rows:
            name = _clean(r["commercial_name_en"], 150)
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            values = {
                "generic_name": _clean(r["scientific_name"], 400),
                "form": form_for(name, r["route"]),
                "manufacturer": _clean(r["manufacturer"], 150),
                "aliases_ar": [normalize_arabic(r["commercial_name_ar"])] if r["commercial_name_ar"].strip() else [],
            }
            categories = cats(categories_for(r["drug_class"], r["route"], r["scientific_name"]))
            known = existing.get(name.lower())
            if known is None:
                to_create.append(Drug(organization=org, name=name, source=SOURCE, **values))
                wanted[name.lower()] = categories
            elif update and known[1] == SOURCE:
                to_update.append(Drug(pk=known[0], **values))
                wanted[name.lower()] = categories
        Drug.objects.bulk_create(to_create, batch_size=1000)
        if to_update:
            Drug.objects.bulk_update(to_update, ["generic_name", "form", "manufacturer", "aliases_ar"], batch_size=1000)
        # Categories: only ever *added* (a doctor's manual tags are kept on --update).
        ids = {
            n.lower(): pk for n, pk in Drug.objects.filter(organization=org, source=SOURCE).values_list("name", "pk")
        }
        through = Drug.categories.through
        if replace_categories:
            through.objects.filter(drug_id__in=[ids[n] for n in wanted if n in ids]).delete()
        links = [
            through(drug_id=ids[name], drugcategory_id=c.pk)
            for name, categories in wanted.items()
            if name in ids
            for c in categories
        ]
        through.objects.bulk_create(links, batch_size=2000, ignore_conflicts=True)
        return len(to_create), len(to_update), len(rows) - len(to_create) - len(to_update)
