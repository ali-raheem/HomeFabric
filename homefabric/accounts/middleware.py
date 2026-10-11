from homefabric.accounts.models import Household, Membership

SESSION_KEY = "current_household_id"


def resolve_household(request):
    """Return (household, role) for the request's user, or (None, None)."""
    user = request.user
    if not user.is_authenticated:
        return None, None
    wanted = request.session.get(SESSION_KEY)
    memberships = Membership.objects.filter(
        user=user, is_active=True, household__archived_at__isnull=True
    ).select_related("household")
    if user.is_instance_admin:
        if wanted:
            household = Household.objects.filter(pk=wanted, archived_at__isnull=True).first()
            if household:
                return household, "admin"
        m = memberships.first()
        if m:
            return m.household, "admin"
        household = Household.objects.filter(archived_at__isnull=True).first()
        return (household, "admin") if household else (None, None)
    if wanted:
        m = memberships.filter(household_id=wanted).first()
        if m:
            return m.household, m.role
    m = memberships.first()
    if m:
        request.session[SESSION_KEY] = str(m.household_id)
        return m.household, m.role
    return None, None


class HouseholdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.household, request.role = resolve_household(request)
        return self.get_response(request)
