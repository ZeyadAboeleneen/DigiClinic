from django.core.management.base import BaseCommand

from apps.quotations.services import expire_overdue


class Command(BaseCommand):
    help = "Mark issued quotations past their validity date as expired (run daily; run.bat runs it on start)."

    def handle(self, *args, **options):
        self.stdout.write(f"Expired {expire_overdue()} quotation(s).")
