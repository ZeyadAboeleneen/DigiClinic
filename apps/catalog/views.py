from django.db import transaction
from django.db.models import Prefetch
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import has_perm, require_perm
from apps.audit import services as audit

from .forms import CategoryForm, GroupForm, ProductForm
from .models import Category, Product, ProductGroup, Unit


def _catalog_ctx(request):
    org = request.organization
    q = request.GET.get("q", "").strip()
    group_id = request.GET.get("group", "")
    can_edit = has_perm(request.membership, "product.edit")
    show_all = can_edit and request.GET.get("show") == "all"

    products = Product.objects.for_org(org).select_related("unit")
    categories = Category.objects.for_org(org)
    groups = ProductGroup.objects.for_org(org)
    if not show_all:
        products = products.filter(is_active=True)
        categories = categories.filter(is_active=True)
        groups = groups.filter(is_active=True)
    if q:
        products = products.search(q)
    categories = categories.prefetch_related(Prefetch("products", queryset=products.order_by("sort_order", "id")))
    groups_qs = groups.prefetch_related(Prefetch("categories", queryset=categories.order_by("sort_order", "id")))

    tree = []
    for g in groups_qs:
        if group_id and str(g.pk) != group_id:
            continue
        cats = [(c, list(c.products.all())) for c in g.categories.all()]
        if q:
            cats = [(c, ps) for c, ps in cats if ps]
            if not cats:
                continue
        tree.append((g, cats, sum(len(ps) for _, ps in cats)))

    return {
        "tree": tree,
        "all_groups": ProductGroup.objects.for_org(org).filter(is_active=True),
        "q": q,
        "group_id": group_id,
        "show_all": show_all,
        "total": sum(n for *_, n in tree),
        "units": Unit.objects.for_org(org).values_list("name", flat=True),
    }


def _render_body(request):
    resp = render(request, "catalog/partials/body.html", _catalog_ctx(request))
    resp["HX-Retarget"] = "#catalog-body"
    resp["HX-Reswap"] = "outerHTML"
    return resp


@require_perm("product.view")
def catalog(request):
    ctx = _catalog_ctx(request)
    if request.htmx and request.htmx.target == "catalog-body":
        return render(request, "catalog/partials/body.html", ctx)
    return render(request, "catalog/index.html", ctx)


# --- products --------------------------------------------------------------------------


def _units(request):
    return Unit.objects.for_org(request.organization).values_list("name", flat=True)


@require_perm("product.edit")
def product_create(request, category_pk):
    category = get_object_or_404(Category.objects.for_org(request.organization), pk=category_pk)
    form = ProductForm(request.POST or None, organization=request.organization, initial={"category": category})
    if request.method == "POST" and form.is_valid():
        form.save()
        return _render_body(request)
    return render(
        request,
        "catalog/partials/product_form_row.html",
        {"form": form, "category": category, "units": _units(request)},
    )


@require_perm("product.edit")
def product_edit(request, pk):
    product = get_object_or_404(Product.objects.for_org(request.organization).select_related("unit"), pk=pk)
    old_category = product.category_id
    form = ProductForm(request.POST or None, instance=product, organization=request.organization)
    if request.method == "POST" and form.is_valid():
        product = form.save()
        if product.category_id != old_category:
            return _render_body(request)
        return render(request, "catalog/partials/product_row.html", {"p": product, "can_edit": True})
    return render(
        request,
        "catalog/partials/product_form_row.html",
        {"form": form, "product": product, "category": product.category, "units": _units(request)},
    )


@require_perm("product.view")
def product_row(request, pk):
    product = get_object_or_404(Product.objects.for_org(request.organization).select_related("unit"), pk=pk)
    can_edit = has_perm(request.membership, "product.edit")
    return render(request, "catalog/partials/product_row.html", {"p": product, "can_edit": can_edit})


@require_POST
@require_perm("product.edit")
def product_toggle(request, pk):
    product = get_object_or_404(Product.objects.for_org(request.organization).select_related("unit"), pk=pk)
    product.is_active = not product.is_active
    product.save()
    return render(request, "catalog/partials/product_row.html", {"p": product, "can_edit": True})


