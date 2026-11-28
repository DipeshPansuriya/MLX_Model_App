"""Row 1091, point 26: the update check. No test reaches the network."""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

import update_check

LATEST = "https://api.github.com/repos/DipeshPansuriya/MLX_Model_App/releases/latest"
PAGE = "https://github.com/DipeshPansuriya/MLX_Model_App/releases/tag/v2.1.0"


def _reply(**fields):
    body = {"tag_name": "v2.1.0", "draft": False, "prerelease": False, **fields}
    return lambda url: json.dumps(body).encode()


def test_a_newer_release_is_offered_with_a_page_built_from_the_version():
    result = update_check.check("2.0.14", fetch=_reply(html_url="https://evil.example/x"))
    assert result == {
        "status": "update",
        "current": "2.0.14",
        "latest": "2.1.0",
        "url": PAGE,
        "message": "MLX Studio 2.1.0 is available; you have 2.0.14.",
    }


@pytest.mark.parametrize(
    ("current", "message"),
    [
        ("2.1.0", "MLX Studio 2.1.0 is the latest release."),
        ("2.1.1", "You have 2.1.1; the latest published release is 2.1.0."),
        ("2.10.0", "You have 2.10.0; the latest published release is 2.1.0."),
    ],
)
def test_the_same_or_an_older_release_is_not_an_update(current, message):
    result = update_check.check(current, fetch=_reply())
    assert (result["status"], result["message"], result["url"]) == ("current", message, None)


def test_only_the_public_latest_release_address_is_asked():
    asked = []

    def fetch(url):
        asked.append(url)
        return _reply()(url)

    update_check.check("2.0.0", fetch=fetch)
    assert asked == [LATEST]
    manifest = Path(__file__).resolve().parent.parent / "packaging" / "release_manifest.py"
    assert f'REPO = "{update_check.REPO}"' in manifest.read_text("utf-8")


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"<html>",
        b"\xff\xfe\xff",
        b"[]",
        b"null",
        b"{}",
        b"[" * 100_000,
        json.dumps({"tag_name": 210}).encode(),
        json.dumps({"tag_name": "v2.1.0<script>"}).encode(),
        json.dumps({"tag_name": "v2.1.0\n"}).encode(),
        json.dumps({"tag_name": "../../v2.1.0"}).encode(),
        json.dumps({"tag_name": "v" + "9" * 50 + ".0.0"}).encode(),
        json.dumps({"tag_name": "v٢.1.0"}).encode(),
        json.dumps({"tag_name": "v2.1.0", "draft": True}).encode(),
        json.dumps({"tag_name": "v2.1.0", "prerelease": True}).encode(),
        b" " * (update_check.MAX_BYTES + 1),
    ],
)
def test_an_unusable_reply_is_an_error_and_none_of_it_is_shown(body):
    result = update_check.check("2.0.0", fetch=lambda url: body)
    assert result == {
        "status": "error",
        "current": "2.0.0",
        "latest": None,
        "url": None,
        "message": "GitHub sent a reply that could not be used.",
    }


def _http(code):
    return urllib.error.HTTPError(LATEST, code, "secret detail", {}, None)


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (_http(404), "No published release was found."),
        (_http(403), "GitHub is limiting requests. Try again later."),
        (_http(429), "GitHub is limiting requests. Try again later."),
        (_http(302), "GitHub could not be reached."),
        (urllib.error.URLError("offline"), "GitHub could not be reached."),
        (TimeoutError(), "GitHub could not be reached."),
    ],
)
def test_network_failures_are_plain_errors(error, message):
    def fetch(url):
        raise error

    result = update_check.check("2.0.0", fetch=fetch)
    assert (result["status"], result["message"]) == ("error", message)


class Opener:
    def __init__(self, body):
        self.body, self.seen = body, []

    def open(self, request, timeout):
        self.seen.append((request, timeout))
        return io.BytesIO(self.body)


def test_the_request_has_no_token_a_timeout_and_a_size_limit():
    opener = Opener(b"x" * (update_check.MAX_BYTES * 2))
    body = update_check.fetch_release(LATEST, opener=opener)
    assert len(body) == update_check.MAX_BYTES + 1
    request, timeout = opener.seen[0]
    assert request.full_url == LATEST
    assert request.get_method() == "GET"
    assert timeout == update_check.TIMEOUT_SECONDS
    assert sorted(name.lower() for name, _ in request.header_items()) == ["accept", "user-agent"]


def test_redirects_are_refused():
    handler = update_check.NoRedirect()
    assert handler.redirect_request(None, None, 302, "", {}, "https://evil.example") is None
