"""Quotation business rules: drafts, items, price memory, numbering, finalizing, revisions."""

from dataclasses import dataclass
from datetime import date

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.models import Product, Unit
from apps.documents.pdf import generate_final_pdf_safely
from apps.organizations.models import DEFAULT_QUOTE_NUMBER_FORMAT, OrganizationSettings

from .models import (
    OTHER_SECTION_NAME,
    OTHER_SECTION_ORDER,
    Quotation,
    QuotationItem,
    QuotationStatus,
    QuoteSequence,
)


class FinalizeError(Exception):
    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = problems


def _settings(org) -> OrganizationSettings:
    return OrganizationSettings.objects.get_or_create(organization=org)[0]


def section_order(category) -> int:
    return category.group.sort_order * 10000 + category.sort_order


# --- drafts --------------------------------------------------------------------------------


def new_draft(org, user, customer=None, contact=None) -> Quotation:
    s = _settings(org)
    today = timezone.localdate()
    return Quotation.objects.create(
        organization=org,
        created_by=user,
        customer=customer,
        contact=contact,
        issue_date=today,
        valid_until=s.default_valid_until(today),
        prices_include_vat=s.prices_include_vat,
        vat_rate=s.vat_rate,
        intro_text=s.default_intro_text,
        price_note=s.price_note,
        terms=list(s.default_terms),
    )


def _copy_into(target: Quotation, source: Quotation):
    for it in source.items.all():
        if it.product_id and target.items.filter(product_id=it.product_id).exists():
            continue
        QuotationItem.objects.create(
            organization_id=target.organization_id,
            quotation=target,
            product_id=it.product_id,
            category_name=it.category_name,
            section_order=it.section_order,
            item_order=it.item_order,
            description=it.description,
            unit_name=it.unit_name,
            unit_price=it.unit_price,
        )


@transaction.atomic
def duplicate(source: Quotation, user) -> Quotation:
    """Copy as a brand-new quotation (new number when issued)."""
    draft = new_draft(source.organization, user, source.customer, source.contact)
    draft.intro_text, draft.terms, draft.price_note = source.intro_text, list(source.terms), source.price_note
    draft.save()
    _copy_into(draft, source)
    return draft


@transaction.atomic
def create_revision(source: Quotation, user) -> Quotation:
    """Editable copy of an issued quotation; keeps its number and gets -R1, -R2... when issued."""
    if source.is_editable:
        raise ValueError("Only issued quotations can be revised")
    draft = duplicate(source, user)
    draft.parent_quotation = source
    draft.save(update_fields=["parent_quotation", "updated_at"])
    return draft


@transaction.atomic
def copy_last_for_customer(target: Quotation) -> Quotation | None:
    last = (
        Quotation.objects.for_org(target.organization)
        .issued()
        .filter(customer=target.customer)
        .exclude(pk=target.pk)
        .order_by("-finalized_at", "-id")
        .first()
    )
    if last:
        _copy_into(target, last)
    return last


# --- items ---------------------------------------------------------------------------------


def add_product(q: Quotation, product: Product) -> tuple[QuotationItem, bool]:
    existing = q.items.filter(product=product).first()
    if existing:
        return existing, False
    item = QuotationItem.objects.create(
        organization_id=q.organization_id,
        quotation=q,
        product=product,
        category_name=product.category.name,
        section_order=section_order(product.category),
        item_order=product.sort_order,
        description=product.name,
        unit_name=product.unit.name,
    )
    return item, True


def add_category(q: Quotation, category) -> int:
    added = 0
    for p in category.products.filter(is_active=True).select_related("category__group", "unit"):
        added += add_product(q, p)[1]
    return added


