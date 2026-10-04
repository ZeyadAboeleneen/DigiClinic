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
