import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def pytest_sessionstart(session):
    import devdb

    devdb.start()


@pytest.fixture(autouse=True)
def _reset_axes(db):
    from axes.utils import reset

    reset()


@pytest.fixture
def make_org(db):
    from apps.organizations.models import Organization, OrganizationSettings

    def _make(slug="demo-clinic", name="عيادة ديمو"):
        org = Organization.objects.create(slug=slug, name_ar=name)
        OrganizationSettings.objects.create(organization=org)
        return org

    return _make


@pytest.fixture
def make_member(db):
    from apps.accounts.models import User
    from apps.organizations.models import Membership

    def _make(org, role, email=None, password="Strong-Pass-2026!"):
        email = email or f"{role}-{org.slug}@example.com"
        user = User.objects.create_user(email=email, password=password, full_name=f"{role} {org.slug}")
        Membership.objects.create(user=user, organization=org, role=role)
        return user

    return _make


@pytest.fixture
def org_a(make_org):
    return make_org("demo-clinic")


@pytest.fixture
def org_b(make_org):
    return make_org("other", "شركة تانية")
