import re
import uuid

from homefabric.core import request_context

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get("X-Request-ID", "")
        request.request_id = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex
        token = request_context.set_request_id(request.request_id)
        try:
            response = self.get_response(request)
        finally:
            request_context.reset_request_id(token)
        response["X-Request-ID"] = request.request_id
        return response


class SecurityHeadersMiddleware:
    CSP = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", self.CSP)
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if getattr(request, "user", None) is not None and request.user.is_authenticated:
            response.setdefault("Cache-Control", "private, no-store")
        return response
