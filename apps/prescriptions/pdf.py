"""Prescription PDF (05). Context comes from the prescription's snapshots, never live data — except for drafts
(preview), which use the live patient/doctor."""

from django.template.loader import render_to_string

from apps.documents import pdf

from .models import PrescriptionSettings, PrintMode, RxStatus


def _context(rx, mode):
    org = rx.organization
    org_settings = getattr(org, "settings", None)
    rx_settings = PrescriptionSettings.for_org(org)
    if rx.status == RxStatus.DRAFT:
        from .services import _snapshots

        patient, doctor = _snapshots(rx)
    else:
        patient, doctor = rx.patient_snapshot, rx.doctor_snapshot
    issued = rx.issued_at or rx.updated_at
    return {
        "rx": rx,
        "items": list(rx.items.all()),
        "patient": patient,
        "doctor": doctor,
        "issued": issued,
        "mode": mode,
        "page_size": rx_settings.page_size,
        "s": rx_settings,
        "org_settings": org_settings,
        "clinic_name": (org_settings.clinic_name_ar if org_settings else "") or org.name_ar,
        "logo": pdf.logo_uri(org_settings),
        "fonts": pdf.font_css(),
        "is_draft": rx.status == RxStatus.DRAFT,
    }


def render_html(rx, mode=PrintMode.FULL):
    return render_to_string("pdf/prescription.html", _context(rx, mode))


def render_prescription(rx, mode=PrintMode.FULL) -> bytes:
    """`full` = the clinic's complete design (also what patients receive); `preprinted` = content only, placed
    inside the margins measured on the clinic's pre-printed paper."""
    ctx = _context(rx, mode)
    html = render_to_string("pdf/prescription.html", ctx)
    return pdf.render_pdf(html, prefer_css_page_size=True, print_background=True)


def render_calibration(org) -> bytes:
    s = PrescriptionSettings.for_org(org)
    width, height = (148, 210) if s.page_size == "A5" else (210, 297)
    html = render_to_string(
        "pdf/calibration.html",
        {"s": s, "fonts": pdf.font_css(), "width": width, "height": height,
         "xs": range(0, width + 1, 10), "ys": range(0, height + 1, 10)},
    )  # fmt: skip
    return pdf.render_pdf(html, prefer_css_page_size=True, print_background=True)
