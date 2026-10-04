"""python manage.py restore <file.dcbak> --database NEW_DB --media-root EMPTY_FOLDER [--create-db] [--check]

Restores into an EMPTY database + EMPTY media folder only (never over live data). To switch the clinic to the
restored copy afterwards, point DATABASE_URL / MEDIA_ROOT in .env at them and restart run.bat.
`--check` prints row counts of the main tables so you can compare them with the live system.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.core import backup

CHECK_TABLES = [
    "patients_patient",
    "scheduling_appointment",
    "clinical_visit",
    "prescriptions_prescription",
    "billing_payment",
    "notifications_scheduledmessage",
]


class Command(BaseCommand):
    help = "Restore an encrypted backup into an empty database and an empty media folder."

    def add_arguments(self, parser):
        parser.add_argument("file")
        parser.add_argument("--database", required=True, help="Target database name (must be empty).")
        parser.add_argument("--media-root", required=True, help="Target folder for private files (must be empty).")
        parser.add_argument("--create-db", action="store_true", help="Create the target database first.")
        parser.add_argument("--check", action="store_true", help="Print row counts of the main tables after restore.")

    def handle(self, *args, **o):
        try:
            manifest = backup.restore_backup(
                Path(o["file"]), database=o["database"], media_root=Path(o["media_root"]), create_db=o["create_db"]
            )
        except backup.BackupError as e:
            raise CommandError(str(e)) from e
        self.stdout.write(self.style.SUCCESS(f"Restored backup from {manifest['created_at']} into {o['database']}"))
        if o["check"]:
            for table, n in backup.count_rows(o["database"], CHECK_TABLES).items():
                self.stdout.write(f"  {table}: {n}")
