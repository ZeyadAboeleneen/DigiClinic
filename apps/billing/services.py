from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils.translation import gettext as _

from apps.core.timeutils import CAIRO
from apps.scheduling.models import Appointment

from .models import Payment, PaymentMethod


class PaymentError(Exception):
    pass


@dataclass
class Balance:
    due: Decimal
    paid: Decimal

    @property
    def remaining(self):
        return max(self.due - self.paid, Decimal(0))

    @property
    def status(self):
        if self.due <= 0:
            return "free"
        if self.paid >= self.due:
            return "paid"
        return "partial" if self.paid > 0 else "unpaid"


def balance(appt, payments=None) -> Balance:
    """`payments` can be a prefetched list to avoid a query per row."""
    if payments is None:
        payments = list(appt.payments.all())
    paid = sum((p.signed_amount for p in payments), Decimal(0))
    return Balance(due=appt.price - appt.discount, paid=paid)


@transaction.atomic
def record_payment(appt, *, amount, method=PaymentMethod.CASH, by, note="", is_refund=False):
    appt = Appointment.objects.select_for_update().get(pk=appt.pk)
    amount = Decimal(amount)
    if amount <= 0:
        raise PaymentError(_("المبلغ لازم يكون أكبر من صفر."))
    current = balance(appt)
    if is_refund and amount > current.paid:
        raise PaymentError(_("الاسترداد أكبر من المدفوع."))
    return Payment.objects.create(
        organization=appt.organization,
        appointment=appt,
        amount=amount,
        method=method,
        received_by=by,
        note=note,
        is_refund=is_refund,
    )


@dataclass
class Cashbox:
    day: object
    payments: list
    by_method: list  # [(label, total)]
    total: Decimal


def cashbox(org, day) -> Cashbox:
    start = datetime.combine(day, time.min, tzinfo=CAIRO)
    qs = (
        Payment.objects.for_org(org)
        .filter(received_at__gte=start, received_at__lt=start + timedelta(days=1))
        .select_related("appointment__patient", "appointment__visit_type", "received_by")
    )
    payments = list(qs)
    by_method = []
    for value, label in PaymentMethod.choices:
        income = qs.filter(method=value, is_refund=False).aggregate(s=Sum("amount"))["s"] or Decimal(0)
        refunds = qs.filter(method=value, is_refund=True).aggregate(s=Sum("amount"))["s"] or Decimal(0)
        if income or refunds:
            by_method.append((label, income - refunds))
    total = sum((p.signed_amount for p in payments), Decimal(0))
    return Cashbox(day=day, payments=payments, by_method=by_method, total=total)
