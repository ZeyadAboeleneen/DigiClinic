"""Single source of truth for role → permission mapping (docs/plan/04-auth-permissions-security.md §4.3).

Each phase wires up the permissions its new apps need; clinical, patient and billing permissions
from the matrix are added as those apps land.
"""

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from apps.organizations.models import Role

VIEWER = {
    "appointment.view",
}
RECEPTION = VIEWER | {
    "reception.operate",
    "vitals.record",
    "payment.record",
    "patient.view_basic",
    "patient.edit_basic",
    "appointment.book",
    "appointment.reschedule",
    "appointment.cancel",
    "messages.view",
    "messages.retry",
}
DOCTOR = VIEWER | {
    "reception.operate",
    "vitals.record",
    "payment.record",
    "report.finance",
    "schedule.manage",
    "patient.view_basic",
    "patient.edit_basic",
    "patient.view_medical",
    "patient.edit_medical",
    "patient.merge",
    "appointment.book",
    "appointment.reschedule",
    "appointment.cancel",
    "appointment.overbook",
    "messages.view",
    "messages.retry",
}
ADMIN = VIEWER | {
    "user.manage",
    "settings.manage",
    "audit.view",
    "schedule.manage",
    "patient.view_basic",
    "patient.edit_basic",
    "patient.view_medical",
    "patient.edit_medical",
    "patient.merge",
    "appointment.book",
    "appointment.reschedule",
    "appointment.cancel",
    "messages.view",
    "messages.retry",
}
OWNER = ADMIN | DOCTOR | RECEPTION

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    Role.VIEWER: frozenset(VIEWER),
    Role.RECEPTION: frozenset(RECEPTION),
    Role.DOCTOR: frozenset(DOCTOR),
    Role.ADMIN: frozenset(ADMIN),
    Role.OWNER: frozenset(OWNER),
}
ALL_PERMISSIONS = frozenset(ADMIN | DOCTOR | RECEPTION)

# Roles an admin may assign. Only an owner can create or promote another owner.
ASSIGNABLE_ROLES = {
    Role.OWNER: [Role.OWNER, Role.ADMIN, Role.DOCTOR, Role.RECEPTION, Role.VIEWER],
    Role.ADMIN: [Role.ADMIN, Role.DOCTOR, Role.RECEPTION, Role.VIEWER],
}


def has_perm(membership, perm: str) -> bool:
    if perm not in ALL_PERMISSIONS:
        raise ValueError(f"Unknown permission: {perm}")
    if membership is None or not membership.is_active:
        return False
    return perm in ROLE_PERMISSIONS.get(membership.role, frozenset())


class _Can(dict):
    """Template helper: `{% if can.settings_manage %}` (dots become underscores)."""

    def __missing__(self, key):
        return False


def permissions_for(membership) -> _Can:
    perms = ROLE_PERMISSIONS.get(membership.role, frozenset()) if membership and membership.is_active else ()
    return _Can({p.replace(".", "_"): True for p in perms})


def require_perm(perm: str):
    """View decorator. Anonymous → login redirect; no org or missing perm → 403."""
    if perm not in ALL_PERMISSIONS:
        raise ValueError(f"Unknown permission: {perm}")

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not has_perm(getattr(request, "membership", None), perm):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def require_membership(view):
    """Any active member of the current organization."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if getattr(request, "membership", None) is None:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper
