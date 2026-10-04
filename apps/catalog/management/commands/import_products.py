"""Idempotent catalog import.

    python manage.py import_products docs/plan/seed/products.csv --org albarq

CSV columns: group_order,group,group_description,category_order,category,product_order,name_ar,unit
Matches groups/categories by name and products by (category, name). Only missing rows are created:
anything that already exists (including order, unit and edits made in the UI) is left untouched,
so re-running it after adding rows to the CSV never duplicates or undoes UI changes.
"""

import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalog.models import Category, Product, ProductGroup, Unit
from apps.organizations.models import Organization

REQUIRED = {"group_order", "group", "category_order", "category", "product_order", "name_ar", "unit"}


class Command(BaseCommand):
    help = "Import / update the product catalog from CSV (idempotent upsert)."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--org", default="albarq")

    @transaction.atomic
    def handle(self, *args, **o):
        try:
            org = Organization.objects.get(slug=o["org"])
        except Organization.DoesNotExist as e:
            raise CommandError(f"Organization '{o['org']}' not found.") from e
        path = Path(o["path"])
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        if not rows or not REQUIRED <= set(rows[0]):
            raise CommandError(f"CSV must have columns: {', '.join(sorted(REQUIRED))}")

        stats = dict.fromkeys(("groups", "categories", "products_new", "products_existing"), 0)
        groups, categories = {}, {}
        for row in rows:
            gname = " ".join(row["group"].split())
            if gname not in groups:
                group, created = ProductGroup.objects.get_or_create(
                    organization=org,
                    name=gname,
                    defaults={"sort_order": int(row["group_order"]), "description": row.get("group_description", "")},
                )
                groups[gname] = group
                stats["groups"] += created
            cname = " ".join(row["category"].split())
            if cname not in categories:
                cat, created = Category.objects.get_or_create(
                    organization=org,
                    name=cname,
                    defaults={"group": groups[gname], "sort_order": int(row["category_order"])},
                )
                categories[cname] = cat
                stats["categories"] += created
            name = " ".join(row["name_ar"].split())
            if Product.objects.filter(organization=org, category=categories[cname], name=name).exists():
                stats["products_existing"] += 1
                continue
            Product.objects.create(
                organization=org,
                category=categories[cname],
                name=name,
                unit=Unit.get_for(org, row["unit"]),
                sort_order=int(row["product_order"]),
            )
            stats["products_new"] += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"{len(rows)} rows · groups: {len(groups)} ({stats['groups']} new) · "
                f"categories: {len(categories)} ({stats['categories']} new) · "
                f"products: {stats['products_new']} new, {stats['products_existing']} already there"
            )
        )
