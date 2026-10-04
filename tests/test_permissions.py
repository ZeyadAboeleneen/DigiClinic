import pytest
from django.urls import reverse

from apps.accounts.permissions import ROLE_PERMISSIONS, has_perm
from apps.organizations.models import Membership, Role


@pytest.mark.parametrize(
    ("role", "allowed"),
    [("viewer", False), ("reception", False), ("doctor", False), ("admin", True), ("owner", True)],
)
def test_settings_page_access_by_role(client, org_a, make_member, role, allowed):
    client.force_login(make_member(org_a, role))
    resp = client.get(reverse("organizations:settings"))
    assert resp.status_code == (200 if allowed else 403)


def test_reception_cannot_post_settings_directly(client, org_a, make_member):
    client.force_login(make_member(org_a, "reception"))
    resp = client.post(reverse("organizations:settings"), {"org-name_ar": "hacked"})
    assert resp.status_code == 403
    org_a.refresh_from_db()
    assert org_a.name_ar != "hacked"


@pytest.mark.parametrize("role", ["viewer", "reception", "doctor"])
def test_users_page_forbidden_below_admin(client, org_a, make_member, role):
    client.force_login(make_member(org_a, role))
    assert client.get(reverse("accounts:users")).status_code == 403
    assert client.post(reverse("accounts:invite_create"), {}).status_code == 403


def test_sidebar_hides_admin_links_for_reception(client, org_a, make_member):
    client.force_login(make_member(org_a, "reception"))
    html = client.get(reverse("dashboard:home")).content.decode()
    assert reverse("organizations:settings") not in html
    assert reverse("accounts:users") not in html


def test_sidebar_shows_admin_links_for_owner(client, org_a, make_member):
    client.force_login(make_member(org_a, "owner"))
    html = client.get(reverse("dashboard:home")).content.decode()
    assert reverse("organizations:settings") in html
    assert reverse("accounts:users") in html


def test_owner_has_every_permission():
    assert ROLE_PERMISSIONS[Role.ADMIN] <= ROLE_PERMISSIONS[Role.OWNER]
    assert ROLE_PERMISSIONS[Role.DOCTOR] <= ROLE_PERMISSIONS[Role.OWNER]
    assert ROLE_PERMISSIONS[Role.RECEPTION] <= ROLE_PERMISSIONS[Role.OWNER]


def test_unknown_permission_raises():
    with pytest.raises(ValueError):
        has_perm(Membership(role=Role.OWNER), "does.not.exist")


def test_admin_cannot_modify_owner(client, org_a, make_member):
    owner = make_member(org_a, "owner")
    client.force_login(make_member(org_a, "admin"))
    m = Membership.objects.get(user=owner)
    assert client.post(reverse("accounts:membership_toggle", args=[m.pk])).status_code == 403
    assert client.post(reverse("accounts:membership_role", args=[m.pk]), {"role": "reception"}).status_code == 403
    m.refresh_from_db()
    assert m.is_active and m.role == Role.OWNER


def test_admin_cannot_grant_owner(client, org_a, make_member):
    reception = make_member(org_a, "reception")
    client.force_login(make_member(org_a, "admin"))
    m = Membership.objects.get(user=reception)
    client.post(reverse("accounts:membership_role", args=[m.pk]), {"role": "owner"})
    m.refresh_from_db()
    assert m.role == Role.RECEPTION


def test_cannot_deactivate_self(client, org_a, make_member):
    owner = make_member(org_a, "owner")
    client.force_login(owner)
    m = Membership.objects.get(user=owner)
    assert client.post(reverse("accounts:membership_toggle", args=[m.pk])).status_code == 403
