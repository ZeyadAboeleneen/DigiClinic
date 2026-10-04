import base64
import io
import json
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time

from apps.clinical import services as clinical
from apps.doctors.models import Doctor, VisitType, WorkingPeriod
from apps.notifications import dispatcher as dispatch
from apps.notifications.management.commands.seed_notifications import seed_org_notifications
from apps.notifications.models import Event, MessageStatus, NotificationSettings, ScheduledMessage
from apps.patients.models import Allergy, Gender, Patient
from apps.prescriptions import pdf as rx_pdf
from apps.prescriptions import safety, services
from apps.prescriptions.models import Drug, Prescription, PrescriptionSettings, RxStatus
from apps.scheduling import services as booking
from apps.scheduling.models import AppointmentStatus

pytestmark = pytest.mark.usefixtures("midday")

FAKE_PDF = b"%PDF-1.4 fake"


@pytest.fixture(autouse=True)
def fake_render(monkeypatch):
    """Chromium isn't needed for logic tests; `test_real_pdf_render` covers the real engine."""
    calls = []

    def render(rx, mode="full"):
        calls.append(mode)
        return FAKE_PDF

    monkeypatch.setattr(rx_pdf, "render_prescription", render)
    return calls


@pytest.fixture
def world(org_a, org_b, make_member):
    call_command("import_drugs", org=org_a.slug, stdout=io.StringIO())
    seed_org_notifications(org_a)
    doctor = Doctor.objects.create(organization=org_a, name_ar="سارة علي", title_ar="د.", slot_minutes=20,
                                   qualifications=["ماجستير الباطنة"])  # fmt: skip
    for weekday in range(7):
        WorkingPeriod.objects.create(organization=org_a, doctor=doctor, weekday=weekday,
                                     start_time="00:00", end_time="23:59")  # fmt: skip
    vt = VisitType.objects.create(organization=org_a, doctor=doctor, name_ar="كشف", duration_minutes=20,
                                  price=Decimal("300"))  # fmt: skip
    patient = Patient.objects.create(organization=org_a, full_name="محمد أحمد", phone="+201001234567",
                                     email="m@example.com", gender=Gender.MALE, file_number=1,
                                     messaging_consent=True)  # fmt: skip
    doc = make_member(org_a, "doctor")
    reception = make_member(org_a, "reception")
    appt = booking.book(patient=patient, doctor=doctor, visit_type=vt, by=reception,
                        start_at=timezone.now() + timedelta(hours=1), overbook=True)  # fmt: skip
    booking.transition(appt, AppointmentStatus.ARRIVED, by=reception)
    visit = clinical.start_visit(appt, by=doc)
    return {"org": org_a, "doctor": doctor, "vt": vt, "patient": patient, "doc": doc, "reception": reception,
            "visit": visit, "other": make_member(org_b, "doctor")}  # fmt: skip


def _drug(world, name):
    return Drug.objects.get(organization=world["org"], name=name)


def _rx_with(world, *names):
    rx = services.draft_for_visit(world["visit"])
    for n in names:
        services.add_item(rx, drug=_drug(world, n))
    return rx


# --- catalog ---------------------------------------------------------------------------------


def test_import_drugs_is_idempotent_and_keeps_ui_edits(world):
    d = _drug(world, "Augmentin 1g")
    d.default_instructions = "تعديل من الواجهة"
    d.save()
    call_command("import_drugs", org=world["org"].slug)
    d.refresh_from_db()
    assert d.default_instructions == "تعديل من الواجهة"
    assert Drug.objects.filter(organization=world["org"]).count() == 32
    assert d.aliases_ar == ["اوجمنتين", "اوجمنتن"]


def test_search_by_arabic_alias_and_usage_order(world):
    names = [d.name for d in services.search_drugs(world["org"], "اوجمنتين")]
    assert names[:2] == ["Augmentin 1g", "Augmentin 457 susp"]


# --- safety ----------------------------------------------------------------------------------


def test_penicillin_allergy_warns_for_amoxicillin_and_blocks_until_acknowledged(world):
    Allergy.objects.create(organization=world["org"], patient=world["patient"], name="Penicillin", severity="severe")
    rx = _rx_with(world, "Augmentin 1g", "Panadol 500")
    warnings = safety.warnings_for(rx)
    assert [w["kind"] for w in warnings] == ["allergy"]
    assert "Augmentin 1g" in warnings[0]["text"]
    with pytest.raises(services.PrescriptionError):
        services.finalize(rx, by=world["doc"])
    rx = services.finalize(rx, by=world["doc"], acknowledged=[warnings[0]["key"]])
    assert rx.status == RxStatus.FINAL and rx.allergy_ack == [warnings[0]["key"]]


