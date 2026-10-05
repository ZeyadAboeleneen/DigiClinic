import io
import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.clinical import services as clinical
from apps.doctors.models import Doctor, VisitType, WorkingPeriod
from apps.patients.models import Gender, Patient
from apps.prescriptions import matching, services
from apps.prescriptions.models import Drug, Prescription, PrescriptionSettings
from apps.scheduling import services as booking
from apps.scheduling.models import AppointmentStatus

pytestmark = pytest.mark.usefixtures("midday")


@pytest.fixture
def org(org_a):
    call_command("import_drugs", org=org_a.slug, stdout=io.StringIO())
    return org_a


def _top(org, text):
    [line] = matching.match_text(org, text)
    return line


# --- matching (13 §13.5) ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("اوجمنتين", "Augmentin 1g"),
        ("بنادول اكسترا", "Panadol Extra"),
        ("فلاجيل", "Flagyl 500"),
        ("كاتافلام", "Cataflam 50"),
        ("augmentin", "Augmentin 1g"),
        ("أوجمنتين", "Augmentin 1g"),  # hamza normalized
    ],
)
def test_spoken_names_match_the_catalog(org, spoken, expected):
    assert _top(org, spoken).candidates[0].drug.name == expected


def test_unknown_word_has_no_suggestion_above_threshold(org):
    assert _top(org, "زفت طين").candidates == []


def test_top_three_and_instructions_from_a_full_sentence(org):
    lines = matching.match_text(
        org, "اوجمنتين واحد جرام قرص كل اتناشر ساعة بعد الأكل لمدة اسبوع، بنادول اكسترا عند اللزوم"
    )
    assert [line.candidates[0].drug.name for line in lines] == ["Augmentin 1g", "Panadol Extra"]
    assert len(lines[0].candidates) <= 3
    assert lines[0].instructions == "قرص كل 12 ساعة بعد الأكل لمدة اسبوع"
    assert lines[1].instructions == "عند اللزوم"


def test_written_numbers_become_digits():
    assert matching.words_to_numbers("تلات مرات كل اتناشر ساعة ونص قرص") == "3 مرات كل 12 ساعة و½ قرص"
    assert matching.words_to_numbers("٣ مرات") == "3 مرات"


def test_bare_number_kept_unless_it_is_the_strength(org):
    assert _top(org, "فلاجيل تلات مرات يوميا").instructions == "3 مرات يوميا"
    assert _top(org, "بروفين اربعمية بعد الأكل").instructions == "بعد الأكل"


def test_lines_split_on_commas_and_spoken_new_line():
    assert matching.split_lines("بنادول، بروفين سطر جديد فلاجيل\nنكسيوم") == ["بنادول", "بروفين", "فلاجيل", "نكسيوم"]


def test_learning_from_the_doctors_choice(org):
    drug = Drug.objects.get(organization=org, name="Zithromax 500")
    nickname = "المضاد ابو تلات حبايات"  # how this doctor refers to it — nothing like the name
    assert _top(org, nickname).candidates == []
    assert matching.learn_alias(drug, nickname)
    assert not matching.learn_alias(drug, nickname)  # learned once
    assert not matching.learn_alias(drug, "zz")  # too short / not Arabic
    assert _top(org, nickname).candidates[0].drug == drug


# --- endpoints & review gate (13 §13.4) ------------------------------------------------------


