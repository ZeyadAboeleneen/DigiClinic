import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.organizations.models import Organization, OrganizationSettings

DEFAULT_CLINIC = Path(settings.BASE_DIR) / "docs" / "plan" / "seed" / "clinic.json"


class Command(BaseCommand):
    help = "Create an Organization and its settings from clinic.json. Existing ones are left alone unless --force."

    def add_arguments(self, parser):
        parser.add_argument("--clinic", default=str(DEFAULT_CLINIC))
        parser.add_argument(
            "--force", action="store_true", help="Overwrite settings of an existing organization with clinic.json."
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        data = json.loads(Path(opts["clinic"]).read_text(encoding="utf-8"))
        o, c = data["organization"], data["settings"]
        org, created = Organization.objects.get_or_create(
            slug=o["slug"], defaults={"name_ar": o["name_ar"], "name_en": o.get("name_en", "")}
        )
        s, settings_created = OrganizationSettings.objects.get_or_create(organization=org)
        if not (created or settings_created or opts["force"]):
            # run.bat calls this on every start: never clobber what was edited in the settings UI.
            self.stdout.write(f"Organization '{org.slug}' already set up (use --force to reapply clinic.json)")
            return
        if opts["force"]:
            org.name_ar, org.name_en = o["name_ar"], o.get("name_en", "")
            org.save()
        s.clinic_name_ar = c.get("clinic_name_ar", "")
        s.clinic_name_en = c.get("clinic_name_en", "")
        s.primary_color = c["primary_color"]
        s.dark_color = c["dark_color"]
        s.neutral_color = c["neutral_color"]
        s.phones = c.get("phones", [])
        s.address_ar = c.get("address_ar", "")
        s.map_link = c.get("map_link", "")
        s.working_hours_text = c.get("working_hours_text", "")
        s.timezone = c.get("timezone", s.timezone)
        s.whatsapp_sender = c.get("whatsapp_sender", "")
        s.save()
        verb = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{verb} organization '{org.slug}' ({org.name_ar})"))
