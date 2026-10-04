"""Prescription builder logic (03 §3.6, 05). Views stay thin. Final prescriptions are immutable."""

import logging
from datetime import timedelta

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Case, F, IntegerField, Max, Q, Value, When
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.arabic import normalize_arabic
from apps.core.timeutils import CAIRO

from . import safety
from .models import (
    Drug,
    Prescription,
    PrescriptionItem,
    PrescriptionSequence,
    PrescriptionTemplate,
    PrescriptionTemplateItem,
    RxStatus,
)

logger = logging.getLogger(__name__)

ITEM_FIELDS = ("drug_name", "form", "instructions", "duration", "notes")


class PrescriptionError(Exception):
    pass


# --- drafts & items --------------------------------------------------------------------------


def draft_for_visit(visit):
    """The visit's open draft (created on first use)."""
    rx = (
        Prescription.objects.filter(visit=visit, status=RxStatus.DRAFT, revision_of__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if rx is None:
        rx = Prescription.objects.create(
            organization=visit.organization,
            visit=visit,
            patient=visit.patient,
            doctor=visit.doctor,
            next_visit_date=_suggested_next_visit(visit),
        )
    return rx


def _suggested_next_visit(visit):
    if not visit.followup_after_days:
        return None
    return timezone.localtime(timezone.now(), CAIRO).date() + timedelta(days=visit.followup_after_days)


def _require_draft(rx):
    if not rx.is_editable:
        raise PrescriptionError(_("الروشتة دي معتمدة — اعمل نسخة معدلة."))


def search_drugs(org, q, limit=8, *, category=None):
    """Catalog search. With a category: only that category's drugs whose trade name, generic name or an Arabic
    alias *starts with* `q` (an empty `q` lists the category). Without: anywhere in the name, prefix matches first."""
    q = (q or "").strip()
    qs = Drug.objects.filter(organization=org, is_active=True)
    norm = normalize_arabic(q)
    starts = Q(name__istartswith=q) | Q(generic_name__istartswith=q) | Q(aliases_ar__icontains=f'"{norm}')
    if category is not None:
        qs = qs.filter(categories=category)
        if q:
            qs = qs.filter(starts)
        return list(qs.distinct().order_by("-usage_count", "name")[:limit])
    if not q:
        return []
    qs = qs.filter(Q(name__icontains=q) | Q(generic_name__icontains=q) | Q(aliases_ar__icontains=norm))
    qs = qs.annotate(prefix=Case(When(starts, then=Value(0)), default=Value(1), output_field=IntegerField()))
    return list(qs.order_by("prefix", "-usage_count", "name")[:limit])


def _next_order(rx):
    return (rx.items.aggregate(m=Max("order"))["m"] or 0) + 1


def add_item(rx, *, drug=None, drug_name="", instructions=None, duration=None, form=None, needs_review=False):
    _require_draft(rx)
    name = (drug.name if drug else drug_name).strip()
    if not name:
        raise PrescriptionError(_("اكتب اسم الدوا."))
    return PrescriptionItem.objects.create(
        organization=rx.organization,
        prescription=rx,
        drug=drug,
        drug_name=name[:150],
        form=form if form is not None else (drug.form if drug else ""),
        instructions=instructions if instructions is not None else (drug.default_instructions if drug else ""),
        duration=duration if duration is not None else (drug.default_duration if drug else ""),
        order=_next_order(rx),
        needs_review=needs_review,
    )


def update_item(item, field, value):
    _require_draft(item.prescription)
    if field not in ITEM_FIELDS:
        raise PrescriptionError(_("حقل مش معروف."))
    max_len = PrescriptionItem._meta.get_field(field).max_length
    setattr(item, field, (value or "").strip()[:max_len])
    if field == "drug_name" and not item.drug_name:
        raise PrescriptionError(_("اكتب اسم الدوا."))
    item.save(update_fields=[field, "updated_at"])
    return item


def confirm_item(item):
    """Phase 8: a dictated line becomes final only once the doctor confirms it."""
    _require_draft(item.prescription)
    item.needs_review = False
    item.save(update_fields=["needs_review", "updated_at"])


def remove_item(item):
    _require_draft(item.prescription)
    item.delete()


def move_item(item, direction):
    _require_draft(item.prescription)
    items = list(item.prescription.items.order_by("order", "id"))
    i = items.index(item)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(items):
        items[i], items[j] = items[j], items[i]
        for n, it in enumerate(items, start=1):
            if it.order != n:
                PrescriptionItem.objects.filter(pk=it.pk).update(order=n)


def update_fields(rx, *, advice=None, next_visit_date=None, send_to_patient=None):
    _require_draft(rx)
    fields = []
    if advice is not None:
        rx.advice = advice[:4000]
        fields.append("advice")
    if next_visit_date is not None:
        rx.next_visit_date = next_visit_date or None
        fields.append("next_visit_date")
    if send_to_patient is not None:
        rx.send_to_patient = send_to_patient
        fields.append("send_to_patient")
    if fields:
        rx.save(update_fields=[*fields, "updated_at"])


# --- templates & repeat ----------------------------------------------------------------------


def apply_template(rx, tpl):
    """Adds the template's lines to the current prescription (never replaces what's there)."""
    _require_draft(rx)
    for t in tpl.items.all():
        add_item(rx, drug=t.drug, drug_name=t.drug_name, instructions=t.instructions, duration=t.duration,
                 form=t.form)  # fmt: skip
    if tpl.advice and not rx.advice:
        update_fields(rx, advice=tpl.advice)
    PrescriptionTemplate.objects.filter(pk=tpl.pk).update(usage_count=F("usage_count") + 1)


def repeat_last(rx):
    """Copy the patient's last final prescription into this draft."""
    _require_draft(rx)
    last = (
        Prescription.objects.filter(patient=rx.patient, status=RxStatus.FINAL)
        .exclude(pk=rx.pk)
        .order_by("-issued_at")
        .first()
    )
    if last is None:
        raise PrescriptionError(_("مفيش روشتة قبل كده للمريض ده."))
    for it in last.items.all():
        add_item(rx, drug=it.drug, drug_name=it.drug_name, instructions=it.instructions, duration=it.duration,
                 form=it.form)  # fmt: skip
    if last.advice and not rx.advice:
        update_fields(rx, advice=last.advice)
    return last


@transaction.atomic
def save_as_template(rx, name):
    name = (name or "").strip()
    if not name:
        raise PrescriptionError(_("اكتب اسم للقالب."))
    tpl = PrescriptionTemplate.objects.create(organization=rx.organization, doctor=rx.doctor, name=name[:100],
                                              advice=rx.advice)  # fmt: skip
    for it in rx.items.all():
        PrescriptionTemplateItem.objects.create(
            organization=rx.organization, template=tpl, drug=it.drug, drug_name=it.drug_name, form=it.form,
            instructions=it.instructions, duration=it.duration, notes=it.notes, order=it.order,
        )  # fmt: skip
    return tpl


# --- finalize / revise / void ----------------------------------------------------------------


def _next_number(org, year):
    seq, _created = PrescriptionSequence.objects.select_for_update().get_or_create(organization=org, year=year)
    seq.last += 1
    seq.save(update_fields=["last", "updated_at"])
    return f"RX-{year}-{seq.last:05d}"


def _snapshots(rx):
    p, d = rx.patient, rx.doctor
    today = timezone.localtime(timezone.now(), CAIRO).date()
    age = None
    if p.date_of_birth:
        dob = p.date_of_birth
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    patient = {"name": p.full_name, "age": age, "gender": p.get_gender_display(), "file_number": p.file_number}
    doctor = {
        "name_ar": f"{d.title_ar} {d.name_ar}".strip(),
        "name_en": d.name_en,
        "specialty_ar": d.specialty_ar,
        "qualifications": list(d.qualifications or []),
    }
    return patient, doctor


@transaction.atomic
def finalize(rx, *, by, acknowledged=()):
    """draft → final: number, snapshots, immutable items. Every allergy/duplicate warning must be acknowledged and
    every dictated line confirmed. The PDF is rendered right after (outside the lock)."""
    rx = Prescription.objects.select_for_update().get(pk=rx.pk)
    _require_draft(rx)
    items = list(rx.items.all())
    if not items:
        raise PrescriptionError(_("الروشتة فاضية."))
    if any(i.needs_review for i in items):
        raise PrescriptionError(_("فيه سطور متملية بالصوت لسه محتاجة مراجعة."))
    pending = [w for w in safety.warnings_for(rx) if w["key"] not in set(acknowledged)]
    if pending:
        raise PrescriptionError(_("لازم تأكد التحذيرات الأول: ") + " · ".join(w["text"] for w in pending))
    now = timezone.now()
    if rx.revision_of_id:
        rx.number = rx.revision_of.number
    else:
        rx.number = _next_number(rx.organization, timezone.localtime(now, CAIRO).year)
    rx.patient_snapshot, rx.doctor_snapshot = _snapshots(rx)
    rx.allergy_ack = list(acknowledged)
    rx.status = RxStatus.FINAL
    rx.issued_at, rx.issued_by = now, by
    rx.save()
    Drug.objects.filter(pk__in=[i.drug_id for i in items if i.drug_id]).update(usage_count=F("usage_count") + 1)
    transaction.on_commit(lambda: ensure_pdf(rx))
    return rx


def ensure_pdf(rx):
    """Render and store the `full` PDF once (the copy sent to patients). Failures are logged, not raised: printing
    regenerates on demand and the send step retries."""
    from .pdf import render_prescription

    rx.refresh_from_db()
    if rx.pdf_full or rx.status == RxStatus.DRAFT:
        return rx.pdf_full
    try:
        data = render_prescription(rx, mode="full")
        rx.pdf_full.save(f"{rx.display_number}.pdf", ContentFile(data), save=False)
    except Exception:  # Chromium missing/crashed, or storage not writable — never break the caller
        logger.exception("could not store the PDF of prescription %s", rx.pk)
        return None
    Prescription.objects.filter(pk=rx.pk).update(pdf_full=rx.pdf_full.name)
    return rx.pdf_full


@transaction.atomic
def revise(rx, *, by):
    """Editing a final prescription = a new draft revision (`-R1`, `-R2`...) with the same lines."""
    if rx.status != RxStatus.FINAL:
        raise PrescriptionError(_("التعديل بيكون على روشتة معتمدة بس."))
    root = rx.revision_of or rx
    open_draft = Prescription.objects.filter(revision_of=root, status=RxStatus.DRAFT).first()
    if open_draft:
        return open_draft
    last_rev = Prescription.objects.filter(Q(pk=root.pk) | Q(revision_of=root)).aggregate(m=Max("revision"))["m"]
    new = Prescription.objects.create(
        organization=rx.organization, visit=rx.visit, patient=rx.patient, doctor=rx.doctor, revision=last_rev + 1,
        revision_of=root, advice=rx.advice, next_visit_date=rx.next_visit_date, send_to_patient=rx.send_to_patient,
    )  # fmt: skip
    for it in rx.items.all():
        PrescriptionItem.objects.create(
            organization=rx.organization, prescription=new, drug=it.drug, drug_name=it.drug_name, form=it.form,
            instructions=it.instructions, duration=it.duration, notes=it.notes, order=it.order,
        )  # fmt: skip
    return new


def void(rx, *, reason, by):
    if rx.status != RxStatus.FINAL:
        raise PrescriptionError(_("مينفعش تلغي غير روشتة معتمدة."))
    rx.status = RxStatus.VOIDED
    rx.void_reason = (reason or "")[:200]
    rx._history_user = by
    rx.save(update_fields=["status", "void_reason", "updated_at"])
    return rx


def open_drafts_with_items(visit):
    return Prescription.objects.filter(visit=visit, status=RxStatus.DRAFT, items__isnull=False).distinct()
