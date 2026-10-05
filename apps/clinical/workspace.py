"""Doctor workspace (the visit page as one cockpit): read-only summaries built from data that already exists —
the previous visit, what changed since then, quick phrases learned from the doctor's own notes — plus "reuse from a
previous visit". Nothing here infers clinical facts: an item is shown only when both values really exist."""

import re
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.timeutils import CAIRO

from . import services
from .models import Attachment, Visit, VisitStatus, Vitals

# Fields the doctor can copy from a previous visit ("↻ استخدم الزيارة اللي فاتت").
REUSABLE = ("chief_complaint", "history", "examination", "diagnosis", "plan", "notes", "followup_after_days")
CHECKED_BY_DEFAULT = {"chief_complaint", "examination", "diagnosis"}  # + the prescription; notes/follow-up opt-in

# Shown until the doctor's own phrases take over (a shortcut for typing, not a clinical suggestion).
DEFAULT_PHRASES = {
    "chief_complaint": ["منذ يومين", "منذ 3 أيام", "منذ أسبوع", "ألم شديد", "ارتفاع حرارة", "كحة", "ضيق تنفس"],
    "examination": ["Normal", "Chest clear", "Throat congested", "Abdomen soft, lax", "Heart sounds normal"],
    "notes": ["متابعة", "تحاليل في الزيارة الجاية", "التزام بالعلاج"],
}
PHRASE_FIELDS = tuple(DEFAULT_PHRASES)
_SEGMENTS = re.compile(r"[،,\n.؛;]+")


def previous_visit(visit):
    """The patient's last finished visit before this one (any doctor in the clinic), or None."""
    return (
        Visit.objects.filter(patient=visit.patient, status=VisitStatus.FINISHED, created_at__lt=visit.created_at)
        .exclude(pk=visit.pk)
        .select_related("vitals")
        .order_by("-finished_at", "-created_at")
        .first()
    )


def vitals_of(visit):
    return Vitals.objects.filter(visit=visit).first() if visit else None


def final_rx(visit):
    """The visit's latest final prescription (with items), or None."""
    from apps.prescriptions.models import Prescription, RxStatus

    if visit is None:
        return None
    return (
        Prescription.objects.filter(visit=visit, status=RxStatus.FINAL)
        .prefetch_related("items")
        .order_by("-issued_at")
        .first()
    )


def last_rx(patient, exclude_visit=None):
    from apps.prescriptions.models import Prescription, RxStatus

    qs = Prescription.objects.filter(patient=patient, status=RxStatus.FINAL)
    if exclude_visit is not None:
        qs = qs.exclude(visit=exclude_visit)
    return qs.prefetch_related("items").order_by("-issued_at").first()


def followup_due(prev):
    """Date the previous visit asked the patient to come back, or None."""
    if prev is None or not prev.followup_after_days or not prev.finished_at:
        return None
    return timezone.localtime(prev.finished_at, CAIRO).date() + timedelta(days=prev.followup_after_days)


@dataclass
class Change:
    label: str
    before: str = ""
    after: str = ""
    note: str = ""  # used instead of before → after ("ملف جديد: …")
    tone: str = ""  # "up" / "down" / "" (direction only, never a judgement)


def _num(v):
    return f"{v:g}" if isinstance(v, float) else str(v)


