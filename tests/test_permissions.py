from itertools import pairwise

import pytest
from django.urls import reverse

from apps.accounts.permissions import ROLE_PERMISSIONS, has_perm
from apps.organizations.models import Membership, Role


@pytest.mark.parametrize(
    ("role", "allowed"),
    [("viewer", False), ("sales", False), ("manager", False), ("admin", True), ("owner", True)],
)
def test_settings_page_access_by_role(client, org_a, make_member, role, allowed):
    client.force_login(make_member(org_a, role))
    resp = client.get(reverse("organizations:settings"))
    assert resp.status_code == (200 if allowed else 403)


def test_sales_cannot_post_settings_directly(client, org_a, make_member):
    client.force_login(make_member(org_a, "sales"))
    resp = client.post(reverse("organizations:settings"), {"org-name_ar": "hacked"})
    assert resp.status_code == 403
    org_a.refresh_from_db()
    assert org_a.name_ar != "hacked"


@pytest.mark.parametrize("role", ["viewer", "sales", "manager"])
def test_users_page_forbidden_below_admin(client, org_a, make_member, role):
    client.force_login(make_member(org_a, role))
    assert client.get(reverse("accounts:users")).status_code == 403
    assert client.post(reverse("accounts:invite_create"), {}).status_code == 403


def test_sidebar_hides_admin_links_for_sales(client, org_a, make_member):
    client.force_login(make_member(org_a, "sales"))
    html = client.get(reverse("dashboard:home")).content.decode()
    assert reverse("organizations:settings") not in html
    assert reverse("accounts:users") not in html


def test_sidebar_shows_admin_links_for_owner(client, org_a, make_member):
    client.force_login(make_member(org_a, "owner"))
    html = client.get(reverse("dashboard:home")).content.decode()
    assert reverse("organizations:settings") in html
    assert reverse("accounts:users") in html


def test_matrix_is_cumulative():
    order = [Role.VIEWER, Role.SALES, Role.MANAGER, Role.ADMIN, Role.OWNER]
    for lower, higher in pairwise(order):
        assert ROLE_PERMISSIONS[lower] < ROLE_PERMISSIONS[higher]


def test_unknown_permission_raises():
    with pytest.raises(ValueError):
        has_perm(Membership(role=Role.OWNER), "does.not.exist")


def test_admin_cannot_modify_owner(client, org_a, make_member):
    owner = make_member(org_a, "owner")
    client.force_login(make_member(org_a, "admin"))
    m = Membership.objects.get(user=owner)
    assert client.post(reverse("accounts:membership_toggle", args=[m.pk])).status_code == 403
    assert client.post(reverse("accounts:membership_role", args=[m.pk]), {"role": "sales"}).status_code == 403
    m.refresh_from_db()
    assert m.is_active and m.role == Role.OWNER


def test_admin_cannot_grant_owner(client, org_a, make_member):
    sales = make_member(org_a, "sales")
    client.force_login(make_member(org_a, "admin"))
    m = Membership.objects.get(user=sales)
    client.post(reverse("accounts:membership_role", args=[m.pk]), {"role": "owner"})
    m.refresh_from_db()
    assert m.role == Role.SALES


def test_cannot_deactivate_self(client, org_a, make_member):
    owner = make_member(org_a, "owner")
    client.force_login(owner)
    m = Membership.objects.get(user=owner)
    assert client.post(reverse("accounts:membership_toggle", args=[m.pk])).status_code == 403
