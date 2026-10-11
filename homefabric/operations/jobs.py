"""Scheduler job wrapper: overlap prevention, heartbeat, and maintenance respect."""

from __future__ import annotations

import datetime as dt
import logging
import uuid

from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from homefabric.core.redaction import redact
from homefabric.operations import maintenance_lock
from homefabric.operations.models import JobLock, SchedulerHeartbeat

log = logging.getLogger("homefabric.jobs")

JOB_NAMES = ("process_schedules", "process_notifications")


def acquire_lock(name: str, owner: str, ttl_seconds: int) -> bool:
    now = timezone.now()
    try:
        JobLock.objects.get_or_create(name=name)
    except IntegrityError:
        pass
    updated = JobLock.objects.filter(name=name).filter(
        Q(expires_at__isnull=True) | Q(expires_at__lt=now) | Q(owner=owner)
    ).update(owner=owner, expires_at=now + dt.timedelta(seconds=ttl_seconds))
    return updated == 1


def release_lock(name: str, owner: str) -> None:
    JobLock.objects.filter(name=name, owner=owner).update(owner="", expires_at=None)


class JobSkipped(Exception):
    pass


def run_job(name: str, func, *, ttl_seconds: int = 15 * 60):
    if maintenance_lock.is_locked():
        raise JobSkipped("maintenance lock held (backup or restore in progress)")
    owner = uuid.uuid4().hex
    if not acquire_lock(name, owner, ttl_seconds):
        raise JobSkipped("another run is still in progress")
    hb, _ = SchedulerHeartbeat.objects.get_or_create(name=name)
    SchedulerHeartbeat.objects.filter(name=name).update(last_started_at=timezone.now())
    try:
        result = func()
    except Exception as exc:
        SchedulerHeartbeat.objects.filter(name=name).update(
            last_failure_at=timezone.now(), last_error=redact(str(exc))[:1000])
        log.exception("Job %s failed", name)
        raise
    finally:
        release_lock(name, owner)
    SchedulerHeartbeat.objects.filter(name=name).update(
        last_success_at=timezone.now(), last_error="", last_summary=result or {})
    return result


def scheduler_health() -> dict:
    from django.conf import settings

    now = timezone.now()
    stale_after = dt.timedelta(seconds=settings.SCHEDULER_STALE_SECONDS)
    beats = {hb.name: hb for hb in SchedulerHeartbeat.objects.filter(name__in=JOB_NAMES)}
    jobs = []
    for name in JOB_NAMES:
        hb = beats.get(name)
        last = hb.last_success_at if hb else None
        jobs.append({
            "name": name,
            "last_success_at": last,
            "last_failure_at": hb.last_failure_at if hb else None,
            "last_error": hb.last_error if hb else "",
            "stale": last is None or now - last > stale_after,
        })
    last_run = min((j["last_success_at"] for j in jobs if j["last_success_at"]), default=None)
    return {"jobs": jobs, "stale": any(j["stale"] for j in jobs), "last_run": last_run}
