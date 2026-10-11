import os
import tempfile
from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from homefabric import __version__
from homefabric.accounts.permissions import household_view, instance_admin_required
from homefabric.core import audit
from homefabric.core.models import AuditEvent
from homefabric.core.pagination import paginate
from homefabric.notifications import services as notify
from homefabric.operations import maintenance_lock
from homefabric.operations.health import readiness
from homefabric.operations.jobs import scheduler_health
from homefabric.operations.models import SystemState


@never_cache
def healthz(request):
    return JsonResponse({"status": "ok", "version": __version__})


@never_cache
def readyz(request):
    report = readiness()
    return JsonResponse(report, status=200 if report["ok"] else 503)


class ResumeForm(forms.Form):
    public_url_verified = forms.BooleanField(
        label=f"Links in notifications use {settings.PUBLIC_BASE_URL}, and that is correct")
    backlog = forms.ChoiceField(widget=forms.RadioSelect, initial="discard", choices=[
        ("discard", "Discard pending notifications and resume with new reminders only"),
        ("send", "Send pending notifications now")])


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


@instance_admin_required
def instance_status(request):
    db_file = Path(settings.DATABASES["default"]["NAME"])
    state = SystemState.get()
    rollback_copies = sorted(p.name for p in settings.DATA_DIR.glob("rollback-*"))
    ctx = {
        "version": __version__,
        "data_dir": settings.DATA_DIR,
        "db_size": db_file.stat().st_size if db_file.exists() else 0,
        "blob_size": _dir_size(settings.BLOB_DIR),
        "scheduler": scheduler_health(),
        "state": state,
        "notify": notify.health_summary(),
        "preview": notify.resume_preview(),
        "lock": maintenance_lock.current(),
        "resume_form": ResumeForm(),
        "email_configured": settings.EMAIL_CONFIGURED,
        "public_url": settings.PUBLIC_BASE_URL,
        "rollback_copies": rollback_copies,
        "audit": AuditEvent.objects.select_related("household")[:15],
        "readiness": readiness(),
        "debug": settings.DEBUG,
    }
    return render(request, "operations/instance_status.html", ctx)


@require_POST
@instance_admin_required
def notifications_pause(request):
    notify.pause(request.POST.get("reason") or "Paused by administrator", actor=request.user)
    messages.success(request, "Notification delivery paused.")
    return redirect("operations:instance_status")


@require_POST
@instance_admin_required
def notifications_resume(request):
    form = ResumeForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Confirm the public URL before resuming delivery.")
        return redirect("operations:instance_status")
    result = notify.resume(actor=request.user, public_url_verified=True,
                           discard_backlog=form.cleaned_data["backlog"] == "discard")
    messages.success(request, f"Delivery resumed. {result['discarded']} pending job(s) "
                     "discarded.")
    return redirect("operations:instance_status")


@instance_admin_required
def integrity(request):
    from homefabric.operations.integrity import check

    report = check()
    return render(request, "operations/integrity.html", {"report": report})


@household_view("export")
def household_export(request):
    if request.method != "POST":
        return render(request, "operations/export.html")
    from homefabric.operations.export import write_export

    tmp = tempfile.NamedTemporaryFile(prefix="export-", suffix=".zip", dir=settings.STAGING_DIR,
                                      delete=False)
    tmp.close()
    path = write_export(request.household, tmp.name)
    audit.record(request.user, "household.export", request.household,
                 household=request.household)
    handle = open(path, "rb")  # noqa: SIM115 - closed by FileResponse
    try:
        os.unlink(path)  # POSIX: removed once closed
    except OSError:
        pass  # Windows keeps it; check_integrity --clean removes stale staging files
    name = f"homefabric-{request.household.name}-{timezone.now():%Y%m%d}.zip".replace(" ", "_")
    return FileResponse(handle, as_attachment=True, filename=name,
                        content_type="application/zip")


@household_view("manage")
def household_audit(request):
    events = AuditEvent.objects.filter(household=request.household).select_related("actor")
    return render(request, "operations/audit.html", {"page": paginate(request, events, 50)})
