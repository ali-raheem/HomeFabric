from django.conf import settings

from homefabric import __version__
from homefabric.accounts.models import Household, Membership
from homefabric.accounts.permissions import can


def household(request):
    ctx = {
        "current_household": getattr(request, "household", None),
        "current_role": getattr(request, "role", None),
        "app_version": __version__,
        "debug": settings.DEBUG,
    }
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        if user.is_instance_admin:
            ctx["available_households"] = list(Household.objects.all()[:50])
        else:
            ctx["available_households"] = [
                m.household
                for m in Membership.objects.filter(user=user, is_active=True).select_related(
                    "household"
                )
            ]
        ctx["perm"] = {cap: can(request, cap) for cap in ("view", "edit", "manage", "export")}
    return ctx
