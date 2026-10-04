"""Fill today's reception screen with a realistic day for manual testing (demo patients only).

    python manage.py simulate_day            # 10 bookings today: some done & paid, some waiting, one no-show
    python manage.py simulate_day --remove   # delete today's simulated bookings again

Goes through the real services (book / transition / mark_no_show / record_payment). No message is ever sent:
every outbox row created for these demo appointments is cancelled at the end.
"""

from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.billing.models import Payment
from apps.billing.services import record_payment
from apps.clinical.models import Visit, Vitals
from apps.core.timeutils import CAIRO
from apps.doctors.models import BookingMode, VisitType
from apps.doctors.services import get_doctor
from apps.notifications.models import MessageStatus, ScheduledMessage
from apps.organizations.models import Organization
from apps.patients.management.commands.seed_demo import MARKER
from apps.patients.models import Patient
from apps.scheduling import services as booking
from apps.scheduling.availability import periods_for
from apps.scheduling.models import Appointment, AppointmentStatus

NOTE = "simulate_day"


class Command(BaseCommand):
    help = "Book 10 demo patients for today and walk some of them through the day (for trying the reception screen)."

    def add_arguments(self, parser):
        parser.add_argument("--org", default="demo-clinic")
        parser.add_argument("--remove", action="store_true")

    def handle(self, *args, **o):
        org = Organization.objects.filter(slug=o["org"]).first()
        if org is None:
            raise CommandError(f"No organization '{o['org']}'")
        today = timezone.localtime(timezone.now(), CAIRO).date()
        if o["remove"]:
            self._remove(org)
            return
        doctor = get_doctor(org)
        periods = periods_for(doctor, today)
        if not periods:
            raise CommandError("The doctor doesn't work today — add a working period or an 'extra' exception first.")
        visit_type = VisitType.objects.filter(organization=org, doctor=doctor, is_active=True).order_by("id").first()
        if visit_type is None:
            raise CommandError("No visit types — add one in Settings first.")
        if Patient.objects.filter(organization=org, notes=MARKER).count() < 10:
            call_command("seed_demo", org=org.slug)
        patients = list(Patient.objects.filter(organization=org, notes=MARKER).order_by("id")[:10])
        with transaction.atomic():
            appts = self._book(doctor, visit_type, periods, patients)
            self._walk_through(appts)
        simulated = Appointment.objects.filter(notes=NOTE)
        rebooked = Appointment.objects.filter(auto_rebooked_from__in=simulated)
        ScheduledMessage.objects.filter(appointment__in=list(simulated) + list(rebooked)).exclude(
            status=MessageStatus.SENT
        ).update(status=MessageStatus.CANCELLED, status_reason="محاكاة — مش هتتبعت")
        self.stdout.write(self.style.SUCCESS(f"Simulated {len(appts)} bookings for {today}. Open /reception/."))

    def _book(self, doctor, visit_type, periods, patients):
        appts = []
        step = timedelta(minutes=visit_type.duration_minutes or doctor.slot_minutes)
        start = periods[0].start_at
        for patient in patients:
            if doctor.booking_mode == BookingMode.SLOTS:
                appt = booking.book(patient=patient, doctor=doctor, visit_type=visit_type, by=None, start_at=start,
                                    overbook=True, notify=False)  # fmt: skip
                start += step
            else:
                appt = booking.book(patient=patient, doctor=doctor, visit_type=visit_type, by=None,
                                    day=periods[0].start_at.date(), period_key=periods[0].period_id,
                                    overbook=True, notify=False)  # fmt: skip
            Appointment.objects.filter(pk=appt.pk).update(notes=NOTE)
            appts.append(appt)
        return appts

    def _walk_through(self, appts):
        s = AppointmentStatus
        # 0-2 done and paid, 3 with the doctor, 4-5 waiting (with vitals), 6 no-show, 7-9 still booked.
        for i, appt in enumerate(appts):
            if i <= 5:
                booking.transition(appt, s.ARRIVED, by=None)
                if i in (4, 5):
                    visit = Visit.for_appointment(appt)
                    Vitals.objects.create(organization=appt.organization, visit=visit, weight_kg=70 + i,
                                          bp_systolic=120, bp_diastolic=80, pulse=78)  # fmt: skip
            if i <= 3:
                booking.transition(appt, s.IN_CONSULTATION, by=None)
            if i <= 2:
                booking.transition(appt, s.COMPLETED, by=None)
                if appt.price - appt.discount > 0:
                    record_payment(appt, amount=appt.price - appt.discount, by=None)
            if i == 6:
                booking.mark_no_show(appt, by=None)

    def _remove(self, org):
        appts = Appointment.objects.filter(organization=org, notes=NOTE)
        rebooked = Appointment.objects.filter(auto_rebooked_from__in=appts)
        everything = list(appts) + list(rebooked)
        ScheduledMessage.objects.filter(appointment__in=everything).delete()
        Payment.objects.filter(appointment__in=everything).delete()
        Vitals.objects.filter(visit__appointment__in=everything).delete()
        Visit.objects.filter(appointment__in=everything).delete()
        Appointment.objects.filter(pk__in=[a.pk for a in rebooked]).delete()
        count, _ = appts.delete()
        self.stdout.write(self.style.SUCCESS(f"Removed {count} simulated row(s)."))
