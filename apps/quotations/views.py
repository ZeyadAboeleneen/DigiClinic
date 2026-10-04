import json
from itertools import groupby

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.accounts.permissions import has_perm, require_perm
from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.catalog.models import Category, Product, ProductGroup
from apps.customers.forms import ContactForm
from apps.customers.models import Contact, Customer, CustomerKind
from apps.documents import pdf as pdfdoc
from apps.messaging.services import SENDABLE_STATUSES, record_review

from . import services
from .forms import CustomItemForm, MetaForm, parse_price
from .models import Quotation, QuotationItem, QuotationStatus

PAGE_SIZE = 30


def _quotes(request):
    return Quotation.objects.for_org(request.organization)


def _draft(request, pk):
    q = get_object_or_404(_quotes(request).select_related("customer", "contact"), pk=pk)
    if not q.is_editable:
        raise PermissionDenied
    return q


def _trigger(resp, event="itemsChanged"):
    resp["HX-Trigger"] = event
    return resp


def _sections(items):
    """[(category_name, [items...]), ...] in PDF order; items get a running number `no` (م)."""
    for n, it in enumerate(items, start=1):
        it.no = n
    return [(name, list(group)) for name, group in groupby(items, key=lambda i: i.category_name)]


# --- list ----------------------------------------------------------------------------------


@require_perm("quotation.view")
def quotation_list(request):
    services.expire_overdue(request.organization)
    qs = _quotes(request).select_related("customer", "contact", "created_by")
    status = request.GET.get("status", "")
    customer_id = request.GET.get("customer", "")
    user_id = request.GET.get("user", "")
    q = request.GET.get("q", "").strip()
    date_from, date_to = parse_date(request.GET.get("from", "") or ""), parse_date(request.GET.get("to", "") or "")
    if status in QuotationStatus.values:
        qs = qs.filter(status=status)
    elif status != "all":
        qs = qs.exclude(status=QuotationStatus.SUPERSEDED)
    if customer_id.isdigit():
        qs = qs.filter(customer_id=customer_id)
    if user_id.isdigit():
        qs = qs.filter(created_by_id=user_id)
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(customer__name__icontains=q))
    if date_from:
        qs = qs.filter(issue_date__gte=date_from)
    if date_to:
        qs = qs.filter(issue_date__lte=date_to)
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
    ctx = {
        "page": page,
        "statuses": QuotationStatus.choices,
        "customers": Customer.objects.for_org(request.organization).filter(is_active=True).order_by("name"),
        "users": User.objects.filter(memberships__organization=request.organization).order_by("full_name"),
        "f": {
            "status": status,
            "customer": customer_id,
            "user": user_id,
            "q": q,
            "from": request.GET.get("from", ""),
            "to": request.GET.get("to", ""),
        },
    }
    if request.htmx and request.htmx.target == "quote-rows":
        return render(request, "quotations/partials/list_rows.html", ctx)
    return render(request, "quotations/list.html", ctx)


# --- create / open -------------------------------------------------------------------------


@require_POST
@require_perm("quotation.create")
def quotation_new(request):
    customer = contact = None
    cid = request.POST.get("customer", "")
    if cid.isdigit():
        customer = Customer.objects.for_org(request.organization).filter(pk=cid, is_active=True).first()
        if customer:
            contact = customer.contacts.filter(is_active=True).order_by("-is_primary", "id").first()
    q = services.new_draft(request.organization, request.user, customer, contact)
    return redirect("quotations:edit", pk=q.pk)


@require_perm("quotation.view")
def quotation_detail(request, pk):
    q = get_object_or_404(_quotes(request).select_related("customer", "contact", "created_by", "finalized_by"), pk=pk)
    if q.is_editable and has_perm(request.membership, "quotation.create"):
        return redirect("quotations:edit", pk=q.pk)
    items = list(q.items.all())
    revisions = _quotes(request).filter(number=q.number).exclude(pk=q.pk).order_by("revision") if q.number else []
    ctx = {
        "q": q,
        "sections": _sections(items),
        "revisions": revisions,
        "sendable": q.status in SENDABLE_STATUSES,
        "deliveries": list(q.deliveries.select_related("sent_by")),
        "events": AuditEvent.objects.for_org(request.organization)
        .filter(target_type="quotation", target_id=q.pk)
        .select_related("actor")[:30],
        "can_cancel": has_perm(request.membership, "quotation.cancel")
        or (has_perm(request.membership, "quotation.cancel_own") and q.created_by_id == request.user.id),
    }
    return render(request, "quotations/detail.html", ctx)


