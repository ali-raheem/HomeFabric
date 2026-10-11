import hashlib
import secrets
import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.utils.crypto import salted_hmac


class User(AbstractUser):
    """Custom user model, established before the initial migration.

    ``is_superuser`` marks the instance administrator. Household ownership is
    a membership role and never implies instance administration.
    """

    display_name = models.CharField(max_length=150, blank=True)
    # Bumping the epoch invalidates every existing session for the user.
    session_epoch = models.PositiveIntegerField(default=0)

    def _get_session_auth_hash(self, secret=None):
        key_salt = "homefabric.accounts.User.session_auth_hash"
        return salted_hmac(
            key_salt, f"{self.password}:{self.session_epoch}", secret=secret, algorithm="sha256"
        ).hexdigest()

    def get_session_auth_hash(self):
        return self._get_session_auth_hash()

    def get_session_auth_fallback_hash(self):
        for fallback_secret in settings.SECRET_KEY_FALLBACKS:
            yield self._get_session_auth_hash(secret=fallback_secret)

    @property
    def label(self) -> str:
        return self.display_name or self.get_full_name() or self.username

    @property
    def is_instance_admin(self) -> bool:
        return self.is_active and self.is_superuser

    def __str__(self):
        return self.label


class Household(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    timezone = models.CharField(max_length=64, default="Europe/London")
    currency = models.CharField(max_length=3, default="GBP")
    created_at = models.DateTimeField(auto_now_add=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Role(models.TextChoices):
    VIEWER = "viewer", "Viewer"
    EDITOR = "editor", "Editor"
    OWNER = "owner", "Household owner"


ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3, "admin": 4}


class Membership(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.VIEWER)
    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "household"], name="unique_membership")
        ]
        ordering = ["household__name", "user__username"]

    def __str__(self):
        return f"{self.user} in {self.household} ({self.role})"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Invitation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField(blank=True)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.VIEWER)
    token_hash = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    used_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Invitation to {self.household} ({self.role})"

    @staticmethod
    def new_token() -> str:
        return secrets.token_urlsafe(32)

    @property
    def is_usable(self) -> bool:
        return not self.used_at and not self.revoked_at and self.expires_at > timezone.now()

    @property
    def state(self) -> str:
        if self.used_at:
            return "used"
        if self.revoked_at:
            return "revoked"
        if self.expires_at <= timezone.now():
            return "expired"
        return "pending"


class LoginAttempt(models.Model):
    username = models.CharField(max_length=150, db_index=True)
    ip_address = models.CharField(max_length=64, blank=True, db_index=True)
    succeeded = models.BooleanField(default=False)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    def __str__(self):
        return f"{self.username} {'ok' if self.succeeded else 'failed'} {self.timestamp}"
