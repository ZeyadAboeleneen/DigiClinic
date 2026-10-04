import getpass

from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import User
from apps.organizations.models import Membership, Organization, Role


class Command(BaseCommand):
    help = (
        "Create (or update) a user and their membership from the terminal.\n"
        "  python manage.py create_user --email a@b.com --name 'Name' --role owner [--org albarq]\n"
        "The password is asked interactively unless --password is given."
    )

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--role", required=True, choices=Role.values)
        parser.add_argument("--org", default="albarq")
        parser.add_argument("--password", help="Avoid on shared machines; omit to be prompted.")
        parser.add_argument("--superuser", action="store_true", help="Also grant Django admin access.")

    def handle(self, *args, **o):
        try:
            org = Organization.objects.get(slug=o["org"])
        except Organization.DoesNotExist as e:
            raise CommandError(f"Organization '{o['org']}' not found. Run `manage.py seed_org` first.") from e

        email = o["email"].strip().lower()
        user = User.objects.filter(email=email).first()
        password = o["password"]
        if user is None and not password:
            password = getpass.getpass("Password: ")
            if password != getpass.getpass("Password (again): "):
                raise CommandError("Passwords do not match.")

        with transaction.atomic():
            if user is None:
                candidate = User(email=email, full_name=o["name"])
                try:
                    password_validation.validate_password(password, candidate)
                except ValidationError as e:
                    raise CommandError(" ".join(e.messages)) from e
                user = User.objects.create_user(email=email, password=password, full_name=o["name"])
                action = "Created"
            else:
                user.full_name = o["name"]
                if password:
                    user.set_password(password)
                action = "Updated"
            if o["superuser"]:
                user.is_staff = user.is_superuser = True
            user.save()
            Membership.objects.update_or_create(
                user=user, organization=org, defaults={"role": o["role"], "is_active": True}
            )
        self.stdout.write(self.style.SUCCESS(f"{action} {email} as {o['role']} in {org.slug}"))
