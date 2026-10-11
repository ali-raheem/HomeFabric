"""Channel adapters. Recurrence and planning code never depends on these.

To add a provider, subclass ``Adapter`` and register its dotted path in
``settings.NOTIFICATION_ADAPTERS``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils.module_loading import import_string

log = logging.getLogger("homefabric.notifications")


@dataclass
class Message:
    subject: str
    body: str


class DeliveryError(Exception):
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class Adapter:
    name = ""
    label = ""
    needs_secret = False
    secret_label = ""
    secret_help = ""

    def validate(self, endpoint) -> None:
        """Raise ValueError if the endpoint configuration is unusable."""

    def send(self, endpoint, message: Message) -> None:
        raise NotImplementedError


class EmailAdapter(Adapter):
    name = "email"
    label = "Email"

    def validate(self, endpoint) -> None:
        from django.core.validators import validate_email

        validate_email(endpoint.address)

    def send(self, endpoint, message: Message) -> None:
        if not settings.EMAIL_CONFIGURED and not settings.DEBUG:
            raise DeliveryError("SMTP is not configured on this instance (FABRIC_EMAIL_HOST).",
                                retryable=False)
        try:
            EmailMessage(subject=message.subject, body=message.body,
                         to=[endpoint.address]).send(fail_silently=False)
        except (TimeoutError, OSError) as exc:
            raise DeliveryError(f"SMTP error: {exc}") from exc
        except Exception as exc:  # smtplib errors subclass OSError mostly; be defensive
            raise DeliveryError(f"SMTP error: {exc}") from exc


class AppriseAdapter(Adapter):
    name = "apprise"
    label = "Apprise (Telegram, Signal, ntfy, Slack, Discord, and more)"
    needs_secret = True
    secret_label = "Apprise URL"
    secret_help = "For example ntfy://topic or tgram://bottoken/ChatID. Stored privately and masked."

    def validate(self, endpoint) -> None:
        import apprise

        secret = endpoint.resolve_secret()
        if not secret:
            raise ValueError("Enter an Apprise URL (or an environment variable reference).")
        obj = apprise.Apprise()
        if not obj.add(secret):
            raise ValueError("Apprise does not recognise that URL.")

    def send(self, endpoint, message: Message) -> None:
        import apprise

        secret = endpoint.resolve_secret()
        if not secret:
            raise DeliveryError("The Apprise URL is missing; re-enter it in your notification "
                                "settings.", retryable=False)
        obj = apprise.Apprise()
        if not obj.add(secret):
            raise DeliveryError("Apprise does not recognise the configured URL.", retryable=False)
        ok = obj.notify(title=message.subject, body=message.body)
        if not ok:
            raise DeliveryError("The Apprise provider reported a delivery failure.")


class LogAdapter(Adapter):
    """Development channel: writes the message to the application log."""

    name = "log"
    label = "Application log (development)"
    sent: list = []

    def send(self, endpoint, message: Message) -> None:
        LogAdapter.sent.append((endpoint.pk, message.subject))
        log.info("Notification for %s: %s", endpoint.user, message.subject)


def registry() -> dict[str, Adapter]:
    return {name: import_string(path)() for name, path in settings.NOTIFICATION_ADAPTERS.items()}


def get_adapter(name: str) -> Adapter:
    path = settings.NOTIFICATION_ADAPTERS.get(name)
    if not path:
        raise DeliveryError(f"The '{name}' channel is not enabled on this instance.",
                            retryable=False)
    return import_string(path)()
