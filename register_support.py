"""Content detection and desktop clipboard adapters for register."""

import os
import shutil
import subprocess
import tempfile


class SupportError(ValueError):
    pass


_IMAGE_TYPES = (
    "image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp",
    "image/tiff", "image/svg+xml", "application/pdf",
)
_TEXT_TYPES = ("text/plain;charset=utf-8", "text/plain;charset=UTF-8", "text/plain")


def _looks_utf8(data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return not any(ord(c) < 32 and c not in "\t\n\r\f\b" for c in text)


def _signature(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"II*\0", b"MM\0*")):
        return "image/tiff"
    if (len(data) >= 26 and data.startswith(b"BM") and data[6:10] == b"\0\0\0\0"
            and int.from_bytes(data[10:14], "little") >= 26):
        return "image/bmp"
    if b"%PDF-" in data[:1024]:
        return "application/pdf"
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return "application/zip"
    if data.startswith(b"\x1f\x8b"):
        return "application/gzip"
    if data.startswith(b"\x7fELF"):
        return "application/x-executable"
    return None


def detect_content(data):
    """Infer a useful MIME type and advisory text flag without changing bytes."""
    if not data:
        return True, "text/plain;charset=utf-8"
    mime = _signature(data)
    if mime is None and shutil.which("file"):
        try:
            result = subprocess.run(
                ["file", "--brief", "--mime-type", "-"], input=data,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
                check=False,
            )
            candidate = result.stdout.decode("ascii", "replace").strip()
            if result.returncode == 0 and _valid_mime(candidate):
                mime = candidate
        except (OSError, subprocess.TimeoutExpired):
            pass
    is_text = _looks_utf8(data)
    if mime and not (mime.startswith("text/") or mime in (
        "application/json", "application/xml", "application/javascript",
        "application/x-sh", "application/x-shellscript", "image/svg+xml",
    )):
        is_text = False
    if mime is None:
        mime = "text/plain;charset=utf-8" if is_text else "application/octet-stream"
    elif mime == "text/plain" and is_text:
        mime = "text/plain;charset=utf-8"
    return is_text, mime


def _valid_mime(value):
    return "/" in value and not any(c.isspace() or ord(c) < 32 for c in value)


def _backend():
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland":
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    raise SupportError("no desktop clipboard is available in this session")


def _call(command, *, data=None):
    try:
        result = subprocess.run(
            command, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=15, check=False,
        )
    except FileNotFoundError:
        raise SupportError("clipboard support is not installed; run ./install.sh --dependencies") from None
    except subprocess.TimeoutExpired:
        raise SupportError("the desktop clipboard did not respond") from None
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip().replace("\n", "; ")
        raise SupportError("clipboard operation failed" + (": " + message if message else ""))
    return result.stdout


def _publish(command, data):
    # Clipboard helpers keep a background owner alive. Do not give that owner
    # output pipes: waiting for their EOF would wait until the next clipboard.
    with tempfile.TemporaryFile() as errors:
        try:
            result = subprocess.run(
                command, input=data, stdout=subprocess.DEVNULL, stderr=errors,
                timeout=15, check=False,
            )
        except FileNotFoundError:
            raise SupportError("clipboard support is not installed; run ./install.sh --dependencies") from None
        except subprocess.TimeoutExpired:
            raise SupportError("the desktop clipboard did not respond") from None
        if result.returncode != 0:
            errors.seek(0)
            message = errors.read().decode("utf-8", "replace").strip().replace("\n", "; ")
            raise SupportError("clipboard operation failed" + (": " + message if message else ""))


def _choose_type(types, *, x11=False):
    """Prefer an image representation, then plain text, then another MIME type."""
    for mime in _IMAGE_TYPES:
        if mime in types:
            return mime, mime
    if x11 and "UTF8_STRING" in types:
        return "UTF8_STRING", "text/plain;charset=utf-8"
    for mime in _TEXT_TYPES:
        if mime in types:
            return mime, mime
    if x11 and "STRING" in types:
        return "STRING", "text/plain;charset=iso-8859-1"
    for mime in types:
        if mime.startswith("image/") and _valid_mime(mime):
            return mime, mime
    for mime in types:
        if _valid_mime(mime):
            return mime, mime
    raise SupportError("the clipboard has no supported contents")


def read_clipboard():
    """Return exact clipboard bytes and their advertised MIME type."""
    if _backend() == "wayland":
        offered = _call(["wl-paste", "--list-types"])
        types = offered.decode("utf-8", "replace").splitlines()
        target, mime = _choose_type(types)
        data = _call(["wl-paste", "--no-newline", "--type", target])
    else:
        base = ["xclip", "-selection", "clipboard", "-out", "-target"]
        offered = _call(base + ["TARGETS"])
        types = offered.decode("utf-8", "replace").splitlines()
        target, mime = _choose_type(types, x11=True)
        data = _call(base + [target])
    return data, mime


def write_clipboard(data, mime):
    """Publish a buffer using its stored MIME type, including image formats."""
    if not _valid_mime(mime):
        raise SupportError("invalid clipboard content type")
    if _backend() == "wayland":
        command = ["wl-copy", "--type", mime]
    else:
        target = "UTF8_STRING" if mime.lower() in (
            "text/plain;charset=utf-8", "text/plain;charset=utf8",
        ) else mime
        command = ["xclip", "-selection", "clipboard", "-in", "-target", target]
    _publish(command, data)
