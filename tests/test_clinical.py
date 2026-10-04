import io
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time
from PIL import Image

from apps.audit.models import AuditEvent
from apps.clinical import services
from apps.clinical.models import Attachment, Visit, VisitStatus
from apps.doctors.models import Doctor, VisitType, WorkingPeriod
from apps.patients.models import Allergy, Gender, Patient, PatientFieldDefinition
from apps.scheduling import services as booking
from apps.scheduling.models import AppointmentStatus

pytestmark = pytest.mark.usefixtures("midday")


@pytest.fixture
def world(org_a, org_b, make_member):
    doctor = Doctor.objects.create(organization=org_a, name_ar="سارة", slot_minutes=20)
    for weekday in range(7):
        WorkingPeriod.objects.create(organization=org_a, doctor=doctor, weekday=weekday,
                                     start_time="00:00", end_time="23:59")  # fmt: skip
    vt = VisitType.objects.create(organization=org_a, doctor=doctor, name_ar="كشف", duration_minutes=20,
                                  price=Decimal("300"))  # fmt: skip
    reception = make_member(org_a, "reception")
    patients, appts = [], []
    start = (timezone.now() + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    for n in range(1, 6):
        p = Patient.objects.create(organization=org_a, full_name=f"مريض {n}", phone=f"+2010012345{n:02d}",
                                   gender=Gender.MALE, file_number=n)  # fmt: skip
        a = booking.book(patient=p, doctor=doctor, visit_type=vt, by=reception,
                         start_at=start + timedelta(minutes=20 * n), overbook=True)  # fmt: skip
        booking.transition(a, AppointmentStatus.ARRIVED, by=reception)
        patients.append(p)
        appts.append(a)
    Allergy.objects.create(organization=org_a, patient=patients[0], name="Penicillin", severity="severe")
    return {
        "org": org_a, "doctor": doctor, "vt": vt, "patients": patients, "appts": appts, "reception": reception,
        "doc": make_member(org_a, "doctor"), "other": make_member(org_b, "doctor"),
    }  # fmt: skip


def _png(size=(20, 20)):
    buf = io.BytesIO()
    Image.new("RGB", size, "red").save(buf, "PNG")
    return SimpleUploadedFile("xray.heic.png", buf.getvalue(), content_type="image/png")


# --- doctor flow -----------------------------------------------------------------------------


def test_doctor_sees_five_patients_in_a_row(client, world):
    client.force_login(world["doc"])
    resp = client.post(reverse("clinical:call", args=[world["appts"][0].pk]))
    visit = Visit.objects.get(appointment=world["appts"][0])
    assert resp.url == reverse("clinical:visit", args=[visit.pk])
    for i in range(5):
        visit = Visit.objects.get(appointment=world["appts"][i])
        page = client.get(reverse("clinical:visit", args=[visit.pk])).content.decode()
        assert world["patients"][i].full_name in page
        client.post(reverse("clinical:visit_field", args=[visit.pk]), {"field": "diagnosis", "value": f"برد، صداع {i}"})
        resp = client.post(reverse("clinical:visit_finish", args=[visit.pk]), {"next": "1"})
    assert resp.url == reverse("clinical:desk")  # nobody left
    for a in world["appts"]:
        a.refresh_from_db()
        assert a.status == AppointmentStatus.COMPLETED
    v = Visit.objects.get(appointment=world["appts"][4])
    assert v.status == VisitStatus.FINISHED and v.diagnosis_tags == ["برد", "صداع 4"]
    assert "برد" in services.diagnosis_suggestions(world["doctor"])


def test_allergy_banner_on_the_doctor_screen(client, world):
    client.force_login(world["doc"])
    client.post(reverse("clinical:call", args=[world["appts"][0].pk]))
    visit = Visit.objects.get(appointment=world["appts"][0])
    page = client.get(reverse("clinical:visit", args=[visit.pk])).content.decode()
    assert "Penicillin" in page and "حساسية" in page


def test_autosave_fields_and_validation(client, world):
    PatientFieldDefinition.objects.create(organization=world["org"], key="head", label_ar="محيط الرأس",
                                          type="number", scope="visit")  # fmt: skip
    client.force_login(world["doc"])
    client.post(reverse("clinical:call", args=[world["appts"][0].pk]))
    visit = Visit.objects.get(appointment=world["appts"][0])
    url = reverse("clinical:visit_field", args=[visit.pk])
    assert "اتحفظ" in client.post(url, {"field": "chief_complaint", "value": "كحة من 3 أيام"}).content.decode()
    client.post(url, {"field": "cf_head", "value": "45.5"})
    client.post(url, {"field": "followup_after_days", "value": "7"})
    assert "red" in client.post(url, {"field": "cf_head", "value": "abc"}).content.decode()
    assert "red" in client.post(url, {"field": "status", "value": "finished"}).content.decode()  # not editable
    visit.refresh_from_db()
    assert (visit.chief_complaint, visit.custom_fields, visit.followup_after_days, visit.status) == (
        "كحة من 3 أيام", {"head": 45.5}, 7, VisitStatus.OPEN)  # fmt: skip


def test_tab_closed_mid_typing_text_survives(client, world):
    """sendBeacon posts a plain form (csrf token in the body) — the same endpoint must accept it."""
    client.force_login(world["doc"])
    client.post(reverse("clinical:call", args=[world["appts"][0].pk]))
    visit = Visit.objects.get(appointment=world["appts"][0])
    csrf_client = client.__class__(enforce_csrf_checks=True)
    csrf_client.force_login(world["doc"])
    page = csrf_client.get(reverse("clinical:visit", args=[visit.pk])).content.decode()
    token = page.split('const token = "')[1].split('"')[0]
    resp = csrf_client.post(reverse("clinical:visit_field", args=[visit.pk]),
                            {"csrfmiddlewaretoken": token, "field": "examination", "value": "صدر سليم"})  # fmt: skip
    assert resp.status_code == 200
    visit.refresh_from_db()
    assert visit.examination == "صدر سليم"
    assert "صدر سليم" in csrf_client.get(reverse("clinical:visit", args=[visit.pk])).content.decode()


def test_editing_a_finished_visit_keeps_history(world):
    visit = services.start_visit(world["appts"][0], by=world["doc"])
    services.save_field(visit, "diagnosis", "التهاب", by=world["doc"])
    services.finish_visit(visit, by=world["doc"])
    services.save_field(visit, "diagnosis", "التهاب جيوب", by=world["doc"])
    assert list(visit.records.values_list("diagnosis", flat=True)[:2]) == ["التهاب جيوب", "التهاب"]


# --- permissions -----------------------------------------------------------------------------


# (url, method, object-scoped?) for every clinical URL — built from the urlconf so new views are covered automatically.
def _clinical_urls(world, visit, attachment):
    from apps.clinical import urls as clinical_urls

    post_only = {"lock", "unlock", "call", "visit_field", "visit_finish"}
    out = []
    for pattern in clinical_urls.urlpatterns:
        if pattern.name == "attachment_upload":
            continue  # reception may upload (attachment.upload) — tested separately
        kwargs = {}
        for key in pattern.pattern.converters:
            if key == "appt_pk":
                kwargs[key] = world["appts"][1].pk
            elif pattern.name.startswith("visit"):
                kwargs[key] = visit.pk
            elif pattern.name == "attachment_file":
                kwargs[key] = attachment.pk
            else:
                kwargs[key] = world["patients"][0].pk
        method = "post" if pattern.name in post_only else "get"
        out.append((reverse(f"clinical:{pattern.name}", kwargs=kwargs), method, bool(kwargs)))
    return out


def test_reception_gets_403_on_every_clinical_url(client, world):
    visit = services.start_visit(world["appts"][0], by=world["doc"])
    att = services.add_attachment(patient=world["patients"][0], f=_png(), kind="lab", by=world["doc"])
    urls = _clinical_urls(world, visit, att)
    assert len(urls) >= 13
    client.force_login(world["reception"])
    for url, method, _scoped in urls:
        assert getattr(client, method)(url).status_code == 403, url
    assert client.get(reverse("patients:detail", args=[world["patients"][0].pk])).status_code == 200


def test_clinical_views_are_tenant_scoped(client, world):
    visit = services.start_visit(world["appts"][0], by=world["doc"])
    att = services.add_attachment(patient=world["patients"][0], f=_png(), kind="lab", by=world["doc"])
    client.force_login(world["other"])
    for url, method, scoped in _clinical_urls(world, visit, att):
        if scoped:
            assert getattr(client, method)(url).status_code == 404, url


# --- attachments -----------------------------------------------------------------------------


def test_upload_checks_content_not_extension(client, world):
    client.force_login(world["doc"])
    url = reverse("clinical:attachment_upload", args=[world["patients"][0].pk])
    resp = client.post(url, {"file": _png(), "kind": "radiology", "title": "صدر"})
    att = Attachment.objects.get()
    assert att.content_type == "image/png" and att.file.name.endswith(".png") and "xray" not in att.file.name
    fake = SimpleUploadedFile("report.pdf", b"MZ\x90\x00not a pdf", content_type="application/pdf")
    resp = client.post(url, {"file": fake, "kind": "lab"})
    assert "PDF" in resp.content.decode() and Attachment.objects.count() == 1
    pdf = SimpleUploadedFile("r.pdf", b"%PDF-1.4\n%%EOF", content_type="application/pdf")
    client.post(url, {"file": pdf, "kind": "lab"})
    assert Attachment.objects.filter(content_type="application/pdf").count() == 1
    resp = client.get(reverse("clinical:attachment_file", args=[att.pk]))
    assert resp["Content-Type"] == "image/png" and resp["X-Content-Type-Options"] == "nosniff"


def test_upload_size_limit(world, monkeypatch):
    monkeypatch.setattr(services, "MAX_UPLOAD_BYTES", 10)
    with pytest.raises(services.ValidationError):
        services.add_attachment(patient=world["patients"][0], f=_png(), kind="lab")


def test_reception_can_upload_but_not_view(client, world):
    client.force_login(world["reception"])
    resp = client.post(reverse("clinical:attachment_upload", args=[world["patients"][0].pk]),
                       {"file": _png(), "kind": "lab"})  # fmt: skip
    assert resp.url == reverse("patients:detail", args=[world["patients"][0].pk])
    att = Attachment.objects.get()
    assert client.get(reverse("clinical:attachment_file", args=[att.pk])).status_code == 403


# --- audit & lock ----------------------------------------------------------------------------


def test_opening_a_file_is_audited_once_per_hour(client, world):
    client.force_login(world["doc"])
    url = reverse("clinical:record", args=[world["patients"][0].pk])
    with freeze_time("2026-03-01 10:00:00+02:00"):
        client.get(url)
        client.get(url)
    with freeze_time("2026-03-01 11:05:00+02:00"):
        client.get(url)
    assert AuditEvent.objects.filter(action="patient.clinical_view", target_id=world["patients"][0].pk).count() == 2


def test_idle_lock_is_enforced_server_side(client, world):
    client.force_login(world["doc"])
    client.post(reverse("clinical:lock"))
    resp = client.get(reverse("clinical:record", args=[world["patients"][0].pk]))
    assert resp.status_code == 423 and "مقفولة" in resp.content.decode()
    assert client.post(reverse("clinical:unlock"), {"password": "wrong"}).status_code == 423
    resp = client.post(reverse("clinical:unlock"), {"password": "Strong-Pass-2026!", "next": "/desk/"})
    assert resp.url == "/desk/"
    assert client.get(reverse("clinical:record", args=[world["patients"][0].pk])).status_code == 200
    resp = client.post(reverse("clinical:unlock"), {"password": "Strong-Pass-2026!", "next": "//evil.example"})
    assert resp.url == "/desk/"


# --- follow-ups for reception ----------------------------------------------------------------


def test_followup_task_until_booked(client, world):
    visit = services.start_visit(world["appts"][0], by=world["doc"])
    services.save_field(visit, "followup_after_days", "7")
    services.finish_visit(visit, by=world["doc"])
    client.force_login(world["reception"])
    page = client.get(reverse("reception:today")).content.decode()
    assert "إعادات محتاجة حجز" in page and "مريض 1" in page
    booking.book(patient=world["patients"][0], doctor=world["doctor"], visit_type=world["vt"], by=world["reception"],
                 start_at=timezone.now() + timedelta(days=7), overbook=True)  # fmt: skip
    assert services.followups_due(world["org"]) == []


def test_followup_can_be_dismissed(client, world):
    visit = services.start_visit(world["appts"][0], by=world["doc"])
    services.save_field(visit, "followup_after_days", "7")
    services.finish_visit(visit, by=world["doc"])
    client.force_login(world["reception"])
    client.post(reverse("reception:followup_dismiss", args=[visit.pk]))
    assert services.followups_due(world["org"]) == []


# --- custom patient fields -------------------------------------------------------------------


def test_custom_patient_fields_in_form_and_settings(client, world, make_member):
    owner = make_member(world["org"], "owner")
    client.force_login(world["reception"])
    assert client.get(reverse("patients:fields_settings")).status_code == 403
    client.force_login(owner)
    client.post(reverse("patients:fields_settings"), {"label_ar": "فصيلة الدم", "type": "choice",
                                                      "scope": "patient", "choices_text": "A+، O-"})  # fmt: skip
    d = PatientFieldDefinition.objects.get(label_ar="فصيلة الدم")
    assert d.choices == ["A+", "O-"]
    p = world["patients"][0]
    page = client.get(reverse("patients:edit", args=[p.pk])).content.decode()
    assert "فصيلة الدم" in page
    client.post(reverse("patients:edit", args=[p.pk]), {
        "full_name": p.full_name, "gender": "male", "phone": "01001234501", "whatsapp_same_as_phone": "on",
        "preferred_channel": "whatsapp", f"cf_{d.key}": "O-",
    })  # fmt: skip
    p.refresh_from_db()
    assert p.custom_fields == {d.key: "O-"}


def test_desk_opens_patient_called_in_from_reception(client, world):
    booking.transition(world["appts"][2], AppointmentStatus.IN_CONSULTATION, by=world["reception"])
    client.force_login(world["doc"])
    resp = client.get(reverse("clinical:desk"))
    visit = Visit.objects.get(appointment=world["appts"][2])
    assert resp.url == reverse("clinical:visit", args=[visit.pk]) and visit.started_at


def test_big_phone_photo_is_shrunk_and_rotated(world):
    buf = io.BytesIO()
    Image.new("RGB", (4000, 3000), "white").save(buf, "PNG")
    f = SimpleUploadedFile("scan.png", buf.getvalue(), content_type="image/png")
    att = services.add_attachment(patient=world["patients"][0], f=f, kind="radiology")
    assert att.content_type == "image/jpeg" and att.file.name.endswith(".jpg")
    with Image.open(att.file) as img:
        assert img.size == (2000, 1500)
    assert att.size < len(buf.getvalue())


def test_small_png_is_kept_as_is(world):
    att = services.add_attachment(patient=world["patients"][0], f=_png(), kind="lab")
    assert att.content_type == "image/png"


def test_idle_lock_delay_comes_from_settings(client, world, settings):
    client.force_login(world["doc"])
    assert "const IDLE_MS = 360 * 60 * 1000;" in client.get(reverse("clinical:desk")).content.decode()
    settings.DESK_IDLE_LOCK_MINUTES = 30
    assert "const IDLE_MS = 30 * 60 * 1000;" in client.get(reverse("clinical:desk")).content.decode()
