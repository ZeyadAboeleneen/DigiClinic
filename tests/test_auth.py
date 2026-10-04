from django.urls import reverse

from apps.accounts.models import User

PASSWORD = "Strong-Pass-2026!"


def test_login_and_logout(client, org_a, make_member):
    make_member(org_a, "reception", email="sales@example.com")
    resp = client.post(reverse("accounts:login"), {"username": "sales@example.com", "password": PASSWORD})
    assert resp.status_code == 302
    assert resp.url == reverse("dashboard:home")
    assert client.get(reverse("dashboard:home")).status_code == 200

    resp = client.post(reverse("accounts:logout"))
    assert resp.status_code == 302
    assert client.get(reverse("dashboard:home")).status_code == 302


def test_login_email_is_case_insensitive(client, org_a, make_member):
    make_member(org_a, "reception", email="sales@example.com")
    resp = client.post(reverse("accounts:login"), {"username": "Sales@Example.com", "password": PASSWORD})
    assert resp.status_code == 302


def test_wrong_password_shows_error(client, org_a, make_member):
    make_member(org_a, "reception", email="sales@example.com")
    resp = client.post(reverse("accounts:login"), {"username": "sales@example.com", "password": "wrong-pass"})
    assert resp.status_code == 200
    assert "غلط" in resp.content.decode()


def test_lockout_after_five_failures(client, org_a, make_member):
    make_member(org_a, "reception", email="sales@example.com")
    for _ in range(5):
        client.post(reverse("accounts:login"), {"username": "sales@example.com", "password": "nope"})
    resp = client.post(reverse("accounts:login"), {"username": "sales@example.com", "password": PASSWORD})
    assert resp.status_code == 429
    assert "مقفول" in resp.content.decode()


def test_anonymous_is_redirected_to_login(client, db):
    resp = client.get(reverse("dashboard:home"))
    assert resp.status_code == 302
    assert resp.url.startswith(reverse("accounts:login"))


def test_passwords_are_hashed_with_argon2(settings, db):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher"]
    user = User.objects.create_user(email="x@example.com", password=PASSWORD, full_name="X")
    assert user.password.startswith("argon2")


def test_user_without_membership_gets_403(client, db):
    client.force_login(User.objects.create_user(email="lonely@example.com", password=PASSWORD, full_name="Lonely"))
    assert client.get(reverse("dashboard:home")).status_code == 403


def test_inactive_membership_gets_403(client, org_a, make_member):
    user = make_member(org_a, "reception")
    user.memberships.update(is_active=False)
    client.force_login(user)
    assert client.get(reverse("dashboard:home")).status_code == 403


def test_lockout_counts_email_case_insensitively(client, org_a, make_member):
    make_member(org_a, "reception", email="sales@example.com")
    for i in range(5):
        email = "SALES@example.com" if i % 2 else "sales@example.com"
        client.post(reverse("accounts:login"), {"username": email, "password": "nope"})
    resp = client.post(reverse("accounts:login"), {"username": "sales@example.com", "password": PASSWORD})
    assert resp.status_code == 429
