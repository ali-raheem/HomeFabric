from django.shortcuts import render

from homefabric.operations import maintenance_lock

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


class MaintenanceLockMiddleware:
    """Reject writes while a backup or restore holds the maintenance lock."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method not in SAFE_METHODS and maintenance_lock.is_locked():
            info = maintenance_lock.current() or {}
            response = render(request, "maintenance_lock.html", {"info": info}, status=503)
            response["Retry-After"] = "30"
            return response
        return self.get_response(request)