@pytest.fixture
def desk(org, org_b, make_member):
    doctor = Doctor.objects.create(organization=org, name_ar="سارة", slot_minutes=20)
    for weekday in range(7):
        WorkingPeriod.objects.create(organization=org, doctor=doctor, weekday=weekday,
                                     start_time="00:00", end_time="23:59")  # fmt: skip
    vt = VisitType.objects.create(organization=org, doctor=doctor, name_ar="كشف", duration_minutes=20,
                                  price=Decimal("300"))  # fmt: skip
    patient = Patient.objects.create(organization=org, full_name="منى", phone="+201001234567",
                                     gender=Gender.FEMALE, file_number=1)  # fmt: skip
    doc, reception = make_member(org, "doctor"), make_member(org, "reception")
    appt = booking.book(patient=patient, doctor=doctor, visit_type=vt, by=reception,
                        start_at=timezone.now() + timedelta(hours=1), overbook=True)  # fmt: skip
    booking.transition(appt, AppointmentStatus.ARRIVED, by=reception)
    visit = clinical.start_visit(appt, by=doc)
    rx = services.draft_for_visit(visit)
    return {"rx": rx, "doc": doc, "reception": reception, "other": make_member(org_b, "doctor"), "visit": visit}


def test_dictated_lines_need_review_before_finalizing(client, desk, org):
    client.force_login(desk["doc"])
    rx = desk["rx"]
    html = client.post(reverse("prescriptions:dictate", args=[rx.pk]),
                       {"text": "فلاجيل تلات مرات يوميا\nبنادول اكسترا عند اللزوم"}).content.decode()  # fmt: skip
    assert "Flagyl 500" in html and "Panadol Extra" in html
    assert rx.items.count() == 0  # nothing saved until the doctor picks
    flagyl = Drug.objects.get(organization=org, name="Flagyl 500")
    client.post(reverse("prescriptions:dictate_add", args=[rx.pk]),
                {"drug": flagyl.pk, "spoken": "فلاجيل", "instructions": "3 مرات يوميا"})  # fmt: skip
    item = rx.items.get()
    assert (item.drug, item.instructions, item.needs_review) == (flagyl, "3 مرات يوميا", True)
    html = client.post(reverse("prescriptions:action", args=[rx.pk, "finalize"])).content.decode()
    assert "مراجعة" in html
    assert Prescription.objects.get(pk=rx.pk).status == "draft"
    client.post(reverse("prescriptions:item_action", args=[item.pk, "confirm"]))
    client.post(reverse("prescriptions:action", args=[rx.pk, "finalize"]))
    assert Prescription.objects.get(pk=rx.pk).status == "final"


def test_picking_a_drug_teaches_the_alias(client, desk, org):
    client.force_login(desk["doc"])
    drug = Drug.objects.get(organization=org, name="Telfast 180")
    client.post(reverse("prescriptions:dictate_add", args=[desk["rx"].pk]), {"drug": drug.pk, "spoken": "تيلفاست"})
    drug.refresh_from_db()
    assert "تيلفاست" in drug.aliases_ar


def test_dictation_endpoints_permissions(client, desk):
    rx = desk["rx"]
    for user, status in ((desk["reception"], 403), (desk["other"], 404)):
        client.force_login(user)
        assert client.post(reverse("prescriptions:dictate", args=[rx.pk]), {"text": "بنادول"}).status_code == status
        assert client.post(reverse("prescriptions:dictate_add", args=[rx.pk]), {"spoken": "x"}).status_code == status


def test_mic_script_only_when_voice_is_enabled(client, desk, org):
    client.force_login(desk["doc"])
    url = reverse("clinical:visit", args=[desk["visit"].pk])
    assert "dictation.js" in client.get(url).content.decode()
    PrescriptionSettings.objects.filter(organization=org).update(voice_dictation_enabled=False)
    assert "dictation.js" not in client.get(url).content.decode()


# --- dictation.js in a real browser ---------------------------------------------------------

HARNESS = """<!doctype html><html dir="rtl"><body>
<textarea id="t" data-dictate>أهلا</textarea>
<p id="note" data-dictation-unsupported hidden>unsupported</p>
<script>%(fake)s</script>
<script>%(js)s</script>
</body></html>"""

