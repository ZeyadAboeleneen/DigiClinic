import json
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.organizations.models import Organization, OrganizationSettings

DEFAULT_BRAND = Path(settings.BASE_DIR) / "docs" / "plan" / "seed" / "brand.json"
DEFAULT_LOGO = Path(settings.BASE_DIR) / "docs" / "plan" / "assets" / "logo.png"


class Command(BaseCommand):
    help = "Create an Organization and its settings from brand.json. Existing ones are left alone unless --force."

    def add_arguments(self, parser):
        parser.add_argument("--brand", default=str(DEFAULT_BRAND))
        parser.add_argument("--logo", default=str(DEFAULT_LOGO))
        parser.add_argument(
            "--force", action="store_true", help="Overwrite settings of an existing organization with brand.json."
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        brand = json.loads(Path(opts["brand"]).read_text(encoding="utf-8"))
        o, q, c = brand["organization"], brand["quotation"], brand["colors"]
        org, created = Organization.objects.get_or_create(
            slug=o["slug"], defaults={"name_ar": o["name_ar"], "name_en": o.get("name_en", "")}
        )
        s, settings_created = OrganizationSettings.objects.get_or_create(organization=org)
        if not (created or settings_created or opts["force"]):
            # run.bat calls this on every start: never clobber what was edited in the settings UI.
            self.stdout.write(f"Organization '{org.slug}' already set up (use --force to reapply brand.json)")
            return
        if opts["force"]:
            org.name_ar, org.name_en = o["name_ar"], o.get("name_en", "")
            org.save()
        s.primary_color = c["primary"]
        s.dark_color = c["dark"]
        s.neutral_color = c["neutral"]
        s.section_row_color = c.get("section_row_bg", s.section_row_color)
        s.tagline_ar = brand.get("tagline_ar", "")
        s.phones = brand.get("phones", [])
        s.email_display = brand.get("email", "")
        s.address_ar = brand.get("address_ar", "")
        s.address_en = brand.get("address_en", "")
        s.quote_number_prefix = q["number_prefix"]
        s.quote_number_format = q["number_format"]
        s.default_validity_days = q["validity_days"]
        s.validity_mode = q.get("validity_mode", s.validity_mode)
        s.vat_rate = q["vat_rate"]
        s.prices_include_vat = q["prices_include_vat"]
        s.default_intro_text = q.get("intro_text", "")
        s.price_note = q.get("price_note", "")
        s.default_terms = q.get("default_terms", [])
        s.whatsapp_sender = brand.get("whatsapp_sender", "")
        logo = Path(opts["logo"])
        if logo.exists() and not s.logo:
            with logo.open("rb") as fh:
                s.logo.save(logo.name, File(fh), save=False)
        s.save()
        verb = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{verb} organization '{org.slug}' ({org.name_ar})"))