def changes_since(visit, prev):
    """Meaningful differences between this visit and the previous one. Only real, recorded values."""
    if prev is None:
        return []
    out = []
    now, before = vitals_of(visit), vitals_of(prev)
    if now and before:
        if now.weight_kg and before.weight_kg and now.weight_kg != before.weight_kg:
            out.append(Change(_("الوزن"), f"{_num(float(before.weight_kg))}", f"{_num(float(now.weight_kg))} kg",
                              tone="up" if now.weight_kg > before.weight_kg else "down"))  # fmt: skip
        if (
            now.bp_systolic
            and before.bp_systolic
            and (now.bp_systolic, now.bp_diastolic)
            != (
                before.bp_systolic,
                before.bp_diastolic,
            )
        ):
            out.append(Change(_("الضغط"), f"{before.bp_systolic}/{before.bp_diastolic or '–'}",
                              f"{now.bp_systolic}/{now.bp_diastolic or '–'}",
                              tone="up" if now.bp_systolic > before.bp_systolic else "down"))  # fmt: skip
        if now.blood_glucose and before.blood_glucose and now.blood_glucose != before.blood_glucose:
            out.append(Change(_("السكر"), str(before.blood_glucose), str(now.blood_glucose),
                              tone="up" if now.blood_glucose > before.blood_glucose else "down"))  # fmt: skip
    since = prev.finished_at or prev.created_at
    for att in Attachment.objects.filter(patient=visit.patient, is_archived=False, created_at__gt=since)[:3]:
        out.append(Change(_("مرفق جديد"), note=str(att)))
    due = followup_due(prev)
    if due:
        today = timezone.localtime(visit.created_at, CAIRO).date()
        if today > due + timedelta(days=3):
            out.append(Change(_("الإعادة"), note=_("كانت مطلوبة %(d)s — اتأخر %(n)s يوم")
                              % {"d": f"{due.day}/{due.month}", "n": (today - due).days}))  # fmt: skip
    return out


def snapshot(visit):
    """Everything the compact patient snapshot needs, in one dict (a handful of queries)."""
    prev = previous_visit(visit)
    rx = final_rx(prev) or last_rx(visit.patient, exclude_visit=visit)
    latest = vitals_of(visit)
    if latest is None or latest.is_empty:
        latest = (
            Vitals.objects.filter(visit__patient=visit.patient).exclude(visit=visit).order_by("-recorded_at").first()
        )
    return {
        "prev": prev,
        "prev_rx": rx,
        "latest_vitals": latest,
        "latest_is_today": latest is not None and latest.visit_id == visit.pk,
        "changes": changes_since(visit, prev),
        "followup_due": followup_due(prev),
    }


def quick_phrases(doctor, field, limit=8):
    """The doctor's own most used short phrases in a field (≥ 2 uses), topped up with neutral typing shortcuts."""
    counts = Counter()
    for text in (
        Visit.objects.filter(organization=doctor.organization, doctor=doctor)
        .exclude(**{field: ""})
        .order_by("-created_at")
        .values_list(field, flat=True)[:300]
    ):
        for seg in _SEGMENTS.split(text):
            seg = " ".join(seg.split())
            if 2 <= len(seg) <= 40:
                counts[seg] += 1
    own = [p for p, n in counts.most_common(limit) if n >= 2]
    return own + [p for p in DEFAULT_PHRASES.get(field, []) if p not in own][: max(0, limit - len(own))]


@transaction.atomic
def reuse(visit, source, fields, *, by):
    """Copy the chosen parts of `source` (a previous visit of the same patient) into `visit`. An empty field is filled;
    a field the doctor already wrote in keeps its text and gets the old one appended below it. "prescription" adds
    the source visit's final prescription to this visit's draft (never replaces lines already there)."""
    from apps.prescriptions import services as rx_services

    if source.patient_id != visit.patient_id or source.pk == visit.pk:
        raise services.VisitError(_("الزيارة دي مش لنفس المريض."))
    done = []
    for name in fields:
        if name not in REUSABLE:
            continue
        old = getattr(source, name)
        if old in ("", None):
            continue
        current = getattr(visit, name)
        if name == "followup_after_days":
            value = str(old)
        elif not (current or "").strip():
            value = old
        elif old.strip() in current:
            continue
        else:
            value = f"{current.rstrip()}\n{old}"
        services.save_field(visit, name, value, by=by)
        done.append(name)
    if "prescription" in fields:
        rx = final_rx(source)
        if rx is not None:
            draft = rx_services.current_for_visit(visit)
            if draft.is_editable:
                rx_services.repeat_from(draft, rx)
                done.append("prescription")
    return done
