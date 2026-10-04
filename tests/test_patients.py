import threading

import pytest
from django.db import connection
from django.urls import reverse

from apps.patients import services
from apps.patients.models import ChronicCondition, Gender, Patient


@pytest.fixture
def world(org_a, make_member):
    owner = make_member(org_a, "owner")
    doctor_user = make_member(org_a, "doctor")
    reception = make_member(org_a, "reception")
    return {"org": org_a, "owner": owner, "doctor_user": doctor_user, "reception": reception}


def _patient(org, name, phone, **kw):
    return services.create_patient(org, Patient(organization=org, full_name=name, phone=phone, **kw))


# --- search ----------------------------------------------------------------------------------


def test_search_by_partial_phone(org_a):
    p = _patient(org_a, "محمد أحمد", "+201001234567", gender=Gender.MALE)
    assert p in Patient.objects.for_org(org_a).search("0100 123")
    assert p not in Patient.objects.for_org(org_a).search("0100 999")


def test_search_by_name_ignores_hamza(org_a):
    p = _patient(org_a, "محمد أحمد", "+201001234567", gender=Gender.MALE)
    assert p in Patient.objects.for_org(org_a).search("محمد احمد")


def test_search_by_file_number(org_a):
    p = _patient(org_a, "محمد أحمد", "+201001234567", gender=Gender.MALE)
    assert p in Patient.objects.for_org(org_a).search(str(p.file_number))


def test_one_phone_number_shows_all_three_patients(org_a):
    phone = "+201009990000"
    family = [_patient(org_a, n, phone, gender=Gender.MALE) for n in ["أحمد الأب", "محمد الابن", "سارة الابنة"]]
    results = Patient.objects.for_org(org_a).search("0100 999 0000")
    assert set(results) == set(family)


# --- file numbers ------------------------------------------------------------------------------


def test_file_numbers_increment_per_org(org_a, org_b):
    a1 = _patient(org_a, "واحد", "+201000000001", gender=Gender.MALE)
    a2 = _patient(org_a, "اتنين", "+201000000002", gender=Gender.MALE)
    b1 = _patient(org_b, "تلاتة", "+201000000003", gender=Gender.MALE)
    assert a1.file_number == 1 and a2.file_number == 2
    assert b1.file_number == 1  # separate sequence per org


@pytest.mark.django_db(transaction=True)
def test_concurrent_file_numbers_never_collide(org_a):
    errors = []

    def worker(i):
        try:
            _patient(org_a, f"مريض {i}", f"+20100000{i:04d}", gender=Gender.MALE)
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)
        finally:
            connection.close()

    barrier_count = 8
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(barrier_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    numbers = sorted(Patient.objects.filter(organization=org_a).values_list("file_number", flat=True))
    assert numbers == list(range(1, barrier_count + 1))


# --- views & permissions ----------------------------------------------------------------------


def test_reception_cannot_see_medical_data(client, world):
    patient = _patient(world["org"], "محمد أحمد", "+201001234567", gender=Gender.MALE)
    ChronicCondition.objects.create(organization=world["org"], patient=patient, name="سكر")

    client.force_login(world["reception"])
    html = client.get(reverse("patients:detail", args=[patient.pk])).content.decode()
    assert "سكر" not in html
    assert "الأمراض المزمنة" not in html

    client.force_login(world["doctor_user"])
    html = client.get(reverse("patients:detail", args=[patient.pk])).content.decode()
    assert "سكر" in html and "الأمراض المزمنة" in html


def test_reception_sees_allergy_banner(client, world):
    from apps.patients.models import Allergy

    patient = _patient(world["org"], "محمد أحمد", "+201001234567", gender=Gender.MALE)
    Allergy.objects.create(organization=world["org"], patient=patient, name="بنسلين")
    client.force_login(world["reception"])
    html = client.get(reverse("patients:detail", args=[patient.pk])).content.decode()
    assert "بنسلين" in html


def test_reception_cannot_add_chronic_condition(client, world):
    patient = _patient(world["org"], "محمد أحمد", "+201001234567", gender=Gender.MALE)
    client.force_login(world["reception"])
    r = client.post(reverse("patients:condition_add", args=[patient.pk]), {"name": "سكر"})
    assert r.status_code == 403
    assert not ChronicCondition.objects.exists()


def test_viewer_cannot_see_patients(client, world, make_member):
    viewer = make_member(world["org"], "viewer")
    client.force_login(viewer)
    assert client.get(reverse("patients:list")).status_code == 403


def test_add_patient_from_the_ui(client, world):
    client.force_login(world["reception"])
    data = {
        "full_name": "منى حسن", "gender": "female", "phone": "01011112222", "whatsapp": "",
        "whatsapp_same_as_phone": "on", "email": "", "guardian_name": "", "address": "", "occupation": "",
        "preferred_channel": "whatsapp", "messaging_consent": "on", "notes": "",
    }  # fmt: skip
    r = client.post(reverse("patients:add"), data)
    assert r.status_code == 302
    p = Patient.objects.get(organization=world["org"])
    assert p.full_name == "منى حسن" and p.phone == "+201011112222" and p.whatsapp == "+201011112222"
    assert p.file_number == 1


def test_duplicate_phone_shows_warning_but_does_not_block(client, world):
    _patient(world["org"], "الأول", "+201011112222", gender=Gender.MALE)
    client.force_login(world["reception"])
    r = client.get(reverse("patients:duplicates_check"), {"phone": "01011112222"})
    assert "الأول" in r.content.decode()
    data = {
        "full_name": "التاني", "gender": "male", "phone": "01011112222", "whatsapp": "",
        "whatsapp_same_as_phone": "on", "email": "", "guardian_name": "", "address": "", "occupation": "",
        "preferred_channel": "whatsapp", "messaging_consent": "",
    }  # fmt: skip
    r = client.post(reverse("patients:add"), data)
    assert r.status_code == 302
    assert Patient.objects.filter(organization=world["org"]).count() == 2


def test_merge_moves_allergies_and_deactivates_duplicate(world):
    from apps.patients.models import Allergy

    primary = _patient(world["org"], "الأصلي", "+201000000001", gender=Gender.MALE)
    duplicate = _patient(world["org"], "المكرر", "+201000000001", gender=Gender.MALE)
    Allergy.objects.create(organization=world["org"], patient=duplicate, name="بنسلين")

    merged = services.merge_patients(primary, duplicate)
    assert merged.pk == primary.pk
    duplicate.refresh_from_db()
    assert duplicate.merged_into_id == primary.pk and not duplicate.is_active
    assert primary.allergies.filter(name="بنسلين").exists()


def test_cross_tenant_patient_isolation(client, world, org_b, make_member):
    patient = _patient(world["org"], "محمد أحمد", "+201001234567", gender=Gender.MALE)
    client.force_login(make_member(org_b, "owner"))
    assert client.get(reverse("patients:detail", args=[patient.pk])).status_code == 404
    assert client.get(reverse("patients:edit", args=[patient.pk])).status_code == 404