def test_arabic_allergy_name_and_duplicates(world):
    Allergy.objects.create(organization=world["org"], patient=world["patient"], name="حساسية بنسلين")
    rx = _rx_with(world, "Flumox 500", "Panadol 500", "Panadol 500")
    kinds = sorted(w["kind"] for w in safety.warnings_for(rx))
    assert kinds == ["allergy", "duplicate"]


def test_unrelated_allergy_does_not_warn(world):
    Allergy.objects.create(organization=world["org"], patient=world["patient"], name="Sulfa")
    assert safety.warnings_for(_rx_with(world, "Augmentin 1g", "Zithromax 500")) == []


# --- lifecycle -------------------------------------------------------------------------------


def test_numbering_snapshots_and_immutability(world):
    rx = services.finalize(_rx_with(world, "Panadol 500"), by=world["doc"])
    year = timezone.localtime(rx.issued_at).year
    assert rx.number == f"RX-{year}-00001"
    assert rx.patient_snapshot["name"] == "محمد أحمد" and rx.doctor_snapshot["name_ar"] == "د. سارة علي"
    with pytest.raises(services.PrescriptionError):
        services.add_item(rx, drug=_drug(world, "Brufen 400"))
    assert _drug(world, "Panadol 500").usage_count == 1

    rev = services.revise(rx, by=world["doc"])
    assert services.revise(rx, by=world["doc"]) == rev  # one open revision at a time
    services.add_item(rev, drug=_drug(world, "Brufen 400"))
    rev = services.finalize(rev, by=world["doc"])
    assert rev.display_number == f"RX-{year}-00001-R1" and rev.items.count() == 2
    rx.refresh_from_db()
    assert rx.status == RxStatus.FINAL and rx.items.count() == 1  # original untouched

    second = services.draft_for_visit(world["visit"])
    services.add_item(second, drug_name="Free text drug")
    assert services.finalize(second, by=world["doc"]).number == f"RX-{year}-00002"


def test_template_and_repeat_last_append_lines(world):
    rx = _rx_with(world, "Augmentin 1g", "Panadol 500")
    services.update_fields(rx, advice="راحة")
    tpl = services.save_as_template(rx, "برد — كبار")
    services.finalize(rx, by=world["doc"])

    rx2 = services.draft_for_visit(world["visit"])
    services.add_item(rx2, drug=_drug(world, "Brufen 400"))
    services.apply_template(rx2, tpl)
    assert [i.drug_name for i in rx2.items.all()] == ["Brufen 400", "Augmentin 1g", "Panadol 500"]
    assert rx2.advice == "راحة"

    rx3 = Prescription.objects.create(organization=world["org"], visit=world["visit"], patient=world["patient"],
                                      doctor=world["doctor"])  # fmt: skip
    services.repeat_last(rx3)
    assert [i.drug_name for i in rx3.items.all()] == ["Augmentin 1g", "Panadol 500"]


def test_dictated_lines_must_be_confirmed(world):
    rx = services.draft_for_visit(world["visit"])
    item = services.add_item(rx, drug=_drug(world, "Panadol 500"), needs_review=True)
    with pytest.raises(services.PrescriptionError):
        services.finalize(rx, by=world["doc"])
    services.confirm_item(item)
    assert services.finalize(rx, by=world["doc"]).status == RxStatus.FINAL


def test_finish_visit_requires_finalized_prescription(world):
    _rx_with(world, "Panadol 500")
    with pytest.raises(clinical.VisitError):
        clinical.finish_visit(world["visit"], by=world["doc"])


# --- sending after the visit (05 §5.6) ---------------------------------------------------------


@pytest.fixture
def gateway(settings):
    sent = []

    def handler(request):
        if "/check/" in request.url.path:
            return httpx.Response(200, json={"registered": True})
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "MSG"})

    settings.WA_GATEWAY_TRANSPORT = httpx.MockTransport(handler)
    return sent