# --- builder -------------------------------------------------------------------------------


def _items_ctx(q):
    items = list(q.items.all())
    hints = services.price_hints(q, [i.product_id for i in items if i.product_id])
    for i in items:
        i.hint = hints.get(i.product_id)
    return {
        "q": q,
        "sections": _sections(items),
        "count": len(items),
        "missing": sum(1 for i in items if not i.unit_price),
    }


def _party_ctx(request, q):
    contacts = []
    last_quote = None
    if q.customer_id:
        contacts = (
            Contact.objects.for_org(request.organization)
            .filter(customer=q.customer, is_active=True)
            .prefetch_related("channels")
        )
        last_quote = (
            _quotes(request).issued().filter(customer=q.customer).exclude(pk=q.pk).order_by("-finalized_at").first()
        )
    return {"q": q, "contacts": contacts, "last_quote": last_quote, "locked_customer": bool(q.parent_quotation_id)}


def _picker_ctx(request, q):
    org = request.organization
    term = request.GET.get("pq", "").strip()
    group_id = request.GET.get("pg", "")
    products = Product.objects.for_org(org).filter(is_active=True).select_related("unit")
    if term:
        products = products.search(term)
    cats = (
        Category.objects.for_org(org)
        .filter(is_active=True, group__is_active=True)
        .select_related("group")
        .prefetch_related(Prefetch("products", queryset=products.order_by("sort_order", "id")))
    )
    if group_id.isdigit():
        cats = cats.filter(group_id=group_id)
    tree = [(c, list(c.products.all())) for c in cats]
    tree = [(c, ps) for c, ps in tree if ps]
    return {
        "q": q,
        "tree": tree,
        "groups": ProductGroup.objects.for_org(org).filter(is_active=True),
        "pq": term,
        "pg": group_id,
        "added": set(q.items.exclude(product=None).values_list("product_id", flat=True)),
        "result_count": sum(len(ps) for _, ps in tree),
    }


@require_perm("quotation.create")
def quotation_edit(request, pk):
    q = get_object_or_404(_quotes(request).select_related("customer", "contact", "parent_quotation"), pk=pk)
    if not q.is_editable:
        return redirect("quotations:detail", pk=q.pk)
    ctx = {
        **_items_ctx(q),
        **_party_ctx(request, q),
        **_picker_ctx(request, q),
        "meta_form": MetaForm(instance=q),
        "custom_form": CustomItemForm(organization=request.organization),
    }
    return render(request, "quotations/builder.html", ctx)


@require_perm("quotation.create")
def picker(request, pk):
    q = _draft(request, pk)
    return render(request, "quotations/partials/picker_results.html", _picker_ctx(request, q))


def _render_items(request, q, event="itemsChanged"):
    resp = render(request, "quotations/partials/items.html", _items_ctx(q))
    resp["HX-Retarget"] = "#items"
    resp["HX-Reswap"] = "outerHTML"
    return _trigger(resp, event)


def _render_party(request, q):
    resp = render(request, "quotations/partials/party.html", _party_ctx(request, q))
    resp["HX-Retarget"] = "#party"
    resp["HX-Reswap"] = "outerHTML"
    return resp


# party: customer & contact


@require_perm("quotation.create")
def customer_search(request, pk):
    q = _draft(request, pk)
    term = request.GET.get("cq", "").strip()
    results = Customer.objects.for_org(request.organization).filter(is_active=True).select_related("parent")
    results = results.search(term)[:12] if term else results.order_by("-updated_at")[:8]
    return render(request, "quotations/partials/customer_results.html", {"q": q, "results": results, "cq": term})


