"""Single source of truth for role → permission mapping (docs/plan/04-auth-permissions-security.md §4.2)."""

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from apps.organizations.models import Role

VIEWER = {
    "customer.view",
    "product.view",
    "quotation.view",
}
SALES = VIEWER | {
    "quotation.create",
    "quotation.send",
    "customer.edit",
    "quotation.cancel_own",
}
MANAGER = SALES | {
    "product.edit",
    "quotation.cancel",
    "quotation.delete",
    "customer.delete",
    "audit.view",
}
ADMIN = MANAGER | {
    "user.manage",
    "settings.manage",
}
OWNER = ADMIN | {
    "organization.delete",
    "organization.transfer",
}

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    Role.VIEWER: frozenset(VIEWER),
    Role.SALES: frozenset(SALES),
    Role.MANAGER: frozenset(MANAGER),
    Role.ADMIN: frozenset(ADMIN),
    Role.OWNER: frozenset(OWNER),
}
ALL_PERMISSIONS = frozenset(OWNER)

# Roles an admin may assign. Only an owner can create or promote another owner.
ASSIGNABLE_ROLES = {
    Role.OWNER: [Role.OWNER, Role.ADMIN, Role.MANAGER, Role.SALES, Role.VIEWER],
    Role.ADMIN: [Role.ADMIN, Role.MANAGER, Role.SALES, Role.VIEWER],
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
