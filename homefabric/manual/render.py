"""Turn a built manual into HTML (in-app or standalone) or a ZIP with documents."""

from __future__ import annotations

import zipfile
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string

from homefabric.documents import storage
from homefabric.manual.builder import Manual, ManualBuilder


def _css() -> str:
    return (Path(settings.BASE_DIR) / "static" / "css" / "manual.css").read_text(encoding="utf-8")


def render_html(manual: Manual, request=None) -> str:
    """In-app view links the stylesheet; standalone files inline it for offline use."""
    inline = manual.options["link_mode"] != "app"
    return render_to_string("manual/manual.html",
                            {"m": manual, "inline_css": _css() if inline else ""},
                            request=request)


def build(household, **options) -> Manual:
    return ManualBuilder(household, **options).build()


def write_html(household, output, **options) -> Path:
    manual = build(household, link_mode="inline", **options)
    output = Path(output)
    output.write_bytes(render_html(manual).encode("utf-8"))  # no CRLF translation on Windows
    return output


def write_zip(household, output, **options) -> Path:
    """index.html plus every referenced document, linked with relative paths."""
    manual = build(household, link_mode="zip", **options)
    output = Path(output)
    folder = f"house-manual-{''.join(c if c.isalnum() else '-' for c in household.name)}"
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        zf.writestr(f"{folder}/index.html", render_html(manual))
        for revision, path in manual.bundle:
            src = storage.path_for(revision.blob.storage_key)
            if src.exists():
                zf.write(src, f"{folder}/{path}", compress_type=zipfile.ZIP_STORED)
    return output
