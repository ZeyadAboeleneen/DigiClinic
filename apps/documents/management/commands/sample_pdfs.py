"""Render sample quotation PDFs into tmp/ for visual comparison with the reference. Writes nothing to the DB.

python manage.py sample_pdfs
"""

from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Product
from apps.customers.models import Contact, Customer, CustomerKind
from apps.documents import pdf
from apps.organizations.models import Organization
from apps.quotations import services
from apps.quotations.models import QuotationStatus

PRICES = [Decimal(x) for x in ("65", "60", "85", "100", "6", "55", "20", "33", "40", "52", "650", "5.20", "2.50")]


class Rollback(Exception):
    pass


class Command(BaseCommand):
    help = "Render sample PDFs (5 items, all products, long names) into tmp/ without saving anything."

    def add_arguments(self, parser):
        parser.add_argument("--org", default="albarq")

    def handle(self, *args, **o):
        out = Path(settings.BASE_DIR) / "tmp"
        out.mkdir(exist_ok=True)
        org = Organization.objects.get(slug=o["org"])
        try:
            with transaction.atomic():
                self._render(org, out)
                raise Rollback
        except Rollback:
            pass

    def _render(self, org, out):
        travco = Customer.objects.create(organization=org, name="Travco [sample]", kind=CustomerKind.GROUP, code="ZZT")
        jaz = Customer.objects.create(
            organization=org, name="Jaz Hotels", kind=CustomerKind.CHAIN, parent=travco, code="ZZJ"
        )
        contact = Contact.objects.create(
            organization=org, customer=jaz, name="Ahmed", job_title="مدير المشتريات", is_primary=True
        )
        products = list(Product.objects.for_org(org).filter(is_active=True).select_related("category__group", "unit"))

        def make(n, long_names=False):
            q = services.new_draft(org, None, jaz, contact)
            for i, p in enumerate(products[:n]):
                item, _ = services.add_product(q, p)
                if long_names and i % 7 == 0:
                    item.description = item.description + " — " + "وصف إضافي طويل جدًا للصنف لتجربة التفاف السطر " * 2
                item.unit_price = PRICES[i % len(PRICES)]
                item.save()
            return q

        for label, q, draft in [
            ("5-items-draft", make(5), True),
            ("all-products", make(len(products)), False),
            ("long-names", make(20, long_names=True), False),
        ]:
            if not draft:
                q = services.finalize(q, None)
                q.status = QuotationStatus.FINALIZED
                q.finalized_at = timezone.now()
            path = out / f"sample-{label}.pdf"
            path.write_bytes(pdf.render_pdf(q, draft=draft))
            self.stdout.write(f"{path}  ({q.items.count()} items)")