@require_POST
@require_perm("quotation.create")
def set_customer(request, pk):
    q = _draft(request, pk)
    if q.parent_quotation_id:
        raise PermissionDenied  # a revision stays with its customer
    cid = request.POST.get("customer", "")
    if not cid:
        q.customer = q.contact = None
    else:
        q.customer = get_object_or_404(Customer.objects.for_org(request.organization), pk=cid)
        q.contact = q.customer.contacts.filter(is_active=True).order_by("-is_primary", "id").first()
    q.save(update_fields=["customer", "contact", "updated_at"])
    return _trigger(_render_party(request, q), "itemsChanged")


@require_POST
@require_perm("quotation.create")
def set_contact(request, pk):
    q = _draft(request, pk)
    contact = get_object_or_404(
        Contact.objects.for_org(request.organization).filter(customer=q.customer), pk=request.POST.get("contact")
    )
    q.contact = contact
    q.save(update_fields=["contact", "updated_at"])
    return _render_party(request, q)


@require_perm("quotation.create")
def quick_contact(request, pk):
    q = _draft(request, pk)
    if not q.customer_id or not has_perm(request.membership, "customer.edit"):
        raise PermissionDenied
    form = ContactForm(request.POST or None, customer=q.customer)
    if request.method == "POST" and form.is_valid():
        q.contact = form.save()
        q.save(update_fields=["contact", "updated_at"])
        return _render_party(request, q)
    return render(request, "quotations/partials/quick_contact.html", {"q": q, "form": form})


@require_perm("quotation.create")
def quick_customer(request, pk):
    q = _draft(request, pk)
    if q.parent_quotation_id or not has_perm(request.membership, "customer.edit"):
        raise PermissionDenied
    error = ""
    if request.method == "POST":
        name = " ".join(request.POST.get("name", "").split())
        kind = request.POST.get("kind", CustomerKind.HOTEL)
        if not name:
            error = _("اكتب اسم العميل.")
        elif Customer.objects.for_org(request.organization).filter(name=name, parent=None).exists():
            error = _("فيه عميل بنفس الاسم، دوّر عليه.")
        else:
            q.customer = Customer.objects.create(
                organization=request.organization,
                name=name,
                kind=kind if kind in CustomerKind.values else CustomerKind.HOTEL,
            )
            q.contact = None
            q.save(update_fields=["customer", "contact", "updated_at"])
            return _render_party(request, q)
    return render(
        request, "quotations/partials/quick_customer.html", {"q": q, "kinds": CustomerKind.choices, "error": error}
    )


@require_POST
@require_perm("quotation.create")
def copy_last(request, pk):
    q = _draft(request, pk)
    if not q.customer_id:
        raise Http404
    source = services.copy_last_for_customer(q)
    if source:
        messages.success(request, _("اتنسخت أصناف وأسعار العرض %(n)s.") % {"n": source.display_number})
    return _render_items(request, q)


# items


@require_POST
@require_perm("quotation.create")
def add_item(request, pk):
    q = _draft(request, pk)
    product = get_object_or_404(
        Product.objects.for_org(request.organization).filter(is_active=True).select_related("category__group", "unit"),
        pk=request.POST.get("product"),
    )
    item, _ = services.add_product(q, product)
    resp = _render_items(request, q)
    if request.POST.get("focus") == "price":
        resp["HX-Trigger"] = json.dumps({"itemsChanged": None, "focusPrice": item.pk})
    return resp


@require_POST
@require_perm("quotation.create")
def add_category(request, pk):
    q = _draft(request, pk)
    category = get_object_or_404(
        Category.objects.for_org(request.organization).select_related("group"), pk=request.POST.get("category")
    )
    services.add_category(q, category)
    return _render_items(request, q)


@require_perm("quotation.create")
def add_custom(request, pk):
    q = _draft(request, pk)
    if request.GET.get("closed"):
        return render(request, "quotations/partials/custom_button.html", {"q": q})
    form = CustomItemForm(request.POST or None, organization=request.organization)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        if d["add_to_catalog"] and not has_perm(request.membership, "product.edit"):
            form.add_error(None, _("إضافة أصناف للكتالوج للمدير بس."))
        else:
            services.add_custom(
                q,
                description=d["description"],
                unit_name=d["unit_name"],
                category=d["category"],
                add_to_catalog=d["add_to_catalog"],
                unit_price=d["unit_price"],
            )
            resp = _render_items(request, q)
            button = render(request, "quotations/partials/custom_button.html", {"q": q}).content.decode()
            resp.content += f'<div id="custom-slot" hx-swap-oob="innerHTML">{button}</div>'.encode()
            return resp
    return render(request, "quotations/partials/custom_form.html", {"q": q, "custom_form": form})


