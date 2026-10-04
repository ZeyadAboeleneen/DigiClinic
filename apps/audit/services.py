"""`audit.log(...)`: one call from anywhere to record who did what."""

import logging

from .models import AuditEvent

logger = logging.getLogger(__name__)


def _client_ip(request):
    if request is None:
        return None
    ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR")
    return ip or None


def log(action, *, request=None, org=None, actor=None, target=None, summary="", **metadata):
    """Never raises: a failed audit write must not break the user's action."""
    try:
        if request is not None:
            org = org or getattr(request, "organization", None)
            user = getattr(request, "user", None)
            if actor is None and user is not None and user.is_authenticated:
                actor = user
        return AuditEvent.objects.create(
            organization=org,
            actor=actor,
            action=action,
            target_type=target._meta.model_name if target is not None else "",
            target_id=target.pk if target is not None else None,
            summary=str(summary)[:300],
            metadata=metadata,
            ip_address=_client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT", "")[:300] if request is not None else ""),
        )
    except Exception:
        logger.exception("audit log failed for %s", action)
        return None
