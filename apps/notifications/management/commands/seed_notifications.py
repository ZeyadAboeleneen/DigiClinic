import json
from datetime import time
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.notifications.defaults import DEFAULT_TEMPLATES
from apps.notifications.models import Event, NotificationSettings, NotificationTemplate
from apps.organizations.management.commands.seed_org import DEFAULT_CLINIC
from apps.organizations.models import Organization

SETTINGS_FIELDS = [f.name for f in NotificationSettings._meta.get_fields() if f.concrete and not f.is_relation]


def seed_org_notifications(org, *, clinic_data=None, force=False) -> int:
    """Notification settings + default templates for one clinic. Existing ones are left alone unless `force`."""
    data = clinic_data or {}
    ns, ns_created = NotificationSettings.objects.get_or_create(organization=org)
    if ns_created or force:
        for key, value in (data.get("notification_settings") or {}).items():
            if key in SETTINGS_FIELDS:
                if key in ("quiet_start", "quiet_end", "urgent_until"):
                    value = time.fromisoformat(value)
                setattr(ns, key, value)
        ns.save()

    if NotificationTemplate.objects.filter(organization=org).exists() and not force:
        return 0
    if force:
        NotificationTemplate.objects.filter(organization=org).delete()
    # clinic.json's `reminders` overrides the offsets/lead times of the default reminders, in order.
    reminders = iter(data.get("reminders") or [])
    created = 0
    for order, tpl in enumerate(DEFAULT_TEMPLATES):
        fields = dict(tpl)
        if fields["event"] == Event.REMINDER:
            override = next(reminders, None)
            if override:
                fields.update({k: v for k, v in override.items() if k in tpl or k == "applies_to_mode"})
        NotificationTemplate.objects.create(organization=org, order=order, **fields)
        created += 1
    return created


class Command(BaseCommand):
    help = "Default notification settings + message templates for every clinic (or --org). Idempotent."

    def add_arguments(self, parser):
        parser.add_argument("--org", help="Organization slug (default: all)")
        parser.add_argument("--clinic", default=str(DEFAULT_CLINIC))
        parser.add_argument("--force", action="store_true", help="Replace existing templates and settings.")

    @transaction.atomic
    def handle(self, *args, **opts):
        path = Path(opts["clinic"])
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        orgs = Organization.objects.all()
        if opts["org"]:
            orgs = orgs.filter(slug=opts["org"])
        for org in orgs:
            n = seed_org_notifications(org, clinic_data=data, force=opts["force"])
            self.stdout.write(f"{org.slug}: {n} template(s) created" if n else f"{org.slug}: templates already set up")
