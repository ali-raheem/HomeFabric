"""Household authorization.

Every household-scoped view resolves ``request.household`` and
``request.role`` via HouseholdMiddleware and checks a capability here. Objects
are always loaded through ``scoped()`` so another household's UUID yields 404.
"""

from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect

from homefabric.accounts.models import ROLE_RANK

CAPABILITIES = {
    "view": "viewer",
    "edit": "editor",
    "manage": "owner",
    "export": "owner",
}


def role_allows(role: str | None, capability: str) -> bool:
    if not role:
        return False
    return ROLE_RANK.get(role, 0) >= ROLE_RANK[CAPABILITIES[capability]]


def can(request, capability: str) -> bool:
    return role_allows(getattr(request, "role", None), capability)


def require(request, capability: str) -> None:
    if not can(request, capability):
        raise PermissionDenied("Your household role does not allow this action.")


def household_view(capability: str = "view"):
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                from django.contrib.auth.views import redirect_to_login

                return redirect_to_login(request.get_full_path())
            if getattr(request, "household", None) is None:
                return redirect("accounts:no_household")
            require(request, capability)
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def scoped(request, queryset, **lookup):
    """Fetch one object belonging to the current household or raise 404."""
    obj = queryset.filter(household=request.household, **lookup).first()
    if obj is None:
        raise Http404
    return obj


def instance_admin_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(request.get_full_path())
        if not request.user.is_instance_admin:
            raise PermissionDenied("Instance administrator access is required.")
        return view(request, *args, **kwargs)

    return wrapper
