from datetime import date

import pytest
from django.urls import reverse

from apps.doctors.models import Doctor, ScheduleException, VisitType, WorkingPeriod
from apps.scheduling.availability import periods_for


@pytest.fixture
def world(org_a, make_member):
    owner = make_member(org_a, "owner")
    doctor_user = make_member(org_a, "doctor")
    reception = make_member(org_a, "reception")
    return {"org": org_a, "owner": owner, "doctor_user": doctor_user, "reception": reception}


def test_schedule_settings_permissions(client, world):
    client.force_login(world["reception"])
    assert client.get(reverse("doctors:schedule")).status_code == 403
    client.force_login(world["doctor_user"])
    assert client.get(reverse("doctors:schedule")).status_code == 200
    client.force_login(world["owner"])
    assert client.get(reverse("doctors:schedule")).status_code == 200


def test_schedule_settings_creates_doctor_on_first_visit(client, world):
    assert not Doctor.objects.filter(organization=world["org"]).exists()
    client.force_login(world["owner"])
    client.get(reverse("doctors:schedule"))
    assert Doctor.objects.filter(organization=world["org"]).exists()


def test_save_doctor_booking_settings(client, world):
    client.force_login(world["owner"])
    client.get(reverse("doctors:schedule"))  # creates the doctor
    data = {
        "name_ar": "محمد سامي", "name_en": "", "title_ar": "د.", "specialty_ar": "جراحة",
        "qualifications_text": "بكالوريوس الطب", "booking_mode": "slots", "slot_minutes": 20,
        "booking_horizon_days": 60, "min_notice_minutes": 0, "queue_avg_minutes": 15, "near_turn_threshold": 0,
    }  # fmt: skip
    r = client.post(reverse("doctors:schedule"), data)
    assert r.status_code == 302
    doctor = Doctor.objects.get(organization=world["org"])
    assert doctor.name_ar == "محمد سامي" and doctor.slot_minutes == 20
    assert doctor.qualifications == ["بكالوريوس الطب"]


def test_add_full_week_schedule_from_the_ui(client, world):
    """DoD: 'كل الأيام ما عدا الخميس والجمعة من 2 لـ9 مساءً' enterable from the UI, one request per day."""
    client.force_login(world["owner"])
    client.get(reverse("doctors:schedule"))
    doctor = Doctor.objects.get(organization=world["org"])
    for weekday in range(5):  # Saturday(0) .. Wednesday(4); Thursday(5)/Friday(6) stay closed
        r = client.post(reverse("doctors:period_add"), {"weekday": weekday, "start_time": "14:00", "end_time": "21:00"})
        assert r.status_code == 200
    assert WorkingPeriod.objects.filter(doctor=doctor).count() == 5
    assert periods_for(doctor, date(2026, 1, 8)) == []  # Thursday
    assert len(periods_for(doctor, date(2026, 1, 3))) == 1  # Saturday


def test_invalid_period_shows_error_and_does_not_save(client, world):
    client.force_login(world["owner"])
    client.get(reverse("doctors:schedule"))
    r = client.post(reverse("doctors:period_add"), {"weekday": 0, "start_time": "21:00", "end_time": "14:00"})
    assert r.status_code == 200
    assert "بعد وقت البداية" in r.content.decode()
    assert not WorkingPeriod.objects.exists()


def test_closing_a_day_via_exception(client, world):
    client.force_login(world["owner"])
    client.get(reverse("doctors:schedule"))
    doctor = Doctor.objects.get(organization=world["org"])
    WorkingPeriod.objects.create(
        organization=world["org"], doctor=doctor, weekday=3, start_time="14:00", end_time="21:00"
    )
    holiday = date(2026, 1, 6)
    r = client.post(reverse("doctors:exception_add"), {"date": holiday, "kind": "closed", "reason": "إجازة رسمية"})
    assert r.status_code == 200
    assert periods_for(doctor, holiday) == []
    assert "إجازة رسمية" in r.content.decode()


def test_delete_period_and_exception(client, world):
    client.force_login(world["owner"])
    client.get(reverse("doctors:schedule"))
    doctor = Doctor.objects.get(organization=world["org"])
    wp = WorkingPeriod.objects.create(
        organization=world["org"], doctor=doctor, weekday=0, start_time="14:00", end_time="21:00"
    )
    exc = ScheduleException.objects.create(
        organization=world["org"], doctor=doctor, date=date(2026, 1, 6), kind="closed"
    )
    client.post(reverse("doctors:period_delete", args=[wp.pk]))
    client.post(reverse("doctors:exception_delete", args=[exc.pk]))
    assert not WorkingPeriod.objects.filter(pk=wp.pk).exists()
    assert not ScheduleException.objects.filter(pk=exc.pk).exists()


def test_visit_types_crud(client, world):
    client.force_login(world["owner"])
    client.get(reverse("doctors:schedule"))
    r = client.post(
        reverse("doctors:visit_type_add"),
        {"name_ar": "كشف", "duration_minutes": 20, "price": "400", "free_followup_days": 14,
         "color": "#0E7C86", "is_followup": "", "is_active": "on"},
    )  # fmt: skip
    assert r.status_code == 200
    vt = VisitType.objects.get(organization=world["org"])
    assert vt.name_ar == "كشف" and vt.price == 400 and vt.order == 0
    client.post(reverse("doctors:visit_type_delete", args=[vt.pk]))
    assert not VisitType.objects.filter(pk=vt.pk).exists()


def test_cross_tenant_schedule_isolation(client, world, org_b, make_member):
    client.force_login(world["owner"])
    client.get(reverse("doctors:schedule"))
    doctor = Doctor.objects.get(organization=world["org"])
    wp = WorkingPeriod.objects.create(
        organization=world["org"], doctor=doctor, weekday=0, start_time="14:00", end_time="21:00"
    )
    client.force_login(make_member(org_b, "owner"))
    assert client.post(reverse("doctors:period_delete", args=[wp.pk])).status_code == 404
    assert WorkingPeriod.objects.filter(pk=wp.pk).exists()


def test_seed_org_creates_doctor_schedule_and_visit_types(db):
    from django.core.management import call_command

    call_command("seed_org")
    doctor = Doctor.objects.get()
    assert doctor.name_ar == "سارة علي" and doctor.booking_mode == "slots"
    assert WorkingPeriod.objects.filter(doctor=doctor).count() == 5
    assert VisitType.objects.filter(doctor=doctor).count() == 3
    assert periods_for(doctor, date(2026, 1, 8)) == []  # Thursday: not in the seeded working periods
