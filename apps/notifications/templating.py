"""Template variables (06 §6.3): validation at save time, rendering at send time."""

import string
from datetime import date, datetime, timedelta

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from apps.core.phones import to_local
from apps.core.timeutils import CAIRO, WEEKDAY_LABELS, weekday_egypt
from apps.doctors.models import Doctor

VARIABLES = {
    "patient_name": "اسم المريض",
    "first_name": "الاسم الأول",
    "doctor_name": "اسم الدكتور",
    "clinic_name": "اسم العيادة",
    "date": "اليوم والتاريخ",
    "time": "الساعة",
    "time_label": "الساعة/رقم الدور",
    "queue_number": "رقم الدور",
    "visit_type": "نوع الزيارة",
    "price": "السعر",
    "clinic_phone": "تليفون العيادة",
    "clinic_address": "العنوان",
    "map_link": "لينك الخريطة",
    "old_date": "التاريخ القديم",
    "old_time": "الساعة القديمة",
    "patients_ahead": "عدد اللي قدامه",
    "rx_number": "رقم الروشتة",
    "next_visit_date": "ميعاد الإعادة",
}

MONTHS = ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر")


def fmt_date(d: date) -> str:
    """`الثلاثاء 14 أكتوبر`"""
    if isinstance(d, datetime):
        d = d.astimezone(CAIRO).date()
    return f"{WEEKDAY_LABELS[weekday_egypt(d)]} {d.day} {MONTHS[d.month - 1]}"


def fmt_time(dt: datetime) -> str:
    """`4:20 م`"""
    local = dt.astimezone(CAIRO)
    hour = local.hour % 12 or 12
    return f"{hour}:{local.minute:02d} {'ص' if local.hour < 12 else 'م'}"


def fmt_money(value) -> str:
    return f"{value:,.0f} ج.م" if value else _("مجانًا")


def variables_in(body: str) -> set[str]:
    try:
        return {field for _lit, field, _spec, _conv in string.Formatter().parse(body) if field is not None}
    except ValueError as e:
        raise ValidationError(_("فيه قوس { أو } مش مقفول في النص.")) from e


def validate_body(body: str):
    unknown = sorted(v for v in variables_in(body) if v not in VARIABLES)
    if unknown:
        raise ValidationError(_("متغيرات مش معروفة: %(v)s"), params={"v": "، ".join("{" + v + "}" for v in unknown)})


class _Blank(dict):
    def __missing__(self, key):
        return ""


def render(body: str, ctx: dict) -> str:
    text = string.Formatter().vformat(body, (), _Blank(ctx))
    # Empty variables (e.g. no map link) leave blank lines behind; collapse them.
    lines = [line.rstrip() for line in text.splitlines()]
    out = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def _org_ctx(org) -> dict:
    s = getattr(org, "settings", None)
    phones = (s.phones if s else None) or []
    phone = str(phones[0]) if phones else ""
    return {
        "clinic_name": (s.clinic_name_ar if s else "") or org.name_ar,
        "clinic_phone": to_local(phone) if phone.startswith("+") else phone,
        "clinic_address": s.address_ar if s else "",
        "map_link": s.map_link if s else "",
    }


def appointment_ctx(appt) -> dict:
    patient = appt.patient
    ctx = _org_ctx(appt.organization)
    ctx.update(
        patient_name=patient.full_name,
        first_name=patient.full_name.split()[0] if patient.full_name else "",
        doctor_name=str(appt.doctor),
        date=fmt_date(appt.date),
        time=fmt_time(appt.start_at),
        queue_number=appt.queue_number or "",
        visit_type=appt.visit_type.name_ar,
        price=fmt_money(appt.price - appt.discount),
    )
    if appt.queue_number:
        ctx["time_label"] = _("دورك رقم %(n)s — حوالي الساعة %(t)s") % {"n": appt.queue_number, "t": ctx["time"]}
    else:
        ctx["time_label"] = _("الساعة %(t)s") % {"t": ctx["time"]}
    return ctx


def prescription_ctx(rx) -> dict:
    ctx = _org_ctx(rx.organization)
    name = rx.patient_snapshot.get("name") or rx.patient.full_name
    ctx.update(
        patient_name=name,
        first_name=name.split()[0] if name else "",
        doctor_name=rx.doctor_snapshot.get("name_ar") or str(rx.doctor),
        rx_number=rx.display_number,
        next_visit_date=fmt_date(rx.next_visit_date) if rx.next_visit_date else "",
    )
    return ctx


def sample_ctx(org) -> dict:
    """Preview data for the templates screen."""
    ctx = _org_ctx(org)
    day = (datetime.now(CAIRO) + timedelta(days=1)).replace(hour=16, minute=20)
    old = day - timedelta(days=2)
    ctx.update(
        patient_name="محمد أحمد",
        first_name="محمد",
        doctor_name="د. سارة علي",
        date=fmt_date(day.date()),
        time=fmt_time(day),
        time_label=_("الساعة %(t)s") % {"t": fmt_time(day)},
        queue_number=7,
        visit_type="كشف",
        price=fmt_money(300),
        old_date=fmt_date(old.date()),
        old_time=fmt_time(old),
        patients_ahead=3,
        rx_number="RX-0042",
        next_visit_date=fmt_date((day + timedelta(days=14)).date()),
    )
    doctor = Doctor.objects.filter(organization=org).first()
    if doctor:
        ctx["doctor_name"] = str(doctor)
    return ctx
