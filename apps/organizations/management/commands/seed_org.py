import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.doctors.models import Doctor, VisitType, WorkingPeriod
from apps.organizations.models import Organization, OrganizationSettings

DEFAULT_CLINIC = Path(settings.BASE_DIR) / "docs" / "plan" / "seed" / "clinic.json"


class Command(BaseCommand):
    help = "Create an Organization (+ settings, doctor, schedule) from clinic.json. Left alone unless --force."

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
        self._seed_doctor(org, data)
        verb = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{verb} organization '{org.slug}' ({org.name_ar})"))

    def _seed_doctor(self, org, data):
        d = data.get("doctor")
        if not d:
            return
        doctor, _created = Doctor.objects.update_or_create(
            organization=org,
            defaults={
                "name_ar": d["name_ar"],
                "name_en": d.get("name_en", ""),
                "title_ar": d.get("title_ar", ""),
                "specialty_ar": d.get("specialty_ar", ""),
                "qualifications": d.get("qualifications", []),
                "booking_mode": d.get("booking_mode", "slots"),
                "slot_minutes": d.get("slot_minutes", 15),
                "booking_horizon_days": d.get("booking_horizon_days", 60),
                "min_notice_minutes": d.get("min_notice_minutes", 0),
                "queue_avg_minutes": d.get("queue_avg_minutes", 15),
                "near_turn_threshold": d.get("near_turn_threshold", 0),
                "allow_overbooking": d.get("allow_overbooking", False),
            },
        )
        WorkingPeriod.objects.filter(doctor=doctor).delete()
        for wp in data.get("working_periods", []):
            WorkingPeriod.objects.create(
                organization=org,
                doctor=doctor,
                weekday=wp["weekday"],
                start_time=wp["start"],
                end_time=wp["end"],
                max_patients=wp.get("max_patients"),
            )
        VisitType.objects.filter(doctor=doctor).delete()
        for order, vt in enumerate(data.get("visit_types", [])):
            VisitType.objects.create(
                organization=org,
                doctor=doctor,
                name_ar=vt["name_ar"],
                duration_minutes=vt["duration_minutes"],
                price=vt["price"],
                is_followup=vt.get("is_followup", False),
                free_followup_days=vt.get("free_followup_days", 0),
                color=vt.get("color", "#0E7C86"),
                order=order,
            )
