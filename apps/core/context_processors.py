from django.conf import settings

from apps.accounts.permissions import permissions_for


def organization(request):
    membership = getattr(request, "membership", None)
    org = getattr(request, "organization", None)
    return {
        "current_org": org,
        "current_org_settings": getattr(org, "settings", None) if org else None,
        "current_membership": membership,
        "can": permissions_for(membership),
        "desk_idle_lock_minutes": settings.DESK_IDLE_LOCK_MINUTES,
    }
