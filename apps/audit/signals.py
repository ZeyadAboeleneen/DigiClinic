from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from apps.organizations.models import Membership

from .services import log


def _org_of(user):
    m = Membership.objects.filter(user=user, is_active=True).select_related("organization").order_by("id").first()
    return m.organization if m else None


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    log("auth.login", request=request, org=_org_of(user), actor=user, summary=user.email)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is not None:
        log("auth.logout", request=request, org=_org_of(user), actor=user, summary=user.email)


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    from apps.accounts.models import User

    email = (credentials or {}).get("username", "") or ""
    user = User.objects.filter(email=email.lower()).first() if email else None
    log(
        "auth.login_failed",
        request=request,
        org=_org_of(user) if user else None,
        summary=email[:150],  # the attempted email only, never the password
    )
