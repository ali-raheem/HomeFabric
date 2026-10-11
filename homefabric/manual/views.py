import os
import tempfile

from django.conf import settings
from django.http import FileResponse, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.text import slugify

from homefabric.accounts.permissions import household_view, require
from homefabric.core import audit
from homefabric.manual import render as manual_render
from homefabric.manual.forms import ManualOptionsForm

DEFAULTS = {"history": "recent", "photos": True, "page_per_item": True,
            "include_archived": False}


def _options(request) -> dict:
    """Options from the submitted form; defaults when opened directly."""
    if "history" not in request.GET:
        return dict(DEFAULTS)
    form = ManualOptionsForm(request.GET)
    return form.builder_options() if form.is_valid() else dict(DEFAULTS)


def _filename(request, ext):
    return f"house-manual-{slugify(request.household.name) or 'home'}-{timezone.now():%Y%m%d}.{ext}"


@household_view("view")
def options(request):
    return render(request, "manual/options.html", {"form": ManualOptionsForm(),
                                                   "query": request.GET.urlencode()})


@household_view("view")
def view(request):
    manual = manual_render.build(request.household, actor=request.user, link_mode="app",
                                 **_options(request))
    return HttpResponse(manual_render.render_html(manual, request))


@household_view("view")
def download(request):
    fmt = request.GET.get("format", "html")
    opts = _options(request)
    if fmt == "zip":
        # Bundling every document file is a data export.
        require(request, "export")
        tmp = tempfile.NamedTemporaryFile(prefix="manual-", suffix=".zip",
                                          dir=settings.STAGING_DIR, delete=False)
        tmp.close()
        manual_render.write_zip(request.household, tmp.name, actor=request.user, **opts)
        audit.record(request.user, "household.manual_zip", request.household,
                     household=request.household)
        handle = open(tmp.name, "rb")  # noqa: SIM115 - closed by FileResponse
        try:
            os.unlink(tmp.name)
        except OSError:
            pass  # Windows: removed later by check_integrity --clean
        return FileResponse(handle, as_attachment=True, filename=_filename(request, "zip"),
                            content_type="application/zip")
    manual = manual_render.build(request.household, actor=request.user, link_mode="inline",
                                 **opts)
    response = HttpResponse(manual_render.render_html(manual), content_type="text/html")
    response["Content-Disposition"] = f'attachment; filename="{_filename(request, "html")}"'
    return response
