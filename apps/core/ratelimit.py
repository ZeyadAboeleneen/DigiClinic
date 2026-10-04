"""Tiny per-user rate limit (04 §4.5: patient search ≤ 120 requests/minute/user, against scraping by phone number).
Uses Django's cache (per-process locmem by default; a shared cache on a multi-process server)."""

import time
from functools import wraps

from django.core.cache import cache
from django.http import HttpResponse
from django.utils.translation import gettext as _


def ratelimit(key: str, *, per_minute: int):
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            who = request.user.pk if request.user.is_authenticated else request.META.get("REMOTE_ADDR", "")
            bucket = f"rl:{key}:{who}:{int(time.time() // 60)}"
            cache.add(bucket, 0, timeout=70)
            try:
                count = cache.incr(bucket)
            except ValueError:  # evicted between add and incr
                cache.set(bucket, 1, timeout=70)
                count = 1
            if count > per_minute:
                return HttpResponse(_("طلبات كتير — استنى دقيقة."), status=429)
            return view(request, *args, **kwargs)

        wrapper.ratelimit_key = key  # copied up through functools.wraps by outer decorators (tests check it)
        return wrapper

    return decorator


SEARCH_PER_MINUTE = 120
search_limit = ratelimit("patient-search", per_minute=SEARCH_PER_MINUTE)
