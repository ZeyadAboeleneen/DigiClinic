from apps.organizations.models import Membership

SESSION_ORG_KEY = "current_org_id"


class CurrentOrganizationMiddleware:
    """Resolves `request.organization` and `request.membership` from the user's active membership."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.organization = None
        request.membership = None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            memberships = (
                Membership.objects.filter(user=user, is_active=True, organization__is_active=True)
                .select_related("organization")
                .order_by("id")
            )
            chosen = None
            org_id = request.session.get(SESSION_ORG_KEY)
            if org_id:
                chosen = memberships.filter(organization_id=org_id).first()
            if chosen is None:
                chosen = memberships.first()
            if chosen is not None:
                request.membership = chosen
                request.organization = chosen.organization
                request.session[SESSION_ORG_KEY] = chosen.organization_id
        return self.get_response(request)


# 04 §4.5. Everything is served locally (vendored HTMX/Alpine, embedded fonts). Inline scripts and Alpine's
# expression evaluator still need 'unsafe-inline'/'unsafe-eval'; the policy's value is that nothing can be loaded
# from or sent to another origin (no external scripts, no exfiltration via fetch/img/form).
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "frame-src 'self'",
    "frame-ancestors 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])  # fmt: skip


class ContentSecurityPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(self), geolocation=()")
        return response
