from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.accounts.permissions import require_perm
from apps.audit import services as audit

from .forms import ChannelForm, ContactForm, CustomerForm
from .models import Contact, ContactChannel, Customer, CustomerKind

PAGE_SIZE = 50


def _customers(request):
    return Customer.objects.for_org(request.organization)


def _contacts(request):
    return Contact.objects.for_org(request.organization)


@require_perm("customer.view")
def customer_list(request):
    q = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "")
    show = request.GET.get("show", "active")
    qs = _customers(request).select_related("parent", "parent__parent")
    if q:
        qs = qs.search(q)
    if kind in CustomerKind.values:
        qs = qs.filter(kind=kind)
    if show != "all":
        qs = qs.filter(is_active=True)
    qs = qs.annotate(contacts_count=Count("contacts", filter=Q(contacts__is_active=True), distinct=True))
    page = Paginator(qs.order_by("name"), PAGE_SIZE).get_page(request.GET.get("page"))
    ctx = {"page": page, "q": q, "kind": kind, "show": show, "kinds": CustomerKind.choices}
    if request.htmx and request.htmx.target == "customer-rows":
        return render(request, "customers/partials/rows.html", ctx)
    return render(request, "customers/list.html", ctx)


@require_perm("customer.edit")
def customer_create(request):
    initial = {}
    parent_id = request.GET.get("parent")
    if parent_id and parent_id.isdigit():
        parent = _customers(request).filter(pk=parent_id).first()
        if parent:
            initial["parent"] = parent
    form = CustomerForm(request.POST or None, organization=request.organization, initial=initial)
    if request.method == "POST" and form.is_valid():
        customer = form.save()
        messages.success(request, _("العميل اتضاف. ضيف المسؤولين دلوقتي."))
        return redirect("customers:detail", pk=customer.pk)
    return render(request, "customers/form.html", {"form": form})


@require_perm("customer.edit")
def customer_edit(request, pk):
    customer = get_object_or_404(_customers(request), pk=pk)
    form = CustomerForm(request.POST or None, instance=customer, organization=request.organization)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("بيانات العميل اتحفظت."))
        return redirect("customers:detail", pk=customer.pk)
    return render(request, "customers/form.html", {"form": form, "customer": customer})


@require_POST
@require_perm("customer.edit")
def customer_toggle(request, pk):
    customer = get_object_or_404(_customers(request), pk=pk)
    customer.is_active = not customer.is_active
    customer.save(update_fields=["is_active", "updated_at"])
    messages.success(request, _("العميل اتفعّل.") if customer.is_active else _("العميل اتوقف."))
    return redirect("customers:detail", pk=customer.pk)


def _contacts_ctx(request, customer):
    contacts = (
        _contacts(request)
        .filter(customer=customer)
        .prefetch_related(Prefetch("channels", queryset=ContactChannel.objects.order_by("type", "-is_primary", "id")))
    )
    return {"customer": customer, "contacts": contacts}


@require_perm("customer.view")
def customer_detail(request, pk):
    customer = get_object_or_404(_customers(request).select_related("parent", "parent__parent"), pk=pk)
    ctx = _contacts_ctx(request, customer)
    ctx["children"] = _customers(request).filter(parent=customer).order_by("name")
    ctx["quotes"] = customer.quotations.for_org(request.organization).order_by("-issue_date", "-id")[:20]
    return render(request, "customers/detail.html", ctx)


def _render_contacts(request, customer):
    """Inline forms target themselves; on success swap the whole contacts section instead."""
    resp = render(request, "customers/partials/contacts.html", _contacts_ctx(request, customer))
    resp["HX-Retarget"] = "#contacts"
    resp["HX-Reswap"] = "outerHTML"
    return resp


def _form_status(request, form):
    # htmx doesn't swap 4xx responses by default, so inline forms with errors come back as 200.
    return 400 if form.errors and not request.htmx else 200


@require_perm("customer.view")
def contacts_partial(request, customer_pk):
    customer = get_object_or_404(_customers(request), pk=customer_pk)
    return _render_contacts(request, customer)