def _item(request, q, item_pk):
    return get_object_or_404(QuotationItem.objects.filter(quotation=q), pk=item_pk)


@require_POST
@require_perm("quotation.create")
def update_item(request, pk, item_pk):
    q = _draft(request, pk)
    item = _item(request, q, item_pk)
    error = ""
    if "unit_price" in request.POST:
        try:
            item.unit_price = parse_price(request.POST["unit_price"])
        except ValidationError as e:
            error = e.messages[0]
    if "description" in request.POST and request.POST["description"].strip():
        item.description = " ".join(request.POST["description"].split())[:250]
    if "unit_name" in request.POST and request.POST["unit_name"].strip():
        item.unit_name = " ".join(request.POST["unit_name"].split())[:50]
    if not error:
        item.save()
    item.hint = services.price_hints(q, [item.product_id]).get(item.product_id) if item.product_id else None
    ctx = {"q": q, "it": item, "error": error, "raw": request.POST.get("unit_price", "")}
    resp = render(request, "quotations/partials/item_row.html", ctx)
    return _trigger(resp)


@require_POST
@require_perm("quotation.create")
def delete_item(request, pk, item_pk):
    q = _draft(request, pk)
    _item(request, q, item_pk).delete()
    return _render_items(request, q)


@require_POST
@require_perm("quotation.create")
def clear_items(request, pk):
    q = _draft(request, pk)
    q.items.all().delete()
    return _render_items(request, q)


# meta & summary


@require_perm("quotation.create")
def summary(request, pk):
    q = _draft(request, pk)
    return render(request, "quotations/partials/summary.html", {"q": q, "problems": services.finalize_problems(q)})


@require_POST
@require_perm("quotation.create")
def update_meta(request, pk):
    q = _draft(request, pk)
    form = MetaForm(request.POST, instance=q)
    if form.is_valid():
        form.save()
        form = MetaForm(instance=q)
    resp = render(request, "quotations/partials/meta.html", {"q": q, "meta_form": form})
    return _trigger(resp)


@require_POST
@require_perm("quotation.create")
def finalize(request, pk):
    q = _draft(request, pk)
    try:
        q = services.finalize(q, request.user)
    except services.FinalizeError as e:
        for p in e.problems:
            messages.error(request, p)
        return redirect("quotations:edit", pk=q.pk)
    messages.success(request, _("العرض اتصدر برقم %(n)s.") % {"n": q.display_number})
    return redirect("quotations:detail", pk=q.pk)


@require_POST
@require_perm("quotation.create")
def delete_draft(request, pk):
    """Drafts: anyone who can create quotations. Issued quotations: owner/manager only."""
    q = get_object_or_404(_quotes(request).select_related("customer"), pk=pk)
    if not q.is_editable and not has_perm(request.membership, "quotation.delete"):
        raise PermissionDenied
    label = q.display_number or f"مسودة #{q.pk}"
    services.delete_quotation(q)
    audit.log("quote.draft_deleted", request=request, summary=f"{label} — {q.customer.name if q.customer else ''}")
    messages.success(request, _("العرض اتمسح."))
    return redirect("quotations:list")


@require_POST
@require_perm("quotation.create")
def reopen(request, pk):
    """«تعديل» on an issued quotation: back to draft with the same number."""
    q = get_object_or_404(_quotes(request), pk=pk)
    if q.status in (QuotationStatus.CANCELLED, QuotationStatus.SUPERSEDED):
        raise PermissionDenied
    services.reopen(q, request.user)
    audit.log("quote.reopened", request=request, target=q, summary=q.display_number)
    messages.success(request, _("العرض رجع مسودة. عدّل وبعدين دوس «إصدار العرض» تاني."))
    return redirect("quotations:edit", pk=q.pk)