FAKE_RECOGNITION = """
Object.defineProperty(window, "isSecureContext", { value: true });  // the real app runs on https/localhost
window.webkitSpeechRecognition = class {
  constructor() { window.__rec = this; }
  start() { this.started = true; }
  stop() { this.started = false; this.onend && this.onend(); }
};
window.SpeechRecognition = window.webkitSpeechRecognition;  // newer Chromium also has the unprefixed one
window.__say = (text, isFinal = true) => {
  const result = [{ transcript: text }]; result.isFinal = isFinal;
  window.__rec.onresult({ resultIndex: 0, results: [result] });
};
"""


def _in_browser(fake, scenario):
    """Playwright's sync API runs an event loop; keep it off the test thread (Django's DB-safety check)."""
    from concurrent.futures import ThreadPoolExecutor

    from apps.documents import pdf as engine

    if not engine._browser_candidates():
        pytest.skip("Chromium not installed")
    js = (Path(settings.BASE_DIR) / "static" / "src" / "dictation.js").read_text(encoding="utf-8")

    def run():
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = engine._launch(p)
            try:
                page = browser.new_page()
                page.set_content(HARNESS % {"fake": fake, "js": js})
                return scenario(page)
            finally:
                browser.close()

    with ThreadPoolExecutor(1) as pool:
        return pool.submit(run).result()


def test_js_mic_hidden_when_unsupported():
    def scenario(page):
        return page.locator(".dictation-mic").count(), page.locator("#note").is_visible()

    assert _in_browser("delete window.webkitSpeechRecognition; delete window.SpeechRecognition;", scenario) == (0, True)


def test_js_inserts_final_text_at_cursor_with_commands():
    def scenario(page):
        out = {"mics": page.locator(".dictation-mic").count()}
        page.evaluate("const t = document.getElementById('t'); t.focus(); t.setSelectionRange(4, 4);")
        page.evaluate(
            "window.__inputs = 0; document.getElementById('t').addEventListener('input', () => window.__inputs++)"
        )
        page.locator(".dictation-mic").click()
        page.evaluate("window.__say('كحة', false)")
        out["interim"] = page.locator(".dictation-interim").inner_text()
        page.evaluate("window.__say('كحة من يومين سطر جديد حرارة نقطة')")
        out["value"] = page.locator("#t").input_value()
        out["inputs"] = page.evaluate("window.__inputs")
        page.locator(".dictation-mic").click()  # second click stops
        out["started"] = page.evaluate("window.__rec.started")
        return out

    out = _in_browser(FAKE_RECOGNITION, scenario)
    assert out["mics"] == 1 and out["interim"] == "كحة"
    assert out["value"] == "أهلا كحة من يومين\nحرارة."
    assert out["inputs"] >= 1  # the field's autosave hook fires
    assert out["started"] is False


def test_conversation_keeps_only_medicines_dose_and_duration(client, desk, org):
    client.force_login(desk["doc"])
    rx = desk["rx"]
    talk = ("ازيك يا حاج متقلقش ده دور برد عادي هكتبلك فلاجيل قرص تلات مرات يوميا بعد الأكل لمدة اسبوع "
            "وخلي بالك من الأكل واشرب سوايل كتير وبنادول اكسترا عند اللزوم ولو الحرارة زادت كلمني")  # fmt: skip
    html = client.post(reverse("prescriptions:dictate", args=[rx.pk]), {"text": talk}).content.decode()
    assert "Flagyl 500" in html and "Panadol Extra" in html
    assert "لمدة أسبوع" in html and "عند اللزوم" in html
    shown = re.sub(r'title="[^"]*"|«[^»]*»', "", html)  # drop the quoted speech (shown for reference only)
    assert "سوايل" not in shown  # the chit-chat is not offered as instructions
    flagyl = Drug.objects.get(organization=org, name="Flagyl 500")
    client.post(reverse("prescriptions:dictate_add", args=[rx.pk]),
                {"drug": flagyl.pk, "spoken": "فلاجيل", "instructions": "قرص 3 مرات يوميًا", "duration": "لمدة أسبوع"})  # fmt: skip  # noqa: E501
    assert rx.items.get().duration == "لمدة أسبوع"


