"""python manage.py check_health [--alert]

Prints problems per clinic (scheduler stopped > 15 min, WhatsApp disconnected > 15 min). With --alert, e-mails each
clinic's owners — at most once per problem every 6 hours (remembered as `ops.alert` audit events). Meant to run from
cron on the server every 5 minutes, *outside* the scheduler (so it still works when the scheduler is the problem).
"""

from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.core.health import problems
from apps.organizations.models import Membership, Organization, Role

REPEAT_AFTER = timedelta(hours=6)


class Command(BaseCommand):
    help = "Check the scheduler and WhatsApp; with --alert, e-mail the clinic owners (once per problem per 6 h)."

    def add_arguments(self, parser):
        parser.add_argument("--alert", action="store_true")

    def handle(self, *args, **o):
        now = timezone.now()
        total = 0
        for org in Organization.objects.filter(is_active=True):
            found = problems(org, now)
            total += len(found)
            for key, text in found:
                self.stdout.write(f"[{org.slug}] {key}: {text}")
                if o["alert"]:
                    self._alert(org, key, text, now)
        if not total:
            self.stdout.write("OK")

    def _alert(self, org, key, text, now):
        recent = AuditEvent.objects.filter(
            organization=org, action="ops.alert", summary=key, created_at__gte=now - REPEAT_AFTER
        ).exists()
        if recent:
            return
        owners = list(
            Membership.objects.filter(organization=org, role=Role.OWNER, is_active=True)
            .exclude(user__email="")
            .values_list("user__email", flat=True)
        )
        if not owners:
            return
        send_mail(
            subject=f"DigiClinic — تنبيه: {org.name_ar}",
            message=f"{text}\n\n{settings.SITE_URL}".strip(),
            from_email=None,
            recipient_list=owners,
            fail_silently=True,
        )
        audit.log("ops.alert", org=org, summary=key)
