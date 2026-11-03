"""Helpers to turn ComfyUI URLs into local analyzer image paths.

Images are never stored in the DB — only ComfyUI /view URLs. The analyzer
serves the actual PNGs from its read-only mount of comfyui/files.
"""

import re
from urllib.parse import parse_qs, quote, urlparse

_FILENAME_RE = re.compile(r"^[\w\-.]+/[\w\-.]+$")


def comfyui_url_to_path(url: str | None) -> str | None:
    """Convert a ComfyUI /view URL to a 'subfolder/filename' path.

    Returns None for anything that is not a recognizable ComfyUI URL.
    """
    if not url or "://" not in url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    params = parse_qs(parsed.query)
    filename = (params.get("filename") or [None])[0]
    if not filename:
        return None
    subfolder = (params.get("subfolder") or [""])[0]
    path = f"{subfolder}/{filename}" if subfolder else filename
    if not _FILENAME_RE.match(path):
        return None
    return path


def image_endpoint_path(path: str | None) -> str | None:
    """Convert 'subfolder/file.png' to an /api/image?path=... URL."""
    if not path or not _FILENAME_RE.match(path):
        return None
    return f"/api/image?path={quote(path)}"
