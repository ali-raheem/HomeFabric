import datetime as dt

from django.conf import settings
from django.contrib.auth.backends import ModelBackend
from django.utils import timezone

from homefabric.accounts.models import LoginAttempt

PER_IP_MULTIPLIER = 4


def client_ip(request) -> str:
    if request is None:
        return ""
    return (request.META.get("REMOTE_ADDR") or "")[:64]


def is_throttled(username: str, ip: str) -> bool:
    since = timezone.now() - dt.timedelta(seconds=settings.LOGIN_FAILURE_WINDOW_SECONDS)
    recent = LoginAttempt.objects.filter(timestamp__gte=since, succeeded=False)
    if recent.filter(username__iexact=username).count() >= settings.LOGIN_FAILURE_LIMIT:
        return True
    if ip and recent.filter(ip_address=ip).count() >= settings.LOGIN_FAILURE_LIMIT * PER_IP_MULTIPLIER:
        return True
    return False


class ThrottledModelBackend(ModelBackend):
    """ModelBackend with per-username and per-IP failure throttling."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(self._username_field_name())
        if not username or password is None:
            return None
        ip = client_ip(request)
        if is_throttled(username, ip):
            if request is not None:
                request.login_throttled = True
            return None
        user = super().authenticate(request, username=username, password=password, **kwargs)
        LoginAttempt.objects.create(
            username=username[:150], ip_address=ip, succeeded=user is not None
        )
        if user is not None:
            # Successful login clears the username's failure history.
            LoginAttempt.objects.filter(username__iexact=username, succeeded=False).delete()
        return user

    @staticmethod
    def _username_field_name():
        from django.contrib.auth import get_user_model

        return get_user_model().USERNAME_FIELD
