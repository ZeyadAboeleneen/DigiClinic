import pytest
from django.core.management import call_command

from apps.patients.models import Patient


def test_seed_demo_creates_30_with_a_shared_phone(org_a):
    call_command("seed_demo", org=org_a.slug)
    patients = Patient.objects.filter(organization=org_a)
    assert patients.count() == 30
    phones = list(patients.values_list("phone", flat=True))
    shared = [p for p in set(phones) if phones.count(p) == 3]
    assert len(shared) == 1

    call_command("seed_demo", org=org_a.slug, remove=True)
    assert not Patient.objects.filter(organization=org_a).exists()


def test_seed_demo_is_safe_to_rerun(org_a):
    call_command("seed_demo", org=org_a.slug)
    call_command("seed_demo", org=org_a.slug)  # a second run would duplicate unless explicitly removed first
    assert Patient.objects.filter(organization=org_a).count() == 60


@pytest.fixture
def xlsx_path(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["الاسم", "الموبايل", "السن", "النوع", "ملاحظات"])
    ws.append(["محمد أحمد", "01012345678", 30, "ذكر", "ملاحظة"])
    ws.append(["سارة علي", "01087654321", 25, "أنثى", ""])
    ws.append(["بدون رقم", "", 10, "ذكر", ""])  # skipped: no phone
    path = tmp_path / "patients.xlsx"
    wb.save(path)
    return str(path)


def test_import_patients(org_a, xlsx_path):
    call_command("import_patients", xlsx_path, org=org_a.slug)
    assert Patient.objects.filter(organization=org_a).count() == 2
    p = Patient.objects.get(organization=org_a, full_name="محمد أحمد")
    assert p.phone == "+201012345678" and p.dob_is_estimated

    # re-running doesn't duplicate
    call_command("import_patients", xlsx_path, org=org_a.slug)
    assert Patient.objects.filter(organization=org_a).count() == 2
