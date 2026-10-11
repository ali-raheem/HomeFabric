"""Upload content validation.

Files are identified by their leading bytes, not by the client-supplied
content type. Executable and active web formats are rejected. Office files
are stored and served as downloads only; macros are never processed, and
macro-enabled formats are rejected.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import PurePath

BLOCKED_EXTENSIONS = {
    "exe", "dll", "com", "bat", "cmd", "ps1", "psm1", "vbs", "vbe", "js", "mjs", "jse", "wsf",
    "wsh", "msi", "msp", "scr", "pif", "cpl", "jar", "sh", "bash", "zsh", "py", "pl", "rb",
    "php", "html", "htm", "xhtml", "shtml", "svg", "svgz", "xml", "xsl", "swf", "hta", "lnk",
    "reg", "app", "dmg", "pkg", "deb", "rpm", "apk", "iso", "docm", "dotm", "xlsm", "xltm",
    "xlam", "pptm", "potm", "ppsm", "ppam", "sldm",
}

OOXML = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
ODF = {
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
}
LEGACY_OFFICE = {"doc": "application/msword", "xls": "application/vnd.ms-excel",
                 "ppt": "application/vnd.ms-powerpoint"}
TEXT_TYPES = {"txt": "text/plain", "csv": "text/csv", "md": "text/markdown", "log": "text/plain"}

INLINE_SAFE = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp"}

ACCEPT_ATTR = (
    ".pdf,.png,.jpg,.jpeg,.gif,.webp,.heic,.heif,.tif,.tiff,.bmp,.txt,.csv,.md,.rtf,"
    ".docx,.xlsx,.pptx,.odt,.ods,.odp,.doc,.xls,.ppt"
)


class RejectedFile(Exception):
    pass


@dataclass
class Detected:
    mime_type: str
    extension: str


def _ext(filename: str) -> str:
    suffix = PurePath(filename).suffix.lower().lstrip(".")
    return suffix


def _looks_like_text(head: bytes) -> bool:
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        # Allow a multi-byte character cut at the end of the sample.
        if exc.start < len(head) - 4:
            try:
                head.decode("cp1252")
            except UnicodeDecodeError:
                return False
    lowered = head[:2048].lstrip().lower()
    if lowered.startswith((b"<!doctype html", b"<html", b"<svg", b"<?xml", b"<script")):
        return False
    return True


def detect(path, filename: str) -> Detected:
    ext = _ext(filename)
    if ext in BLOCKED_EXTENSIONS:
        raise RejectedFile(f"'.{ext}' files are not allowed because they can contain active "
                           "content or executable code.")
    with open(path, "rb") as fh:
        head = fh.read(8192)
    if head.startswith(b"MZ") or head.startswith(b"\x7fELF") or head[:4] in (
        b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
    ) or head.startswith(b"#!"):
        raise RejectedFile("Executable files are not allowed.")
    if head.startswith(b"%PDF-"):
        return Detected("application/pdf", "pdf")
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return Detected("image/png", "png")
    if head.startswith(b"\xff\xd8\xff"):
        return Detected("image/jpeg", "jpg")
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return Detected("image/gif", "gif")
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return Detected("image/webp", "webp")
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return Detected("image/tiff", "tiff")
    if head[:2] == b"BM" and ext == "bmp":
        return Detected("image/bmp", "bmp")
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"heis"):
        return Detected("image/heic", "heic")
    if head.startswith(b"{\\rtf"):
        return Detected("application/rtf", "rtf")
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        if ext in LEGACY_OFFICE:
            return Detected(LEGACY_OFFICE[ext], ext)
        raise RejectedFile("Unrecognised legacy Office file; use .doc, .xls or .ppt.")
    if head.startswith(b"PK\x03\x04"):
        return _detect_zip(path, ext)
    if ext in TEXT_TYPES and _looks_like_text(head):
        return Detected(TEXT_TYPES[ext], ext)
    if not ext and _looks_like_text(head):
        return Detected("text/plain", "txt")
    raise RejectedFile("This file type is not supported. Upload PDF, images, plain text, or "
                       "Office documents.")


def _detect_zip(path, ext: str) -> Detected:
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "mimetype" in names and ext in ODF:
                if zf.read("mimetype").decode("ascii", "replace").strip() == ODF[ext]:
                    if any(n.lower().startswith("basic/") for n in names):
                        raise RejectedFile("Documents containing macros are not allowed.")
                    return Detected(ODF[ext], ext)
            if "[Content_Types].xml" in names and ext in OOXML:
                if any(n.lower().endswith("vbaproject.bin") for n in names):
                    raise RejectedFile("Documents containing macros are not allowed.")
                return Detected(OOXML[ext], ext)
    except zipfile.BadZipFile as exc:
        raise RejectedFile("The file appears to be corrupt.") from exc
    raise RejectedFile("ZIP archives are not supported; upload the documents individually.")