@freeze_time("2026-10-05 12:00:00+03:00")  # outside quiet hours: sending is immediate
def test_prescription_sent_as_pdf_after_visit(world, gateway, django_capture_on_commit_callbacks):
    NotificationSettings.objects.filter(organization=world["org"]).update(send_prescription_after_visit=True)
    with django_capture_on_commit_callbacks(execute=True):
        rx = services.finalize(_rx_with(world, "Panadol 500"), by=world["doc"])
    rx.refresh_from_db()
    assert rx.pdf_full.name.endswith(".pdf")
    with django_capture_on_commit_callbacks(execute=True):
        clinical.finish_visit(world["visit"], by=world["doc"])
        clinical.finish_visit(world["visit"], by=world["doc"])  # idempotent
    [row] = ScheduledMessage.objects.filter(event=Event.PRESCRIPTION)
    assert row.prescription == rx and row.status == MessageStatus.PENDING
    dispatch.Dispatcher().run_once()
    row.refresh_from_db()
    assert row.status == MessageStatus.SENT
    [msg] = gateway
    assert base64.b64decode(msg["pdf_base64"]) == FAKE_PDF
    assert msg["filename"].startswith("روشتة-محمد-أحمد-") and rx.display_number in msg["caption"]


def test_no_sending_when_disabled_or_unticked(world, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        services.finalize(_rx_with(world, "Panadol 500"), by=world["doc"])
        clinical.finish_visit(world["visit"], by=world["doc"])
    assert not ScheduledMessage.objects.filter(event=Event.PRESCRIPTION).exists()


@freeze_time("2026-10-05 12:00:00+03:00")
def test_prescription_by_email_has_attachment(world, django_capture_on_commit_callbacks, mailoutbox):
    from apps.messaging.models import SendingChannelConfig

    cfg = SendingChannelConfig.for_org(world["org"], "email")
    cfg.config = {"host": "smtp.example.com", "username": "c@example.com", "password": "x"}
    cfg.is_active = True
    cfg.save()
    NotificationSettings.objects.filter(organization=world["org"]).update(
        send_prescription_after_visit=True, prescription_channel="email"
    )
    with django_capture_on_commit_callbacks(execute=True):
        services.finalize(_rx_with(world, "Panadol 500"), by=world["doc"])
        clinical.finish_visit(world["visit"], by=world["doc"])
    dispatch.Dispatcher().run_once()
    [mail] = mailoutbox
    assert mail.attachments[0][1] == FAKE_PDF and mail.attachments[0][2] == "application/pdf"


# --- views & permissions ---------------------------------------------------------------------


def test_builder_flow_through_views(client, world, fake_render):
    client.force_login(world["doc"])
    html = client.get(reverse("prescriptions:builder", args=[world["visit"].pk])).content.decode()
    rx = Prescription.objects.get(visit=world["visit"])
    assert "اعتماد وطباعة" in html
    assert (
        "Augmentin 1g"
        in client.get(reverse("prescriptions:drug_search", args=[rx.pk]), {"drug_name": "aug"}).content.decode()
    )
    client.post(reverse("prescriptions:item_add", args=[rx.pk]), {"drug": _drug(world, "Augmentin 1g").pk})
    client.post(reverse("prescriptions:item_add", args=[rx.pk]), {"drug_name": "Vitamin D3 50,000 IU", "free": "1"})
    item = rx.items.get(drug_name="Vitamin D3 50,000 IU")
    client.post(
        reverse("prescriptions:item_update", args=[item.pk]), {"field": "instructions", "value": "كبسولة أسبوعيًا"}
    )
    client.post(reverse("prescriptions:item_action", args=[item.pk, "up"]))
    assert [i.drug_name for i in rx.items.all()] == ["Vitamin D3 50,000 IU", "Augmentin 1g"]
    html = client.post(reverse("prescriptions:action", args=[rx.pk, "finalize"])).content.decode()
    assert "printRx(" in html  # print dialog triggered right away
    rx.refresh_from_db()
    assert rx.status == RxStatus.FINAL
    resp = client.get(reverse("prescriptions:pdf", args=[rx.pk]) + "?mode=print")
    assert resp["Content-Type"] == "application/pdf" and resp.content == FAKE_PDF


def test_print_uses_preprinted_mode_but_patient_copy_is_full(
    client, world, fake_render, django_capture_on_commit_callbacks
):
    PrescriptionSettings.objects.update_or_create(organization=world["org"], defaults={"print_mode": "preprinted"})
    with django_capture_on_commit_callbacks(execute=True):
        rx = services.finalize(_rx_with(world, "Panadol 500"), by=world["doc"])
    client.force_login(world["doc"])
    client.get(reverse("prescriptions:pdf", args=[rx.pk]) + "?mode=print")
    assert fake_render == ["full", "preprinted"]  # stored full copy at finalize, preprinted for printing


def test_reception_cannot_write_and_reprints_only_when_allowed(client, world):
    rx = services.finalize(_rx_with(world, "Panadol 500"), by=world["doc"])
    client.force_login(world["reception"])
    assert client.get(reverse("prescriptions:builder", args=[world["visit"].pk])).status_code == 403
    assert client.post(reverse("prescriptions:action", args=[rx.pk, "revise"])).status_code == 403
    assert client.get(reverse("prescriptions:pdf", args=[rx.pk])).status_code == 403
    PrescriptionSettings.objects.update_or_create(organization=world["org"], defaults={"reception_can_reprint": True})
    assert client.get(reverse("prescriptions:pdf", args=[rx.pk])).status_code == 200
    listing = client.get(reverse("prescriptions:patient_list", args=[world["patient"].pk])).content.decode()
    assert rx.number in listing and "Panadol" not in listing  # numbers only, no drugs for reception


def test_prescription_views_are_tenant_scoped(client, world):
    rx = services.finalize(_rx_with(world, "Panadol 500"), by=world["doc"])
    item = rx.items.first()
    client.force_login(world["other"])
    assert client.get(reverse("prescriptions:builder", args=[world["visit"].pk])).status_code == 404
    assert client.get(reverse("prescriptions:pdf", args=[rx.pk])).status_code == 404
    assert client.post(reverse("prescriptions:action", args=[rx.pk, "revise"])).status_code == 404
    assert client.post(reverse("prescriptions:item_update", args=[item.pk]), {"field": "notes"}).status_code == 404
    assert "RX-" not in client.get(reverse("prescriptions:patient_list", args=[world["patient"].pk])).content.decode()


def test_settings_and_catalog_permissions(client, world, make_member):
    client.force_login(world["doc"])
    assert client.get(reverse("prescriptions:settings")).status_code == 403
    assert client.get(reverse("prescriptions:drugs")).status_code == 200
    client.post(
        reverse("prescriptions:drugs"), {"name": "Augmentin 625", "form": "قرص", "aliases_text": "اوجمنتين 625"}
    )
    assert _drug(world, "Augmentin 625").aliases_ar == ["اوجمنتين 625"]
    client.force_login(make_member(world["org"], "owner"))
    resp = client.post(reverse("prescriptions:settings"), {
        "page_size": "A5", "print_mode": "preprinted", "margin_top_mm": 50, "margin_bottom_mm": 25,
        "margin_right_mm": 10, "margin_left_mm": 10, "prescription_channel": "whatsapp",
        "send_prescription_after_visit": "on",
    })  # fmt: skip
    assert resp.status_code == 302
    assert PrescriptionSettings.objects.get(organization=world["org"]).margin_top_mm == 50
    assert NotificationSettings.objects.get(organization=world["org"]).send_prescription_after_visit


# --- real PDF engine -------------------------------------------------------------------------


def test_real_pdf_render(world, monkeypatch):
    from apps.documents import pdf as engine

    if not engine._browser_candidates():
        pytest.skip("Chromium not installed (python -m playwright install chromium)")
    monkeypatch.undo()  # use the real renderer
    rx = services.finalize(_rx_with(world, "Augmentin 1g", "Vitamin D3 50000"), by=world["doc"])
    for mode in ("full", "preprinted"):
        data = rx_pdf.render_prescription(rx, mode=mode)
        assert data.startswith(b"%PDF") and data.count(b"/Type /Page\n") <= 1
    assert rx_pdf.render_calibration(world["org"]).startswith(b"%PDF")


def test_enter_in_search_picks_best_catalog_match(client, world):
    client.force_login(world["doc"])
    client.get(reverse("prescriptions:builder", args=[world["visit"].pk]))
    rx = Prescription.objects.get(visit=world["visit"])
    client.post(reverse("prescriptions:item_add", args=[rx.pk]), {"drug_name": "اوجمنتين"})
    client.post(reverse("prescriptions:item_add", args=[rx.pk]), {"drug_name": "Something new"})
    items = list(rx.items.all())
    assert items[0].drug.name == "Augmentin 1g" and items[0].instructions == "قرص كل 12 ساعة بعد الأكل"
    assert items[1].drug is None and items[1].drug_name == "Something new"
