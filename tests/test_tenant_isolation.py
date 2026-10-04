"""A user of org B must never see or change anything in org A, even with direct IDs in the URL."""

from django.urls import reverse

from apps.accounts.models import Invitation
from apps.organizations.models import Membership


def test_admin_of_b_cannot_toggle_member_of_a(client, org_a, org_b, make_member):
    victim = make_member(org_a, "sales")
    client.force_login(make_member(org_b, "owner"))
    m = Membership.objects.get(user=victim)
    assert client.post(reverse("accounts:membership_toggle", args=[m.pk])).status_code == 404
    m.refresh_from_db()
    assert m.is_active


def test_admin_of_b_cannot_change_role_in_a(client, org_a, org_b, make_member):
    victim = make_member(org_a, "sales")
    client.force_login(make_member(org_b, "owner"))
    m = Membership.objects.get(user=victim)
    resp = client.post(reverse("accounts:membership_role", args=[m.pk]), {"role": "admin"})
    assert resp.status_code == 404
    m.refresh_from_db()
    assert m.role == "sales"


def test_admin_of_b_cannot_revoke_invite_of_a(client, org_a, org_b, make_member):
    inviter = make_member(org_a, "owner")
    inv, _ = Invitation.create_for(
        organization=org_a, email="new@example.com", full_name="New", role="sales", invited_by=inviter
    )
    client.force_login(make_member(org_b, "owner"))
    assert client.post(reverse("accounts:invite_revoke", args=[inv.pk])).status_code == 404
    inv.refresh_from_db()
    assert inv.revoked_at is None


def test_users_page_lists_only_own_org(client, org_a, org_b, make_member):
    make_member(org_a, "sales", email="a-person@example.com")
    Invitation.create_for(
        organization=org_a, email="a-invite@example.com", full_name="A Invite", role="sales", invited_by=None
    )
    client.force_login(make_member(org_b, "owner"))
    html = client.get(reverse("accounts:users")).content.decode()
    assert "a-person@example.com" not in html
    assert "a-invite@example.com" not in html


def test_settings_edit_only_touches_own_org(client, org_a, org_b, make_member):
    client.force_login(make_member(org_b, "owner"))
    data = {
        "org-name_ar": "اسم جديد",
        "org-name_en": "",
        "tagline_ar": "",
        "email_display": "",
        "address_ar": "",
        "address_en": "",
        "phones_text": "",
        "primary_color": "#AE171C",
        "dark_color": "#111111",
        "neutral_color": "#F5F2EF",
        "section_row_color": "#FBECEC",
    }
    assert client.post(reverse("organizations:settings"), data).status_code == 302
    org_a.refresh_from_db()
    org_b.refresh_from_db()
    assert org_a.name_ar == "البرق للتجارة والتوريدات"
    assert org_b.name_ar == "اسم جديد"


def test_for_org_scopes_querysets(org_a, org_b):
    Invitation.create_for(organization=org_a, email="x@example.com", full_name="X", role="sales", invited_by=None)
    assert Invitation.objects.for_org(org_a).count() == 1
    assert Invitation.objects.for_org(org_b).count() == 0
    assert Invitation.objects.for_org(None).count() == 0


def test_logo_is_private(client, org_a, db):
    assert client.get(reverse("organizations:logo")).status_code == 302
