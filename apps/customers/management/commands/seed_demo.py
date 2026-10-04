"""Demo data (fake numbers/emails): Travco → Jaz Hotels → Jaz Aquamarine, with the 3 contacts from the Phase 2 DoD.

python manage.py seed_demo            # add
python manage.py seed_demo --remove   # remove it again
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.customers.models import ChannelType, Contact, ContactChannel, Customer, CustomerKind, PreferredChannel
from apps.organizations.models import Organization

CONTACTS = [
    # name, job title, whatsapp, email, preferred
    ("Ahmed", "Purchasing Manager", "01000000101", "ahmed@jaz.example", PreferredChannel.BOTH),
    ("Mohamed", "Purchasing Officer", "01000000102", "", PreferredChannel.WHATSAPP),
    ("Sara", "Purchasing Officer", "", "sara@jaz.example", PreferredChannel.EMAIL),
]


class Command(BaseCommand):
    help = "Create (or remove) demo customers for trying the system."

    def add_arguments(self, parser):
        parser.add_argument("--org", default="albarq")
        parser.add_argument("--remove", action="store_true")

    @transaction.atomic
    def handle(self, *args, **o):
        try:
            org = Organization.objects.get(slug=o["org"])
        except Organization.DoesNotExist as e:
            raise CommandError(f"Organization '{o['org']}' not found.") from e

        if o["remove"]:
            travco = Customer.objects.filter(organization=org, name="Travco", parent=None).first()
            if travco:
                ids = [*travco.descendant_ids(), travco.pk]
                Contact.objects.filter(customer_id__in=ids).delete()
                for pk in sorted(ids, key=lambda i: -len(Customer.objects.get(pk=i).ancestors())):
                    Customer.objects.filter(pk=pk).delete()
            self.stdout.write(self.style.SUCCESS("Demo data removed."))
            return

        travco, _ = Customer.objects.get_or_create(
            organization=org, name="Travco", parent=None, defaults={"kind": CustomerKind.GROUP}
        )
        jaz, _ = Customer.objects.get_or_create(
            organization=org, name="Jaz Hotels", parent=travco, defaults={"kind": CustomerKind.CHAIN}
        )
        Customer.objects.get_or_create(
            organization=org,
            name="Jaz Aquamarine",
            parent=jaz,
            defaults={"kind": CustomerKind.RESORT, "city": "الغردقة"},
        )
        for i, (name, job, wa, email, pref) in enumerate(CONTACTS):
            contact, _ = Contact.objects.get_or_create(
                organization=org,
                customer=jaz,
                name=name,
                defaults={
                    "job_title": job,
                    "department": "إدارة المشتريات",
                    "preferred_channel": pref,
                    "is_primary": i == 0,
                },
            )
            for type_, value in ((ChannelType.WHATSAPP, wa), (ChannelType.EMAIL, email)):
                if value:
                    ch = ContactChannel(organization=org, contact=contact, type=type_, value=value, is_primary=True)
                    ch.clean()
                    ContactChannel.objects.get_or_create(
                        contact=contact, type=type_, value=ch.value, defaults={"organization": org, "is_primary": True}
                    )
        self.stdout.write(self.style.SUCCESS("Demo data ready: Travco → Jaz Hotels (3 contacts) → Jaz Aquamarine"))
