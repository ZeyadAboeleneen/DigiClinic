import pytest
from django.urls import reverse

from apps.audit.models import AuditEvent


@pytest.fixture
def world(org_a, make_member):
    owner = make_member(org_a, "owner")
    reception = make_member(org_a, "reception")
    return {"org": org_a, "owner": owner, "reception": reception}


def test_login_and_failed_login_are_logged(client, world):
    client.post(reverse("accounts:login"), {"username": world["reception"].email, "password": "wrong"})
    client.post(reverse("accounts:login"), {"username": world["reception"].email, "password": "Strong-Pass-2026!"})
    actions = list(AuditEvent.objects.values_list("action", flat=True))
    assert "auth.login_failed" in actions and "auth.login" in actions
    failed = AuditEvent.objects.get(action="auth.login_failed")
    assert failed.organization == world["org"] and "wrong" not in failed.summary + str(failed.metadata)


def test_activity_permissions(client, world):
    client.force_login(world["reception"])
    assert client.get(reverse("audit:activity")).status_code == 403
    client.force_login(world["owner"])
    assert client.get(reverse("audit:activity")).status_code == 200


def test_audit_is_append_only(world):
    e = AuditEvent.objects.create(organization=world["org"], action="auth.login")
    e.summary = "x"
    with pytest.raises(PermissionError):
        e.save()
    with pytest.raises(PermissionError):
        e.delete()


def test_activity_is_tenant_scoped(client, world, org_b, make_member):
    AuditEvent.objects.create(organization=world["org"], action="auth.login", summary="secret-a")
    client.force_login(make_member(org_b, "owner"))
    assert "secret-a" not in client.get(reverse("audit:activity")).content.decode()
