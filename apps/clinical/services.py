"""Doctor desk logic (03 §3.5). Views stay thin; appointment state changes still go through scheduling.services."""

import io
import re
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from PIL import Image, UnidentifiedImageError

from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.core.timeutils import CAIRO
from apps.patients import custom_fields
from apps.patients.models import FieldScope
from apps.scheduling import services as booking
from apps.scheduling.models import Appointment, AppointmentStatus

from .models import Attachment, Visit, VisitStatus

TEXT_FIELDS = ("chief_complaint", "history", "examination", "diagnosis", "plan", "notes")
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
IMAGE_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png"}


class VisitError(Exception):
    pass


# --- queue -----------------------------------------------------------------------------------


def today_queue(doctor, day=None):
    """(current, waiting, not_arrived_count) for the doctor's sidebar."""
    day = day or timezone.localtime(timezone.now(), CAIRO).date()
    appts = list(
        Appointment.objects.filter(organization=doctor.organization, doctor=doctor, date=day)
        .filter(
            status__in=[
                AppointmentStatus.ARRIVED,
                AppointmentStatus.IN_CONSULTATION,
                AppointmentStatus.BOOKED,
                AppointmentStatus.CONFIRMED,
            ]
        )
        .select_related("patient", "visit_type")
        .order_by("queue_number", "start_at", "arrived_at")
    )
    current = [a for a in appts if a.status == AppointmentStatus.IN_CONSULTATION]
    waiting = [a for a in appts if a.status == AppointmentStatus.ARRIVED]
    not_arrived = sum(1 for a in appts if a.status in (AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED))
    return current, waiting, not_arrived


@transaction.atomic
def start_visit(appt, *, by):
    """Call an arrived patient in (or reopen the visit of one already inside) and return the open Visit."""
    appt = Appointment.objects.get(pk=appt.pk)  # callers may hold a stale instance
    if appt.status == AppointmentStatus.ARRIVED:
        appt = booking.transition(appt, AppointmentStatus.IN_CONSULTATION, by=by)
    elif appt.status not in (AppointmentStatus.IN_CONSULTATION, AppointmentStatus.COMPLETED):
        raise VisitError(_("المريض ده لسه موصلش."))
    visit = Visit.for_appointment(appt)
    if visit.started_at is None:
        visit.started_at = timezone.now()
        visit.save(update_fields=["started_at", "updated_at"])
    return visit


def call_next(doctor, *, by):
    _current, waiting, _n = today_queue(doctor)
    return start_visit(waiting[0], by=by) if waiting else None


# --- editing ---------------------------------------------------------------------------------


def split_diagnoses(text):
    return [t.strip() for t in re.split(r"[،,\n؛;]+", text or "") if t.strip()][:20]


def save_field(visit, name, value, *, by=None):
    """Autosave of one field. Returns the stored value. Finished visits stay editable (doctor only — the view
    checks `clinical.edit`) and simple-history keeps every previous version."""
    if name in TEXT_FIELDS:
        value = (value or "")[:20000]
        setattr(visit, name, value)
        fields = [name]
        if name == "diagnosis":
            visit.diagnosis_tags = split_diagnoses(value)
            fields.append("diagnosis_tags")
    elif name == "followup_after_days":
        value = (value or "").strip()
        if value and (not value.isdigit() or not 0 < int(value) <= 730):
            raise ValidationError(_("اكتب عدد أيام بين 1 و730."))
        visit.followup_after_days = int(value) if value else None
        fields = [name]
    elif name.startswith(custom_fields.PREFIX):
        key = name[len(custom_fields.PREFIX) :]
        d = next((d for d in custom_fields.definitions(visit.organization, FieldScope.VISIT) if d.key == key), None)
        if d is None:
            raise ValidationError(_("حقل مش معروف."))
        visit.custom_fields = {**visit.custom_fields, key: custom_fields.clean_value(d, value)}
        fields = ["custom_fields"]
    else:
        raise ValidationError(_("حقل مش معروف."))
    if by is not None:
        visit._history_user = by
    visit.save(update_fields=[*fields, "updated_at"])
    return value


@transaction.atomic
def finish_visit(visit, *, by):
    """Visit finished → appointment completed → (if the clinic enabled it) final prescriptions sent to the patient.
    A draft prescription with lines blocks finishing: the doctor must finalize it (or clear it) first (03 §3.5)."""
    from apps.notifications import services as notifications
    from apps.notifications.models import NotificationSettings
    from apps.prescriptions import services as rx_services
    from apps.prescriptions.models import RxStatus

    visit = Visit.objects.select_for_update().get(pk=visit.pk)
    if visit.status == VisitStatus.FINISHED:
        return visit
    if rx_services.open_drafts_with_items(visit).exists():
        raise VisitError(_("فيه روشتة لسه مسودة — اعتمدها الأول (أو امسح سطورها)."))
    visit.status = VisitStatus.FINISHED
    visit.finished_at = timezone.now()
    visit._history_user = by
    visit.save(update_fields=["status", "finished_at", "updated_at"])
    appt = visit.appointment
    if appt.status == AppointmentStatus.ARRIVED:
        appt = booking.transition(appt, AppointmentStatus.IN_CONSULTATION, by=by)
    if appt.status == AppointmentStatus.IN_CONSULTATION:
        booking.transition(appt, AppointmentStatus.COMPLETED, by=by)
    if NotificationSettings.for_org(visit.organization).send_prescription_after_visit:
        for rx in visit.prescriptions.filter(status=RxStatus.FINAL, send_to_patient=True):
            transaction.on_commit(lambda rx=rx: notifications.send_prescription(rx, by=by))
    return visit