def add_custom(q: Quotation, *, description, unit_name, category=None, add_to_catalog=False, unit_price=None):
    description, unit_name = " ".join(description.split()), " ".join(unit_name.split())
    if add_to_catalog and category is not None:
        product = Product.objects.filter(organization_id=q.organization_id, category=category, name=description).first()
        if product is None:
            last = category.products.aggregate(m=Max("sort_order"))["m"] or 0
            product = Product.objects.create(
                organization_id=q.organization_id,
                category=category,
                name=description,
                unit=Unit.get_for(q.organization, unit_name),
                sort_order=last + 1,
            )
        item, _ = add_product(q, product)
    else:
        last = q.items.filter(product__isnull=True).aggregate(m=Max("item_order"))["m"] or 0
        item = QuotationItem.objects.create(
            organization_id=q.organization_id,
            quotation=q,
            category_name=category.name if category else OTHER_SECTION_NAME,
            section_order=section_order(category) if category else OTHER_SECTION_ORDER,
            item_order=100000 + last + 1,  # after catalog items of the same section
            description=description,
            unit_name=unit_name,
        )
    if unit_price is not None:
        item.unit_price = unit_price
        item.save(update_fields=["unit_price", "updated_at"])
    return item


# --- price memory --------------------------------------------------------------------------


@dataclass
class PriceHint:
    customer_price: object = None
    customer_date: date | None = None
    last_price: object = None
    last_date: date | None = None


def price_hints(q: Quotation, product_ids) -> dict[int, PriceHint]:
    """Last price quoted for each product: to this customer, and to anyone."""
    hints = {pid: PriceHint() for pid in product_ids}
    if not hints:
        return hints
    rows = (
        QuotationItem.objects.filter(
            organization_id=q.organization_id,
            product_id__in=hints,
            unit_price__isnull=False,
            quotation__status__in=[s for s in QuotationStatus.values if s not in ("draft", "cancelled")],
        )
        .exclude(quotation=q)
        .order_by("-quotation__issue_date", "-quotation__finalized_at")
        .values_list("product_id", "unit_price", "quotation__issue_date", "quotation__customer_id")
    )
    for pid, price, day, customer_id in rows:
        h = hints[pid]
        if h.last_price is None:
            h.last_price, h.last_date = price, day
        if h.customer_price is None and q.customer_id and customer_id == q.customer_id:
            h.customer_price, h.customer_date = price, day
    return hints


# --- numbering & finalize ------------------------------------------------------------------


def next_number(org, code: str, issue_date: date) -> str:
    """RG-2026/09/24, then RG-2026/09/24-2 ... Must run inside transaction.atomic()."""
    fmt = _settings(org).quote_number_format or DEFAULT_QUOTE_NUMBER_FORMAT
    key = fmt.format(code=code, prefix=code, year=issue_date.year, month=issue_date.month, day=issue_date.day, seq=1)
    QuoteSequence.objects.get_or_create(organization=org, key=key)
    seq = QuoteSequence.objects.select_for_update().get(organization=org, key=key)
    seq.last_value += 1
    seq.save(update_fields=["last_value", "updated_at"])
    return key if seq.last_value == 1 else f"{key}-{seq.last_value}"


def finalize_problems(q: Quotation) -> list[str]:
    problems = []
    if q.customer_id is None:
        problems.append(_("اختار العميل."))
    elif not q.customer.code:
        problems.append(_("العميل ملوش كود. اكتبه من صفحة العميل."))
    if q.contact_id is None:
        problems.append(_("اختار المسؤول."))
    elif q.contact.customer_id != q.customer_id:
        problems.append(_("المسؤول مش تابع للعميل ده."))
    items = list(q.items.all())
    if not items:
        problems.append(_("ضيف صنف واحد على الأقل."))
    missing = [it for it in items if not it.unit_price or it.unit_price <= 0]
    if missing:
        problems.append(_("فيه %(n)s صنف من غير سعر.") % {"n": len(missing)})
    if q.valid_until < q.issue_date:
        problems.append(_("تاريخ السريان قبل تاريخ العرض."))
    if q.parent_quotation_id and q.parent_quotation.customer_id != q.customer_id:
        problems.append(_("النسخة المعدّلة لازم تكون لنفس العميل."))
    return problems


