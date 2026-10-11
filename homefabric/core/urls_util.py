from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme


def safe_redirect(request, target: str | None, default: str):
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                                  require_https=request.is_secure()):
        return redirect(target)
    return redirect(default)