def diagnosis_suggestions(doctor, limit=200):
    seen, out = set(), []
    tags_lists = (
        Visit.objects.filter(organization=doctor.organization, doctor=doctor)
        .exclude(diagnosis_tags=[])
        .order_by("-created_at")
        .values_list("diagnosis_tags", flat=True)[:500]
    )
    for tags in tags_lists:
        for t in tags:
            if t not in seen:
                seen.add(t)
                out.append(t)
                if len(out) >= limit:
                    return out
    return out


# --- follow-ups (reception task) -------------------------------------------------------------


def followups_due(org):
    """Finished visits that asked for a follow-up the patient hasn't booked yet: [(visit, suggested_date)]."""
    visits = (
        Visit.objects.filter(organization=org, status=VisitStatus.FINISHED, followup_handled=False)
        .exclude(followup_after_days=None)
        .select_related("patient")
        .order_by("finished_at")
    )
    due = []
    for v in visits:
        booked = Appointment.objects.filter(
            organization=org,
            patient=v.patient,
            created_at__gte=v.finished_at,
            status__in=[AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED, AppointmentStatus.ARRIVED,
                        AppointmentStatus.IN_CONSULTATION, AppointmentStatus.COMPLETED],
        ).exists()  # fmt: skip
        if booked:
            Visit.objects.filter(pk=v.pk).update(followup_handled=True)
            continue
        suggested = timezone.localtime(v.finished_at, CAIRO).date() + timedelta(days=v.followup_after_days)
        due.append((v, suggested))
    return due


# --- attachments -----------------------------------------------------------------------------


def sniff_upload(f) -> str:
    """Content-based type check (not the extension): returns the MIME type or raises ValidationError."""
    if f.size > MAX_UPLOAD_BYTES:
        raise ValidationError(_("الملف أكبر من 15 ميجا."))
    f.seek(0)
    head = f.read(5)
    f.seek(0)
    if head == b"%PDF-":
        return "application/pdf"
    try:
        with Image.open(f) as img:
            fmt = img.format
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as e:
        raise ValidationError(_("مسموح بس PDF أو صور JPG/PNG.")) from e
    finally:
        f.seek(0)
    if fmt not in IMAGE_TYPES:
        raise ValidationError(_("مسموح بس PDF أو صور JPG/PNG."))
    return IMAGE_TYPES[fmt]


MAX_IMAGE_SIDE = 2000


def shrink_image(f, content_type):
    """07 §7.2: phone photos of scans are 3–8 MB; keep them readable but small (longest side 2000 px, JPEG 85).
    Returns a new in-memory file, or the original when it's already small enough / not an image."""
    from django.core.files.uploadedfile import InMemoryUploadedFile
    from PIL import ImageOps

    if not content_type.startswith("image/"):
        return f
    f.seek(0)
    with Image.open(f) as img:
        img = ImageOps.exif_transpose(img)  # phone photos carry their rotation in EXIF
        if max(img.size) <= MAX_IMAGE_SIDE and content_type == "image/png":
            f.seek(0)
            return f
        img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=85, optimize=True)
    if buf.tell() >= f.size and max(img.size) < MAX_IMAGE_SIDE:
        f.seek(0)
        return f
    buf.seek(0)
    return InMemoryUploadedFile(buf, "file", "upload.jpg", "image/jpeg", buf.getbuffer().nbytes, None)


def add_attachment(*, patient, f, kind, title="", taken_on=None, visit=None, by=None):
    content_type = sniff_upload(f)
    f = shrink_image(f, content_type)
    if getattr(f, "content_type", "") == "image/jpeg":
        content_type = "image/jpeg"
    ext = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}[content_type]
    f.name = f"upload{ext}"
    return Attachment.objects.create(
        organization=patient.organization,
        patient=patient,
        visit=visit,
        file=f,
        content_type=content_type,
        size=f.size,
        kind=kind,
        title=title,
        taken_on=taken_on or timezone.localtime(timezone.now(), CAIRO).date(),
        uploaded_by=by,
    )


# --- audit -----------------------------------------------------------------------------------


def log_clinical_view(request, patient):
    """04 §4.4: one `patient.clinical_view` event per user × patient × hour."""
    since = timezone.now() - timedelta(hours=1)
    already = AuditEvent.objects.filter(
        organization=request.organization,
        actor=request.user,
        action="patient.clinical_view",
        target_type="patient",
        target_id=patient.pk,
        created_at__gte=since,
    ).exists()
    if not already:
        audit.log("patient.clinical_view", request=request, target=patient, summary=patient.full_name)
