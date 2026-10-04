import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet
from django.urls import reverse
from django.utils import timezone

from apps.billing.services import record_payment
from apps.clinical import services as clinical
from apps.core import backup, ratelimit
from apps.dashboard import services as reports
from apps.doctors.models import Doctor, VisitType, WorkingPeriod
from apps.messaging.models import Delivery, DeliveryStatus
from apps.notifications.models import Event, MessageStatus, ScheduledMessage
from apps.patients.models import Gender, Patient
from apps.scheduling import services as booking
from apps.scheduling.models import AppointmentStatus

pytestmark = pytest.mark.usefixtures("midday")


@pytest.fixture
def clinic(org_a, org_b, make_member):
    doctor = Doctor.objects.create(organization=org_a, name_ar="سارة", slot_minutes=20)
    for weekday in range(7):
        WorkingPeriod.objects.create(organization=org_a, doctor=doctor, weekday=weekday,
                                     start_time="00:00", end_time="23:59")  # fmt: skip
    vt = VisitType.objects.create(organization=org_a, doctor=doctor, name_ar="كشف", duration_minutes=20,
                                  price=Decimal("300"))  # fmt: skip
    return {
        "org": org_a, "doctor": doctor, "vt": vt, "reception": make_member(org_a, "reception"),
        "doc": make_member(org_a, "doctor"), "owner": make_member(org_a, "owner"), "admin": make_member(org_a, "admin"),
        "other": make_member(org_b, "owner"),
    }  # fmt: skip


def _patient(clinic, n):
    return Patient.objects.create(organization=clinic["org"], full_name=f"مريض {n}", phone=f"+20100123{n:04d}",
                                  gender=Gender.MALE, file_number=n)  # fmt: skip


def _appt(clinic, patient, minutes):
    return booking.book(patient=patient, doctor=clinic["doctor"], visit_type=clinic["vt"], by=clinic["reception"],
                        start_at=timezone.now() + timedelta(minutes=minutes), overbook=True)  # fmt: skip


# --- reports (03 §3.9) -----------------------------------------------------------------------


def test_report_numbers(clinic):
    staff = clinic["doc"]
    done = []
    for n in range(3):
        a = _appt(clinic, _patient(clinic, n + 1), 30 + n * 20)
        booking.transition(a, AppointmentStatus.ARRIVED, by=staff)
        v = clinical.start_visit(a, by=staff)
        clinical.save_field(v, "diagnosis", "برد، صداع" if n < 2 else "برد")
        clinical.finish_visit(v, by=staff)
        record_payment(a, amount=300, method="cash" if n < 2 else "instapay", by=staff)
        done.append(a)
    missed = _appt(clinic, _patient(clinic, 9), 200)
    booking.transition(missed, AppointmentStatus.NO_SHOW, by=staff)
    record_payment(done[0], amount=50, by=staff, is_refund=True)
    msg = ScheduledMessage.objects.create(organization=clinic["org"], appointment=done[0], event=Event.REMINDER,
                                          channel="whatsapp", send_at=timezone.now(), status=MessageStatus.SENT,
                                          sent_at=timezone.now(), dedupe_key="r1")  # fmt: skip
    Delivery.objects.create(organization=clinic["org"], scheduled_message=msg, channel="whatsapp", recipient="x",
                            status=DeliveryStatus.READ)  # fmt: skip

    day = reports.today()
    r = reports.report(clinic["org"], day, day)
    assert (r.visits, r.no_shows, r.new_patients) == (3, 1, 4)
    assert r.no_show_rate == 0.25
    assert r.revenue_total == Decimal("850")
    assert dict(r.revenue_by_method) == {"كاش": Decimal("550"), "InstaPay": Decimal("300")}
    assert r.top_diagnoses[:2] == [("برد", 3), ("صداع", 2)]
    assert (r.reminders_sent, r.reminders_delivered_rate, r.reminders_read_rate) == (1, 1.0, 1.0)
    assert r.per_day == [(day, 3, 1, Decimal("850"))]


