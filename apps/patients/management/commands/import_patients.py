"""Import patients from an old clinic spreadsheet.

Expected header row (any order, matched by text): الاسم | الموبايل | السن | النوع | ملاحظات
Never overwrites an existing patient — only creates new rows (matched by phone+name), so it's safe to
re-run. File numbers are assigned in sheet order.

    python manage.py import_patients sheet.xlsx --org demo-clinic
"""

from datetime import date

import openpyxl
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.core.phones import to_e164
from apps.organizations.models import Organization
from apps.patients import services
from apps.patients.models import Gender, Patient

HEADER_MAP = {"الاسم": "name", "الموبايل": "phone", "السن": "age", "النوع": "gender", "ملاحظات": "notes"}
GENDER_MAP = {"ذكر": Gender.MALE, "أنثى": Gender.FEMALE, "انثى": Gender.FEMALE}


def _text(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return " ".join(str(v).split())


class Command(BaseCommand):
    help = "Import patients from an xlsx sheet (الاسم، الموبايل، السن، النوع، ملاحظات). Idempotent."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--org", required=True)

    def handle(self, *args, **opts):
        try:
            org = Organization.objects.get(slug=opts["org"])
        except Organization.DoesNotExist as e:
            raise CommandError(f"Organization '{opts['org']}' not found.") from e

        wb = openpyxl.load_workbook(opts["path"], data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            self.stdout.write("Empty sheet.")
            return
        header = [_text(c) for c in rows[0]]
        columns = {HEADER_MAP[h]: i for i, h in enumerate(header) if h in HEADER_MAP}
        if "name" not in columns or "phone" not in columns:
            raise CommandError(f"الشيت محتاج عمود 'الاسم' و'الموبايل'. اللي لقيته: {header}")

        created = skipped = 0
        for row in rows[1:]:
            name = _text(row[columns["name"]]) if columns.get("name") is not None else ""
            phone_raw = _text(row[columns["phone"]]) if columns.get("phone") is not None else ""
            if not name or not phone_raw:
                continue
            try:
                phone = to_e164(phone_raw)
            except ValidationError:
                self.stderr.write(f"رقم غلط اتجوّز: {name} — {phone_raw}")
                skipped += 1
                continue
            if Patient.objects.filter(organization=org, full_name=name, phone=phone).exists():
                skipped += 1
                continue

            patient = Patient(organization=org, full_name=name, phone=phone, gender=Gender.MALE)
            if columns.get("gender") is not None:
                patient.gender = GENDER_MAP.get(_text(row[columns["gender"]]), Gender.MALE)
            if columns.get("age") is not None:
                age_raw = row[columns["age"]]
                if isinstance(age_raw, int | float):
                    today = date.today()
                    patient.date_of_birth = today.replace(year=today.year - int(age_raw))
                    patient.dob_is_estimated = True
            if columns.get("notes") is not None:
                patient.notes = _text(row[columns["notes"]])
            services.create_patient(org, patient)
            created += 1

        self.stdout.write(self.style.SUCCESS(f"{created} اتضافوا، {skipped} اتعدّوا (موجودين أو رقمهم غلط)."))