# PDF


@require_perm("quotation.view")
def preview_pdf(request, pk):
    """Draft preview with a watermark, rendered on the fly and never stored."""
    q = get_object_or_404(_quotes(request).select_related("organization__settings", "customer", "contact"), pk=pk)
    if not q.is_editable:
        return redirect("quotations:pdf", pk=q.pk)
    if not has_perm(request.membership, "quotation.create"):
        raise PermissionDenied
    try:
        data = pdfdoc.render_pdf(q, draft=True)
    except pdfdoc.PdfRenderError as e:
        return render(request, "quotations/pdf_error.html", {"q": q, "error": str(e)}, status=503)
    resp = HttpResponse(data, content_type="application/pdf")
    resp["Content-Disposition"] = "inline"
    return resp


@xframe_options_sameorigin  # embedded in the review/send page (also its error page)
@require_perm("quotation.view")
def final_pdf(request, pk):
    """The stored PDF of an issued quotation (re-rendered from snapshots if the file is missing)."""
    q = get_object_or_404(_quotes(request).select_related("organization__settings", "customer", "contact"), pk=pk)
    if q.is_editable:
        return redirect("quotations:preview", pk=q.pk)
    if not q.pdf_file or not q.pdf_file.storage.exists(q.pdf_file.name):
        try:
            pdfdoc.generate_final_pdf(q)
        except pdfdoc.PdfRenderError as e:
            return render(request, "quotations/pdf_error.html", {"q": q, "error": str(e)}, status=503)
    if request.GET.get("review") and has_perm(request.membership, "quotation.send"):
        record_review(q, request.user)
    return FileResponse(
        q.pdf_file.open("rb"),
        content_type="application/pdf",
        as_attachment=bool(request.GET.get("download")),
        filename=pdfdoc.download_name(q),
    )


# after issue


@require_POST
@require_perm("quotation.create")
def revise(request, pk):
    q = get_object_or_404(_quotes(request), pk=pk)
    if q.is_editable or q.status in (QuotationStatus.CANCELLED, QuotationStatus.SUPERSEDED):
        raise PermissionDenied
    draft = services.create_revision(q, request.user)
    audit.log("quote.revised", request=request, target=q, summary=q.display_number)
    messages.success(request, _("اتعملت نسخة معدّلة من %(n)s. عدّل وبعدين أصدرها.") % {"n": q.display_number})
    return redirect("quotations:edit", pk=draft.pk)


@require_POST
@require_perm("quotation.create")
def duplicate(request, pk):
    q = get_object_or_404(_quotes(request), pk=pk)
    draft = services.duplicate(q, request.user)
    audit.log("quote.duplicated", request=request, target=q, summary=q.display_number or f"#{q.pk}")
    messages.success(request, _("اتعمل عرض جديد بنفس الأصناف."))
    return redirect("quotations:edit", pk=draft.pk)


STATUS_ACTIONS = {
    "accepted": QuotationStatus.ACCEPTED,
    "rejected": QuotationStatus.REJECTED,
    "cancelled": QuotationStatus.CANCELLED,
}


@require_POST
@require_perm("quotation.view")
def set_status(request, pk):
    q = get_object_or_404(_quotes(request), pk=pk)
    new = STATUS_ACTIONS.get(request.POST.get("status"))
    allowed = has_perm(request.membership, "quotation.cancel") or (
        has_perm(request.membership, "quotation.cancel_own") and q.created_by_id == request.user.id
    )
    if not allowed:
        raise PermissionDenied
    if new is None or q.is_editable or q.status in (QuotationStatus.CANCELLED, QuotationStatus.SUPERSEDED):
        messages.error(request, _("مينفعش تغيّر حالة العرض ده."))
    else:
        q.status = new
        q.save(update_fields=["status", "updated_at"])
        audit.log("quote.status", request=request, target=q, summary=f"{q.display_number} → {q.get_status_display()}")
        messages.success(request, _("الحالة اتغيرت: %(s)s") % {"s": q.get_status_display()})
    return redirect("quotations:detail", pk=q.pk)