@require_perm("customer.edit")
def contact_create(request, customer_pk):
    customer = get_object_or_404(_customers(request), pk=customer_pk)
    form = ContactForm(request.POST or None, customer=customer)
    if request.method == "POST" and form.is_valid():
        form.save()
        if request.htmx:
            return _render_contacts(request, customer)
        messages.success(request, _("المسؤول اتضاف."))
        return redirect("customers:detail", pk=customer.pk)
    tpl = "customers/partials/contact_form.html" if request.htmx else "customers/contact_form_page.html"
    return render(request, tpl, {"form": form, "customer": customer}, status=_form_status(request, form))


@require_perm("customer.edit")
def contact_edit(request, pk):
    contact = get_object_or_404(_contacts(request).select_related("customer"), pk=pk)
    customer = contact.customer
    form = ContactForm(request.POST or None, instance=contact, customer=customer)
    if request.method == "POST" and form.is_valid():
        form.save()
        if request.htmx:
            return _render_contacts(request, customer)
        messages.success(request, _("بيانات المسؤول اتحفظت."))
        return redirect("customers:detail", pk=customer.pk)
    tpl = "customers/partials/contact_form.html" if request.htmx else "customers/contact_form_page.html"
    ctx = {"form": form, "customer": customer, "contact": contact}
    return render(request, tpl, ctx, status=_form_status(request, form))


@require_POST
@require_perm("customer.edit")
def contact_toggle(request, pk):
    contact = get_object_or_404(_contacts(request).select_related("customer"), pk=pk)
    contact.is_active = not contact.is_active
    if not contact.is_active:
        contact.is_primary = False
    contact.save()
    if request.htmx:
        return _render_contacts(request, contact.customer)
    return redirect("customers:detail", pk=contact.customer_id)


@require_perm("customer.edit")
def channel_add(request, contact_pk):
    contact = get_object_or_404(_contacts(request).select_related("customer"), pk=contact_pk)
    form = ChannelForm(request.POST or None, contact=contact)
    if request.method == "POST" and form.is_valid():
        form.save()
        if request.htmx:
            return _render_contacts(request, contact.customer)
        return redirect("customers:detail", pk=contact.customer_id)
    tpl = "customers/partials/channel_form.html" if request.htmx else "customers/channel_form_page.html"
    return render(request, tpl, {"form": form, "contact": contact}, status=_form_status(request, form))


@require_POST
@require_perm("customer.edit")
def channel_delete(request, pk):
    channel = get_object_or_404(
        ContactChannel.objects.for_org(request.organization).select_related("contact__customer"), pk=pk
    )
    contact = channel.contact
    was_primary = channel.is_primary
    channel.delete()
    if was_primary:
        replacement = contact.channels.filter(type=channel.type).order_by("id").first()
        if replacement:
            replacement.is_primary = True
            replacement.save()
    if request.htmx:
        return _render_contacts(request, contact.customer)
    return redirect("customers:detail", pk=contact.customer_id)


@require_perm("customer.view")
def customer_history(request, pk):
    customer = get_object_or_404(_customers(request), pk=pk)
    records = customer.history.select_related("history_user").order_by("-history_date")[:50]
    return render(request, "customers/partials/history.html", {"customer": customer, "records": records})


@require_POST
@require_perm("customer.delete")
def customer_delete(request, pk):
    customer = get_object_or_404(_customers(request), pk=pk)
    if customer.quotations.exists():
        messages.error(request, _("العميل ده عليه عروض أسعار. امسح عروضه الأول، أو دوس «إيقاف» بدل المسح."))
        return redirect("customers:detail", pk=customer.pk)
    if customer.children.exists():
        messages.error(request, _("فيه فروع تابعة للعميل ده. امسحها أو انقلها الأول."))
        return redirect("customers:detail", pk=customer.pk)
    name = customer.name
    customer.delete()
    audit.log("customer.deleted", request=request, summary=name)
    messages.success(request, _("العميل «%(n)s» اتمسح.") % {"n": name})
    return redirect("customers:list")


@require_POST
@require_perm("customer.edit")
def contact_delete(request, pk):
    contact = get_object_or_404(_contacts(request).select_related("customer"), pk=pk)
    customer = contact.customer
    if contact.quotations.exists():
        messages.error(request, _("المسؤول ده متسجل على عروض. دوس «إيقاف» بدل المسح."))
    else:
        contact.delete()
        messages.success(request, _("المسؤول اتمسح."))
    if request.htmx:
        resp = _render_contacts(request, customer)
        return resp
    return redirect("customers:detail", pk=customer.pk)
