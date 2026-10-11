"""Rendering of untrusted user text as sanitised Markdown (raw HTML disabled)."""

from django.utils.safestring import mark_safe
from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
_md.disable(["image"])

_SAFE_SCHEMES = ("http://", "https://", "mailto:")


def _validate_link(url: str) -> bool:
    lowered = url.strip().lower()
    return lowered.startswith(_SAFE_SCHEMES) or lowered.startswith(("/", "#"))


_md.validateLink = _validate_link


def render_markdown(text: str) -> str:
    if not text:
        return ""
    html = _md.render(text)
    html = html.replace("<a href=", '<a rel="nofollow noopener noreferrer" href=')
    return mark_safe(html)  # noqa: S308 - html=False escapes all raw HTML
