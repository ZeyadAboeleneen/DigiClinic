"""Health checks (07 §7.4): `/healthz/` for Docker/uptime monitors, and `problems()` for the owner alert command.

`/healthz/` reveals nothing sensitive (no counts, no names): only whether the database answers and whether the message
scheduler is alive. It returns 503 when the database is down so container health checks restart the right thing.
"""

from datetime import timedelta

from django.db import connection
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache

ALERT_AFTER = timedelta(minutes=15)


@never_cache
def healthz(request):
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False
    scheduler_ok = False
    if db_ok:
        from apps.notifications.services import scheduler_alive

        scheduler_ok = scheduler_alive()
    return JsonResponse({"db": db_ok, "scheduler": scheduler_ok}, status=200 if db_ok else 503)


def problems(org, now=None) -> list[tuple[str, str]]:
    """[(key, Arabic description)] of what the owner should be told about for this clinic."""
    from apps.messaging import whatsapp
    from apps.messaging.models import ChannelKind, ChannelStatus, SendingChannelConfig
    from apps.notifications.services import last_heartbeat

    now = now or timezone.now()
    found = []
    hb = last_heartbeat()
    if hb is None or now - hb.beat_at > ALERT_AFTER:
        found.append(("scheduler", "محرك الرسايل واقف من أكتر من 15 دقيقة — التذكيرات مش بتتبعت."))
    cfg = SendingChannelConfig.objects.filter(organization=org, kind=ChannelKind.WHATSAPP, is_active=True).first()
    if cfg is not None:
        state = whatsapp.status(org).get("state")
        disconnected_since = cfg.last_checked_at if cfg.status != ChannelStatus.CONNECTED else None
        if state != "ready" and (disconnected_since is None or now - disconnected_since > ALERT_AFTER):
            found.append(
                ("whatsapp", f"الواتساب مش متصل ({whatsapp.STATE_LABELS.get(state, state)}) — اربطه من الإعدادات.")
            )
    return found