# --- groups & categories ---------------------------------------------------------------


def _simple_form(request, form_cls, template, instance=None, **extra):
    form = form_cls(request.POST or None, instance=instance, organization=request.organization, **extra)
    if request.method == "POST" and form.is_valid():
        form.save()
        return _render_body(request)
    return render(request, template, {"form": form, "instance": instance})


@require_perm("product.edit")
def group_create(request):
    return _simple_form(request, GroupForm, "catalog/partials/group_form.html")


@require_perm("product.edit")
def group_edit(request, pk):
    group = get_object_or_404(ProductGroup.objects.for_org(request.organization), pk=pk)
    return _simple_form(request, GroupForm, "catalog/partials/group_form.html", instance=group)


@require_perm("product.edit")
def category_create(request, group_pk):
    group = get_object_or_404(ProductGroup.objects.for_org(request.organization), pk=group_pk)
    return _simple_form(request, CategoryForm, "catalog/partials/category_form.html", initial={"group": group})


@require_perm("product.edit")
def category_edit(request, pk):
    category = get_object_or_404(Category.objects.for_org(request.organization), pk=pk)
    return _simple_form(request, CategoryForm, "catalog/partials/category_form.html", instance=category)


@require_POST
@require_perm("product.edit")
def group_toggle(request, pk):
    group = get_object_or_404(ProductGroup.objects.for_org(request.organization), pk=pk)
    group.is_active = not group.is_active
    group.save(update_fields=["is_active", "updated_at"])
    return _render_body(request)


@require_POST
@require_perm("product.edit")
def category_toggle(request, pk):
    category = get_object_or_404(Category.objects.for_org(request.organization), pk=pk)
    category.is_active = not category.is_active
    category.save(update_fields=["is_active", "updated_at"])
    return _render_body(request)


# --- drag & drop ordering --------------------------------------------------------------

REORDERABLE = {"product": (Product, "category_id"), "category": (Category, "group_id"), "group": (ProductGroup, None)}


@require_POST
@require_perm("product.edit")
def reorder(request):
    """Body: kind=product|category|group & ids=1&ids=2… in the new order. Items must share one parent."""
    spec = REORDERABLE.get(request.POST.get("kind"))
    try:
        ids = [int(i) for i in request.POST.getlist("ids")]
    except ValueError:
        return HttpResponseBadRequest("bad ids")
    if spec is None or not ids or len(ids) != len(set(ids)):
        return HttpResponseBadRequest("bad request")
    model, parent_field = spec
    objs = {o.pk: o for o in model.objects.for_org(request.organization).filter(pk__in=ids)}
    if len(objs) != len(ids):
        return HttpResponseBadRequest("unknown ids")
    if parent_field and len({getattr(o, parent_field) for o in objs.values()}) != 1:
        return HttpResponseBadRequest("mixed parents")
    with transaction.atomic():
        for position, pk in enumerate(ids, start=1):
            objs[pk].sort_order = position
        model.objects.bulk_update(objs.values(), ["sort_order"])
    return HttpResponse(status=204)


@require_POST
@require_perm("product.edit")
def product_delete(request, pk):
    product = get_object_or_404(Product.objects.for_org(request.organization), pk=pk)
    name = product.name
    product.delete()  # quotation items keep their own copy of the name/unit/price
    audit.log("product.deleted", request=request, summary=name)
    return HttpResponse("")


@require_POST
@require_perm("product.edit")
def category_delete(request, pk):
    category = get_object_or_404(Category.objects.for_org(request.organization), pk=pk)
    if category.products.exists():
        resp = HttpResponse(status=204)
        resp["HX-Trigger"] = '{"notify": "القسم فيه أصناف. امسحها أو انقلها الأول."}'
        return resp
    category.delete()
    return _render_body(request)


@require_POST
@require_perm("product.edit")
def group_delete(request, pk):
    group = get_object_or_404(ProductGroup.objects.for_org(request.organization), pk=pk)
    if group.categories.exists():
        resp = HttpResponse(status=204)
        resp["HX-Trigger"] = '{"notify": "المجموعة فيها أقسام. امسحها أو انقلها الأول."}'
        return resp
    group.delete()
    return _render_body(request)
