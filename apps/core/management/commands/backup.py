"""python manage.py backup [--keep 14]   → encrypted copy of the database + private media (07 §7.5)."""

from django.core.management.base import BaseCommand, CommandError

from apps.core import backup


class Command(BaseCommand):
    help = "Create an encrypted backup (database + private files) and keep the newest N."

    def add_arguments(self, parser):
        parser.add_argument("--keep", type=int, default=14)

    def handle(self, *args, **o):
        try:
            path = backup.create_backup()
        except backup.BackupError as e:
            raise CommandError(str(e)) from e
        removed = backup.prune(keep=o["keep"])
        size = path.stat().st_size / 1024 / 1024
        self.stdout.write(
            self.style.SUCCESS(f"Backup written: {path} ({size:.1f} MB); {len(removed)} old one(s) removed")
        )