def test_pleasantries_only_produce_no_medicines(org):
    from apps.prescriptions import extraction

    assert extraction.extract(org, "عامل ايه النهارده الحمد لله كويس شكرا يا دكتور مع السلامة") == []


@pytest.mark.parametrize(
    ("talk", "drug", "instructions", "duration"),
    [
        ("انتينال ده كويس الانتينال ده تستخدميه مره كل يوم ولماضه اسبوعين وهكتب لك", "ANTINAL 200MG 24 CAPS.",
         "مرة كل يوم", "لمدة اسبوعين"),
        ("براسيتامول الباراسيتامول دي مرتين في اليوم وده لمده ثلاث ايام بس", "PARACETAMOL 500MG 20 TAB.",
         "مرتين في اليوم", "لمدة 3 أيام"),
        ("اللي هو انتينال ده تاخديه مره واحده بس في اليوم قبل ما تيجي", "ANTINAL 200MG 24 CAPS.",
         "مرة واحدة في اليوم", ""),
        ("الباراسيتامول ده مره كل يوم واه مره كل يوم متنسيش", "PARACETAMOL 500MG 20 TAB.", "مرة كل يوم", ""),
        ("هكتبلك انتينال وده مهم جدا ومتاكلش حاجه ساقعه وخلي بالك من نفسك واشرب مايه كتير وارتاح في البيت شويه "
         "مره كل 12 ساعه لمده 5 ايام", "ANTINAL 200MG 24 CAPS.", "مرة كل 12 ساعة", "لمدة 5 أيام"),
    ],
)  # fmt: skip
def test_real_clinic_phrasings(org, talk, drug, instructions, duration):
    from apps.prescriptions import extraction

    for name, alias in (("ANTINAL 200MG 24 CAPS.", "انتينال"), ("PARACETAMOL 500MG 20 TAB.", "باراسيتامول"),
                        ("VALLEY YEAST 30 TAB.", "فالي ييست")):  # fmt: skip
        Drug.objects.create(organization=org, name=name, aliases_ar=[alias])
    [item] = extraction.extract(org, talk)  # "اللي هو" must not match VALLEY YEAST
    top = item.candidates[0].drug.name.upper()
    assert (top.split()[0], item.instructions, item.duration) == (drug.split()[0], instructions, duration)


def test_english_names_said_in_arabic(org):
    from apps.prescriptions import extraction

    Drug.objects.create(organization=org, name="A.ONE SOAP 100 GM", aliases_ar=["ا.اوني سواب"])
    Drug.objects.create(organization=org, name="A.ONE CREAM 50 GM")
    [item] = extraction.extract(org, "استخدمي صابونه اي ون مرتين في اليوم")
    assert (item.candidates[0].drug.name, item.instructions) == ("A.ONE SOAP 100 GM", "مرتين في اليوم")
    assert extraction.extract(org, "دي او ال كده تمام") == []
    Drug.objects.create(organization=org, name="A1 CREAM 50 GM")
    [item] = extraction.extract(org, "وكريم اي 1 بالليل")  # the browser often writes "ون" as the digit
    assert item.candidates[0].drug.name == "A1 CREAM 50 GM"


def test_misheard_letters_and_plain_variant_first(org):
    from apps.prescriptions import extraction

    Drug.objects.create(organization=org, name="ALKOR PLUS 10/40MG 14 TAB", aliases_ar=["الكور بلوس 10/"])
    Drug.objects.create(organization=org, name="ALKOR 10 MG 14 F.C. TABS.", aliases_ar=["الكور"])
    [item] = extraction.extract(org, "وهكتب لك الكول 10 مليجرام ده تاخديه مرتين")  # ر heard as ل
    assert (item.candidates[0].drug.name, item.instructions) == ("ALKOR 10 MG 14 F.C. TABS.", "مرتين")