def _snapshots(q: Quotation):
    c, ct = q.customer, q.contact
    q.customer_snapshot = {
        "id": c.pk,
        "name": c.name,
        "code": c.code,
        "kind": c.kind,
        "recipient_lines": [[str(t), n] for t, n in c.recipient_lines()],
    }
    q.contact_snapshot = {
        "id": ct.pk,
        "name": ct.name,
        "salutation": ct.salutation,
        "job_title": ct.job_title,
        "department": ct.department,
        "channels": [{"type": ch.type, "value": ch.value, "primary": ch.is_primary} for ch in ct.channels.all()],
    }


@transaction.atomic
def finalize(q: Quotation, user) -> Quotation:
    q = Quotation.objects.select_for_update(of=("self",)).select_related("customer", "contact").get(pk=q.pk)
    if not q.is_editable:
        raise FinalizeError([_("العرض ده صادر بالفعل.")])
    problems = finalize_problems(q)
    if problems:
        raise FinalizeError(problems)

    if q.number:
        pass  # re-issued after "تعديل": keeps its number (and revision)
    elif q.parent_quotation_id:
        root = Quotation.objects.select_for_update().get(pk=q.root.pk)
        q.number = root.number
        top = Quotation.objects.filter(organization_id=q.organization_id, number=root.number).aggregate(
            m=Max("revision")
        )["m"]
        q.revision = (top or 0) + 1
        Quotation.objects.filter(pk=q.parent_quotation_id).update(status=QuotationStatus.SUPERSEDED)
    else:
        q.number = next_number(q.organization, q.customer.code, q.issue_date)
        q.revision = 0

    _snapshots(q)
    q.items_count = q.items.count()
    q.status = QuotationStatus.FINALIZED
    q.finalized_by = user
    q.finalized_at = timezone.now()
    q.save()
    from apps.audit.services import log

    log("quote.finalized", org=q.organization, actor=user, target=q, summary=f"{q.display_number} — {q.customer.name}")
    # Render the stored PDF once the number is committed (Celery in production, inline locally).
    transaction.on_commit(lambda: generate_final_pdf_safely(q.pk))
    return q


# --- edit issued / delete / expiry ---------------------------------------------------------------


@transaction.atomic
def reopen(q: Quotation, user) -> Quotation:
    """Turn an issued quotation back into an editable draft, keeping its number. Re-issuing renders a new PDF."""
    q = Quotation.objects.select_for_update(of=("self",)).get(pk=q.pk)
    if q.is_editable:
        return q
    if q.pdf_file:
        q.pdf_file.delete(save=False)
    q.pdf_file = ""
    q.pdf_generated_at = None
    q.status = QuotationStatus.DRAFT
    q.save(update_fields=["pdf_file", "pdf_generated_at", "status", "updated_at"])
    q.reviews.all().delete()  # the old PDF was reviewed, not the new one
    return q


@transaction.atomic
def delete_quotation(q: Quotation):
    if q.pdf_file:
        q.pdf_file.delete(save=False)
    Quotation.objects.filter(parent_quotation=q).update(parent_quotation=q.parent_quotation)
    q.delete()


def expire_overdue(org=None) -> int:
    """Issued quotations past their validity date become 'منتهي'. Cheap; called on page loads and by a command."""
    qs = Quotation.objects.filter(
        status__in=[QuotationStatus.FINALIZED, QuotationStatus.SENT], valid_until__lt=timezone.localdate()
    )
    if org is not None:
        qs = qs.filter(organization=org)
    expired = list(qs.values_list("pk", "organization_id", "number", "revision"))
    if not expired:
        return 0
    Quotation.objects.filter(pk__in=[e[0] for e in expired]).update(status=QuotationStatus.EXPIRED)
    from apps.audit.models import AuditEvent

    AuditEvent.objects.bulk_create(
        AuditEvent(organization_id=o, action="quote.expired", target_type="quotation", target_id=pk,
                   summary=f"{num}-R{rev}" if rev else num)
        for pk, o, num, rev in expired
    )  # fmt: skip
    return len(expired)
