"""Masking of credentials and sensitive URLs in UI text and logs."""

import re

_URL_WITH_CREDS = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<creds>[^/\s@]+)@")
_URL = re.compile(r"\b(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*)://(?P<rest>[^\s'\"<>]+)")
_KEY_VALUE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|secret|apikey|api_key|key|auth)=([^&\s'\"]+)"
)


def mask_secret_url(url: str) -> str:
    """Show only scheme and a short hint, e.g. ``tgram://…a1b2``."""
    if not url:
        return ""
    m = _URL.match(url.strip())
    if not m:
        return "••••" + url[-4:] if len(url) > 8 else "••••"
    rest = m.group("rest")
    hint = rest[-4:] if len(rest) > 12 else ""
    return f"{m.group('scheme')}://••••{hint}"


def redact(text: str, secrets: list[str] | tuple[str, ...] = ()) -> str:
    if not text:
        return text
    out = str(text)
    for secret in secrets:
        if secret and len(secret) >= 4:
            out = out.replace(secret, "[redacted]")
    out = _URL_WITH_CREDS.sub(lambda m: f"{m.group('scheme')}[redacted]@", out)
    out = _KEY_VALUE.sub(lambda m: f"{m.group(1)}=[redacted]", out)
    # Apprise-style URLs carry secrets in the path; keep only the scheme.
    out = _URL.sub(
        lambda m: m.group(0)
        if m.group("scheme").lower() in {"http", "https"} and "@" not in m.group(0)
        else f"{m.group('scheme')}://[redacted]",
        out,
    )
    return out
