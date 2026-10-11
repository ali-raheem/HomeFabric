from django.db import models


class SystemState(models.Model):
    """Singleton row (pk=1) with instance-wide operational flags."""

    notifications_paused = models.BooleanField(default=False)
    paused_reason = models.CharField(max_length=300, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    restored_at = models.DateTimeField(null=True, blank=True)
    restored_from = models.CharField(max_length=300, blank=True)
    public_url_verified = models.BooleanField(default=True)

    def __str__(self):
        return "Notifications paused" if self.notifications_paused else "Notifications active"

    @classmethod
    def get(cls) -> "SystemState":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class SchedulerHeartbeat(models.Model):
    name = models.CharField(max_length=60, primary_key=True)
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=1000, blank=True)
    last_summary = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.name


class JobLock(models.Model):
    """Lease-based lock preventing overlapping scheduler runs on any OS."""

    name = models.CharField(max_length=60, primary_key=True)
    owner = models.CharField(max_length=64, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name
