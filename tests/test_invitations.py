from datetime import timedelta

from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Invitation, User
from apps.organizations.models import Membership

PASSWORD = "Strong-Pass-2026!"


def _invite(client, org, make_member, role="sales"):
    client.force_login(make_member(org, "owner"))
    resp = client.post(
        reverse("accounts:invite_create"), {"full_name": "Mona Ali", "email": "Mona@Example.com", "role": role}
    )
    assert resp.status_code == 302
    link = client.session["new_invite_link"]
    client.logout()
    return link


def test_invite_flow_creates_user_and_membership(client, org_a, make_member):
    link = _invite(client, org_a, make_member)
    assert len(mail.outbox) == 1
    assert link in mail.outbox[0].body

    token = link.rstrip("/").rsplit("/", 1)[-1]
    assert Invitation.objects.get().token_hash != token  # only the hash is stored

    path = reverse("accounts:accept_invite", args=[token])
    assert client.get(path).status_code == 200
    resp = client.post(path, {"full_name": "Mona Ali", "password1": PASSWORD, "password2": PASSWORD})
    assert resp.status_code == 302

    user = User.objects.get(email="mona@example.com")
    assert Membership.objects.get(user=user, organization=org_a).role == "sales"
    resp = client.post(reverse("accounts:login"), {"username": "mona@example.com", "password": PASSWORD})
    assert resp.status_code == 302

    # Link is single-use.
    client.logout()
    assert client.get(path).status_code == 404


def test_expired_invite_is_rejected(client, org_a, make_member):
    link = _invite(client, org_a, make_member)
    Invitation.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
    token = link.rstrip("/").rsplit("/", 1)[-1]
    assert client.get(reverse("accounts:accept_invite", args=[token])).status_code == 404


def test_weak_password_rejected(client, org_a, make_member):
    link = _invite(client, org_a, make_member)
    token = link.rstrip("/").rsplit("/", 1)[-1]
    resp = client.post(
        reverse("accounts:accept_invite", args=[token]),
        {"full_name": "Mona", "password1": "12345678", "password2": "12345678"},
    )
    assert resp.status_code == 200
    assert not User.objects.filter(email="mona@example.com").exists()


def test_admin_cannot_invite_owner(client, org_a, make_member):
    client.force_login(make_member(org_a, "admin"))
    resp = client.post(reverse("accounts:invite_create"), {"full_name": "X", "email": "x@example.com", "role": "owner"})
    assert resp.status_code == 400
    assert not Invitation.objects.exists()


def test_create_user_command(org_a):
    from django.core.management import call_command

    call_command("create_user", email="Owner@Example.com", name="Owner", role="owner", org="albarq", password=PASSWORD)
    user = User.objects.get(email="owner@example.com")
    assert user.memberships.get().role == "owner"
    # Idempotent: running again updates, doesn't duplicate.
    call_command("create_user", email="owner@example.com", name="Owner 2", role="admin", org="albarq")
    user.refresh_from_db()
    assert user.full_name == "Owner 2"
    assert user.memberships.count() == 1
    assert user.memberships.get().role == "admin"


def test_seed_org_is_idempotent(db):
    from django.core.management import call_command

    from apps.organizations.models import Organization

    call_command("seed_org")
    org = Organization.objects.get(slug="albarq")
    org.settings.phones = ["0100"]
    org.settings.save()
    call_command("seed_org")  # must not clobber UI edits
    org.settings.refresh_from_db()
    assert org.settings.phones == ["0100"]
    call_command("seed_org", force=True)
    org.settings.refresh_from_db()
    assert org.settings.phones == ["0653451404", "01060777030", "01000967017"]
    assert org.settings.quote_number_format == "{code}-{year}/{month:02d}/{day:02d}"
    assert org.settings.validity_mode == "end_of_month"
    assert len(org.settings.default_terms) == 5