def test_dashboard_by_role_and_reports_permission(client, clinic):
    client.force_login(clinic["reception"])
    html = client.get(reverse("dashboard:home")).content.decode()
    assert "إعادات محتاجة حجز" in html and "الشهر ده" not in html
    assert client.get(reverse("dashboard:reports")).status_code == 403
    client.force_login(clinic["admin"])
    assert client.get(reverse("dashboard:reports")).status_code == 403  # finance is doctor/owner (04 §4.3)
    client.force_login(clinic["owner"])
    assert "الشهر ده" in client.get(reverse("dashboard:home")).content.decode()
    resp = client.get(reverse("dashboard:reports"), {"from": "2026-01-01", "to": "2025-12-01"})  # swapped is ok
    assert resp.status_code == 200


def test_reports_are_tenant_scoped(client, clinic):
    a = _appt(clinic, _patient(clinic, 1), 30)
    record_payment(a, amount=300, by=clinic["doc"])
    client.force_login(clinic["other"])
    r = client.get(reverse("dashboard:reports")).context["r"]
    assert r.revenue_total == 0 and r.new_patients == 0


# --- security review fixes (04) ---------------------------------------------------------------


def test_patient_search_is_rate_limited(client, clinic, monkeypatch):
    from django.core.cache import cache

    cache.clear()
    client.force_login(clinic["reception"])
    monkeypatch.setattr(ratelimit, "SEARCH_PER_MINUTE", 3)
    from apps.patients import views as patient_views

    # Re-wrap with the lower limit so the test doesn't need 121 requests.
    limited = ratelimit.ratelimit("test-search", per_minute=3)(patient_views.patient_list.__wrapped__)
    from django.test import RequestFactory

    rf = RequestFactory()
    statuses = []
    for _ in range(5):
        req = rf.get("/patients/?q=010")
        req.user, req.organization, req.membership, req.htmx = clinic["reception"], clinic["org"], None, False
        statuses.append(limited(req).status_code)
    assert statuses == [200, 200, 200, 429, 429]
    # the real views are wired to the shared limiter
    for view in ("patients:list", "scheduling:booking_search", "clinical:search", "reception:walk_in"):
        from django.urls import resolve

        assert getattr(resolve(reverse(view)).func, "ratelimit_key", None) == "patient-search", view


