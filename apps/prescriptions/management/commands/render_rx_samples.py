"""python manage.py render_rx_samples [--org demo-clinic]

Writes tmp/rx-sample-full.pdf, tmp/rx-sample-preprinted.pdf and tmp/rx-calibration.pdf for eyeballing the layout
(05 §5.3 mixed Arabic/English test). Uses a throw-away prescription inside a transaction that is rolled back.
"""

import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.clinical.models import Visit
from apps.doctors.models import VisitType
from apps.doctors.services import get_doctor
from apps.organizations.models import Organization
from apps.patients.models import Gender, Patient
from apps.prescriptions import pdf, services
from apps.prescriptions.models import Drug, Prescription
from apps.scheduling import services as booking

SAMPLE = [
    ("Augmentin 1g", "قرص", "قرص كل 12 ساعة بعد الأكل", "لمدة 7 أيام"),
    ("Vitamin D3 50,000 IU", "كبسولة", "كبسولة مرة أسبوعيًا", "لمدة 8 أسابيع"),
    ("Cefotax 1 gm vial", "حقن", "حقنة عضل كل 12 ساعة (بعد اختبار الحساسية)", "لمدة 5 أيام"),
    ("Panadol Extra", "قرص", "½ قرص عند اللزوم، بحد أقصى 4 يوميًا", ""),
    ("Otrivin 0.1%", "نقط أنف", "نقطتين في كل فتحة 3 مرات يوميًا", "لمدة 3 أيام بس"),
]


class _Rollback(Exception):
    pass


class Command(BaseCommand):
    help = "Render sample prescription PDFs (both print modes) and the calibration page into tmp/."

    def add_arguments(self, parser):
        parser.add_argument("--org", default="demo-clinic")

    def handle(self, *args, **o):
        org = Organization.objects.get(slug=o["org"])
        out = Path(settings.BASE_DIR) / "tmp"
        out.mkdir(exist_ok=True)
        try:
            with transaction.atomic():
                rx = self._sample(org)
                for mode in ("full", "preprinted"):
                    started = time.monotonic()
                    (out / f"rx-sample-{mode}.pdf").write_bytes(pdf.render_prescription(rx, mode=mode))
                    self.stdout.write(f"rx-sample-{mode}.pdf ({time.monotonic() - started:.2f}s)")
                (out / "rx-calibration.pdf").write_bytes(pdf.render_calibration(org))
                raise _Rollback
        except _Rollback:
            pass
        self.stdout.write(self.style.SUCCESS(f"Written to {out}"))

    def _sample(self, org):
        doctor = get_doctor(org)
        vt = VisitType.objects.filter(organization=org, doctor=doctor).first()
        patient = Patient.objects.create(organization=org, full_name="محمد أحمد عبد الله", phone="+201001234567",
                                         gender=Gender.MALE, file_number=999_999)  # fmt: skip
        from django.utils import timezone

        appt = booking.book(patient=patient, doctor=doctor, visit_type=vt, by=None, start_at=timezone.now(),
                            day=timezone.localdate(), overbook=True, notify=False)  # fmt: skip
        visit = Visit.for_appointment(appt)
        rx = services.draft_for_visit(visit)
        for name, form, instructions, duration in SAMPLE:
            drug = Drug.objects.filter(organization=org, name=name).first()
            services.add_item(rx, drug=drug, drug_name=name, form=form, instructions=instructions,
                              duration=duration)  # fmt: skip
        services.update_fields(rx, advice="راحة وسوائل كتير، وقياس الحرارة كل 6 ساعات (لو فوق 38.5 كلمنا).")
        return services.finalize(Prescription.objects.get(pk=rx.pk), by=None)