@pytest.fixture
def mini_catalog(org):
    for name, alias in (
        ("PANADOL 500 MG 24 TABS.", "بنادول"), ("CATAFLAM 50 MG 20 TABS.", "كتافلام"),
        ("CONCOR 5 MG 30 TABS.", "كونكور"), ("CONCOR 10 MG 30 TABS.", "كونكور"),
        ("AUGMENTIN 1 GM 14 TABS.", "اوجمنتين"), ("AUGMENTIN 457 MG/5 ML SUSP.", "اوجمنتين"),
        ("AMOXICILLIN 500MG 12 CAPS.", ""), ("BRUFEN 400 MG 30 TABS.", "بروفين"),
        ("GLUCOPHAGE 500 MG 50 TABS.", "جلوكوفاج"),
    ):  # fmt: skip
        Drug.objects.create(organization=org, name=name, aliases_ar=[alias] if alias else [])


@pytest.mark.parametrize(
    ("talk", "expected"),
    [
        ("بلاش البنادول خالص وخدي كتافلام 50 قرص كل 8 ساعات", [("CATAFLAM", "قرص كل 8 ساعات", "")]),
        ("كونكور 5 لا قصدي كونكور 10 مرة الصبح", [("CONCOR 10", "مرة الصبح", "")]),
        ("كونكور5 مرة الصبح", [("CONCOR 5", "مرة الصبح", "")]),
        ("اوج منتين قرص كل 12 ساعة", [("AUGMENTIN", "قرص كل 12 ساعة", "")]),
        ("خدي بالبنادول عند اللزوم", [("PANADOL", "عند اللزوم", "")]),
        ("اموكسيسيلين كبسولة كل 8 ساعات لمدة اسبوع", [("AMOXICILLIN", "كبسولة كل 8 ساعات", "لمدة أسبوع")]),
        ("اوجمنتين شراب 5 مل كل 12 ساعه", [("AUGMENTIN 457", "5 مل كل 12 ساعة", "")]),
        ("جلوكوفاج 500 قرص بعد الاكل بنص ساعه باستمرار", [("GLUCOPHAGE", "قرص بعد الأكل بنص ساعة", "باستمرار")]),
        ("بروفين ميتين وخمسين معلقة كل 8 ساعات", [("BRUFEN", "معلقة كل 8 ساعات", "")]),
        ("انا صحيت النهارده تعبان وبطني بتوجعني من امبارح ومش قادر انام", []),
        ("ضغطك كويس والسكر كمان تمام الحمد لله نكمل على نفس العلاج", []),
    ],
)  # fmt: skip
def test_future_phrasings(org, mini_catalog, talk, expected):
    from apps.prescriptions import extraction

    got = [([f"{c.drug.name} {c.drug.generic_name}".upper() for c in e.candidates], e.instructions, e.duration)
           for e in extraction.extract(org, talk)]  # fmt: skip
    assert len(got) == len(expected), got
    for (names, ins, dur), (prefix, e_ins, e_dur) in zip(got, expected, strict=True):
        # a brand whose generic is the spoken name (Flumox ← "اموكسيسيلين") is a right answer too
        assert (names[0].startswith(prefix) or prefix in names[0]) and (ins, dur) == (e_ins, e_dur), got


def test_filler_before_a_drug_does_not_borrow_its_dose(org):
    from apps.prescriptions import extraction

    Drug.objects.create(organization=org, name="ACT LIFE 20 CAPSULES", aliases_ar=["اكت ليفي"])
    Drug.objects.create(organization=org, name="ANTINAL 200MG 24 CAPS.", aliases_ar=["انتينال"])
    items = extraction.extract(org, "ممكن حضرتك وهكتب لك انتينال تاخديه مره كل يوم لمده 5 ايام")
    assert [e.candidates[0].drug.name.upper()[:7] for e in items] == ["ANTINAL"]  # no ACT LIFE
