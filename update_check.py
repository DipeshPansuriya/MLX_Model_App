"""Is a newer MLX Studio published? Asked only when the user presses the button.

One anonymous GET of GitHub's latest release. The reply is untrusted: only a
strictly shaped version number is taken from it, and the page address is built
here. Nothing is downloaded, saved or run.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable

REPO = "DipeshPansuriya/MLX_Model_App"  # as in packaging/release_manifest.py
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
TIMEOUT_SECONDS = 10
MAX_BYTES = 512 * 1024
VERSION_SHAPE = re.compile(r"v?([0-9]{1,5})\.([0-9]{1,5})\.([0-9]{1,5})")
UNUSABLE = "GitHub sent a reply that could not be used."


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """The one address above is the only one asked."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_release(url: str, opener=None) -> bytes:
    """At most ``MAX_BYTES + 1`` bytes of the reply, so an oversized one shows."""
    if opener is None:
        with contextlib.suppress(Exception):  # optional: macOS certificates
            import truststore

            truststore.inject_into_ssl()
        opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "MLX-Studio"},
    )
    with opener.open(request, timeout=TIMEOUT_SECONDS) as reply:
        return reply.read(MAX_BYTES + 1)


def _latest_version(body: bytes) -> tuple[int, ...] | None:
    if len(body) > MAX_BYTES:
        return None
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name")
    match = VERSION_SHAPE.fullmatch(tag) if isinstance(tag, str) else None
    return tuple(map(int, match.groups())) if match else None


def check(current: str, fetch: Callable[[str], bytes] = fetch_release) -> dict:
    """``status`` is ``update``, ``current`` or ``error``; ``message`` is for the screen.

    ``current`` is the app's own version, ``x.y.z``.
    """
    result = {"status": "error", "current": current, "latest": None, "url": None}
    try:
        found = _latest_version(fetch(LATEST_URL))
    except urllib.error.HTTPError as exc:
        why = {
            404: "No published release was found.",
            403: "GitHub is limiting requests. Try again later.",
            429: "GitHub is limiting requests. Try again later.",
        }
        return {**result, "message": why.get(exc.code, "GitHub could not be reached.")}
    except (OSError, http.client.HTTPException):
        return {**result, "message": "GitHub could not be reached."}
    if found is None:
        return {**result, "message": UNUSABLE}
    latest = ".".join(map(str, found))
    result.update(status="current", latest=latest)
    mine = tuple(map(int, current.split(".")))
    if found > mine:
        result.update(status="update", url=f"https://github.com/{REPO}/releases/tag/v{latest}")
        message = f"MLX Studio {latest} is available; you have {current}."
    elif found == mine:
        message = f"MLX Studio {latest} is the latest release."
    else:
        message = f"You have {current}; the latest published release is {latest}."
    return {**result, "message": message}
