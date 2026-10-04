"""Import customers from Al-Barq's contacts spreadsheet (ارقام العملاء والضرائب.xlsx).

Expected header row: الرقم الضريبى | اسم الشركة | رقم موبايل / واتساب | الاسم | المنصب
- Rows whose role marks a supplier (مورد / مبيعات / مندوب / انستا) are skipped, with any continuation rows.
  So is a company with neither a tax number nor a purchasing role (the file lists suppliers that way).
- A row with an empty company name belongs to the company above it.
- "0100.../0100..." with "Name1 -Name2" becomes two contacts.
Idempotent: re-running updates instead of duplicating.
"""

from dataclasses import dataclass, field

import openpyxl
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.phones import to_e164
from apps.customers.models import ChannelType, Contact, ContactChannel, Customer, CustomerKind
from apps.organizations.models import Organization

SUPPLIER_MARKERS = ("مورد", "مبيعات", "مندوب", "انستا")
ROLE_MAP = {"م مشتريات": "مدير مشتريات"}
PREFIXES = (("فندق ", CustomerKind.HOTEL), ("الشركة ", CustomerKind.COMPANY), ("شركة ", CustomerKind.COMPANY))
RESTAURANT_HINTS = ("برجر", "بورجر", "مطعم", "كافيه")
FALLBACK_CONTACT_NAME = "إدارة المشتريات"


@dataclass
class ParsedCustomer:
    name: str
    kind: str
    tax_id: str = ""
    contacts: list = field(default_factory=list)  # [(name, job_title, [e164, ...])]


def _text(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return " ".join(str(v).split())


def _classify(raw_name):
    name = raw_name
    for prefix, kind in PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix) :].strip(), kind
    if any(h in name for h in RESTAURANT_HINTS):
        return name, CustomerKind.RESTAURANT
    return name, CustomerKind.HOTEL


def _is_supplier(role):
    return any(m in role for m in SUPPLIER_MARKERS)


def _split(value, sep):
    return [p.strip() for p in value.split(sep) if p.strip()]


def parse_workbook(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    for ws in wb.worksheets:
        rows = ws.iter_rows(values_only=True)
        for header in rows:
            cells = [_text(c) for c in header]
            if "اسم الشركة" in cells:
                break
        else:
            continue
        idx = {name: cells.index(name) for name in cells if name}
        col_tax, col_company = idx.get("الرقم الضريبى"), idx["اسم الشركة"]
        col_phone = next(i for n, i in idx.items() if "موبايل" in n or "واتساب" in n)
        col_name, col_role = idx["الاسم"], idx["المنصب"]
        return list(_parse_rows(rows, col_tax, col_company, col_phone, col_name, col_role))
    raise CommandError("Couldn't find a sheet with an 'اسم الشركة' header.")


def _parse_rows(rows, col_tax, col_company, col_phone, col_name, col_role):
    current, skipped = None, []
    for row in rows:
        company = _text(row[col_company]) if col_company < len(row) else ""
        phone_raw = _text(row[col_phone]) if col_phone < len(row) else ""
        person = _text(row[col_name]) if col_name < len(row) else ""
        role = _text(row[col_role]) if col_role < len(row) else ""
        tax = _text(row[col_tax]) if col_tax is not None and col_tax < len(row) else ""
        if not any((company, phone_raw, person, role, tax)):
            continue
        if company:
            if current is not None:
                yield current
            if _is_supplier(role) or (not tax and role not in ROLE_MAP):
                current = None
                skipped.append(company)
                continue
            name, kind = _classify(company)
            tax_digits = "".join(ch for ch in tax if ch.isdigit())
            current = ParsedCustomer(name=name, kind=kind, tax_id=tax_digits.zfill(9) if tax_digits else "")
        elif current is None or _is_supplier(role):
            continue  # continuation of a skipped supplier
        phones = _split(phone_raw, "/")
        people = _split(person, " -") if len(phones) > 1 else [person] if person else []
        job = ROLE_MAP.get(role, role)
        if len(people) == len(phones) and len(phones) > 1:
            for p, ph in zip(people, phones, strict=True):
                current.contacts.append((p, job, [ph]))
        elif phones or people:
            contact_name = people[0] if people else ""
            if not contact_name or contact_name.startswith(("فندق", current.name)):
                contact_name = FALLBACK_CONTACT_NAME
            current.contacts.append((contact_name, job, phones))
    if current is not None:
        yield current


class Command(BaseCommand):
    help = "Import customers (not suppliers) from the Al-Barq contacts spreadsheet. Idempotent."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--org", default="albarq")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **o):
        try:
            org = Organization.objects.get(slug=o["org"])
        except Organization.DoesNotExist as e:
            raise CommandError(f"Organization '{o['org']}' not found.") from e
        parsed = parse_workbook(o["path"])
        stats = {"customers_new": 0, "customers_updated": 0, "contacts_new": 0, "channels_new": 0}
        with transaction.atomic():
            for pc in parsed:
                self._import_one(org, pc, stats)
                self.stdout.write(
                    f"  {pc.name} ({pc.kind}) tax={pc.tax_id or '-'} contacts={[c[0] for c in pc.contacts]}"
                )
            if o["dry_run"]:
                transaction.set_rollback(True)
        verb = "Would import" if o["dry_run"] else "Imported"
        self.stdout.write(self.style.SUCCESS(f"{verb} {len(parsed)} customers: {stats}"))

    def _import_one(self, org, pc, stats):
        customer = Customer.objects.filter(organization=org, name=pc.name, parent=None).first()
        if customer is None:
            customer = Customer.objects.create(organization=org, name=pc.name, kind=pc.kind, tax_id=pc.tax_id)
            stats["customers_new"] += 1
        else:
            if pc.tax_id and not customer.tax_id:
                customer.tax_id = pc.tax_id
                customer.save()
            stats["customers_updated"] += 1
        for i, (name, job, phones) in enumerate(pc.contacts):
            contact = Contact.objects.filter(customer=customer, name=name).first()
            if contact is None:
                contact = Contact.objects.create(
                    organization=org,
                    customer=customer,
                    name=name,
                    job_title=job,
                    is_primary=(i == 0 and not customer.contacts.exists()),
                )
                stats["contacts_new"] += 1
            for raw in phones:
                try:
                    e164 = to_e164(raw)
                except ValidationError:
                    self.stderr.write(self.style.WARNING(f"  skipped invalid number for {pc.name}: {raw}"))
                    continue
                _, created = ContactChannel.objects.get_or_create(
                    contact=contact,
                    type=ChannelType.WHATSAPP,
                    value=e164,
                    defaults={
                        "organization": org,
                        "is_primary": not contact.channels.filter(type=ChannelType.WHATSAPP).exists(),
                    },
                )
                stats["channels_new"] += created