def test_security_headers_and_same_origin_print_frame(client, clinic):
    client.force_login(clinic["reception"])
    resp = client.get(reverse("dashboard:home"))
    csp = resp["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "object-src 'none'" in csp and "frame-ancestors 'self'" in csp
    assert resp["X-Frame-Options"] == "SAMEORIGIN"  # the prescription print iframe (05 §5.4)
    assert resp["Referrer-Policy"] == "same-origin"


def test_consent_records_who_and_resets_when_withdrawn(client, clinic):
    client.force_login(clinic["reception"])
    p = _patient(clinic, 1)
    data = {"full_name": p.full_name, "gender": "male", "phone": "01001230001", "whatsapp_same_as_phone": "on",
            "preferred_channel": "whatsapp", "messaging_consent": "on"}  # fmt: skip
    client.post(reverse("patients:edit", args=[p.pk]), data)
    p.refresh_from_db()
    assert p.consent_recorded_by == clinic["reception"] and p.consent_at
    client.post(reverse("patients:edit", args=[p.pk]), {**data, "messaging_consent": ""})
    p.refresh_from_db()
    assert (p.messaging_consent, p.consent_at, p.consent_recorded_by) == (False, None, None)


def test_patient_export_pdf_permissions(client, clinic, monkeypatch):
    from apps.documents import pdf as engine

    monkeypatch.setattr(engine, "render_pdf", lambda html, **kw: b"%PDF-export " + html.encode()[:0])
    p = _patient(clinic, 1)
    url = reverse("clinical:export", args=[p.pk])
    client.force_login(clinic["reception"])
    assert client.get(url).status_code == 403
    client.force_login(clinic["other"])
    assert client.get(url).status_code == 404
    client.force_login(clinic["doc"])
    resp = client.get(url)
    assert resp.status_code == 200 and resp["Content-Type"] == "application/pdf"
    from apps.audit.models import AuditEvent

    assert AuditEvent.objects.filter(action="patient.exported", target_id=p.pk).exists()


# --- backup / restore (07 §7.5) ---------------------------------------------------------------


def test_backup_file_is_encrypted_and_pruned(tmp_path, settings, monkeypatch):
    settings.BACKUP_DIR = str(tmp_path)
    monkeypatch.setattr(backup, "_run", lambda cmd, env: open(cmd[cmd.index("-f") + 1], "wb").write(b"PGDMP fake"))
    path = backup.create_backup(now=timezone.now())
    raw = path.read_bytes()
    assert b"PGDMP" not in raw and b"manifest" not in raw  # encrypted
    assert backup.read_backup(path)[:2] == b"\x1f\x8b"  # gzip inside
    settings.BACKUP_KEY = Fernet.generate_key().decode()
    with pytest.raises(backup.BackupError):
        backup.read_backup(path)  # wrong key
    for i in range(16):
        (tmp_path / f"digiclinic-202601{i + 1:02d}-000000.dcbak").write_bytes(b"x")
    removed = backup.prune(keep=14)
    assert len(removed) == 3 and backup.latest() == path
    assert backup.backed_up_today()


@pytest.mark.django_db(transaction=True)
def test_real_backup_and_restore_into_an_empty_database(tmp_path, settings, org_a):
    """The Phase 9 restore drill, automated: pg_dump → encrypted file → pg_restore into a brand-new database."""
    settings.BACKUP_DIR = str(tmp_path / "backups")
    for n in range(3):
        Patient.objects.create(organization=org_a, full_name=f"مريض {n}", phone="+201001234567",
                               gender=Gender.MALE, file_number=n + 1)  # fmt: skip
    path = backup.create_backup()
    target = f"test_restore_{uuid.uuid4().hex[:8]}"
    try:
        manifest = backup.restore_backup(path, database=target, media_root=tmp_path / "media", create_db=True)
        assert manifest["format"] == backup.FORMAT_VERSION
        assert backup.count_rows(target, ["patients_patient", "organizations_organization"]) == {
            "patients_patient": 3,
            "organizations_organization": 1,
        }
        with pytest.raises(backup.BackupError):  # never over a database that has data
            backup.restore_backup(path, database=target, media_root=tmp_path / "media2")
    finally:
        backup.drop_database(target)


def test_daily_backup_job_runs_once_a_day(settings, monkeypatch, tmp_path):
    from apps.notifications.management.commands import run_scheduler

    settings.AUTO_BACKUP = True
    settings.BACKUP_DIR = str(tmp_path)
    calls = []

    def fake_create(now=None):
        calls.append(now)
        p = tmp_path / f"digiclinic-{timezone.localtime(now):%Y%m%d-%H%M%S}.dcbak"
        p.write_bytes(b"x")
        return p

    monkeypatch.setattr(backup, "create_backup", fake_create)
    now = timezone.now()
    run_scheduler._daily_backup(now)
    run_scheduler._daily_backup(now + timedelta(hours=1))
    assert len(calls) == 1
    run_scheduler._daily_backup(now + timedelta(days=1))
    assert len(calls) == 2


def test_month_bounds():
    assert reports.month_bounds(date(2026, 2, 14)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert reports.month_bounds(date(2026, 12, 31)) == (date(2026, 12, 1), date(2026, 12, 31))


def test_patient_page_shows_bookings_messages_payments_by_permission(client, clinic):
    from apps.notifications.models import InboundMessage

    p = _patient(clinic, 1)
    a = _appt(clinic, p, 30)
    record_payment(a, amount=300, by=clinic["doc"])
    InboundMessage.objects.create(organization=clinic["org"], patient=p, from_number=p.phone, body="1 تأكيد",
                                  provider_message_id="in-1")  # fmt: skip
    client.force_login(clinic["reception"])
    html = client.get(reverse("patients:detail", args=[p.pk])).content.decode()
    assert "الحجوزات" in html and "المدفوعات" in html and "1 تأكيد" in html
    client.force_login(clinic["admin"])  # admin: messages yes, payments no (04 §4.3)
    html = client.get(reverse("patients:detail", args=[p.pk])).content.decode()
    assert "1 تأكيد" in html and "المدفوعات" not in html
