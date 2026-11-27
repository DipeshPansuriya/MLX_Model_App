"""Row 603: a Content-Security-Policy that only lets this server's scripts run.

A missed ``esc()`` in the UI must not be able to run code, so scripts come from
files on this origin only: no inline script, inline handler or eval.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from test_security import good_headers, request

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"

# The <style> text pywebview adds to the page to turn text selection off
# (webview/js/customize.js). Without its hash the app window would change.
PYWEBVIEW_VERSION = "6.2.1"
PYWEBVIEW_CSS = (
    "body {-webkit-user-select: none; -khtml-user-select: none; "
    "-ms-user-select: none; user-select: none; cursor: default;}"
)


def source_hash(text: str) -> str:
    digest = hashlib.sha256(text.encode()).digest()
    return f"'sha256-{base64.b64encode(digest).decode()}'"


EXPECTED = {
    "default-src": ["'self'"],
    "script-src": ["'self'"],
    "style-src": ["'self'", source_hash(PYWEBVIEW_CSS)],
    # The download progress bar sets its width in a style attribute.
    "style-src-attr": ["'unsafe-inline'"],
    "img-src": ["'self'", "data:"],  # the empty data: favicon
    "object-src": ["'none'"],
    "base-uri": ["'none'"],
    "form-action": ["'none'"],
    "frame-ancestors": ["'none'"],
}


def policy(headers) -> dict[str, list[str]]:
    values = headers.get_all("Content-Security-Policy") or []
    assert len(values) == 1, values
    directives = [part.split() for part in values[0].split(";") if part.strip()]
    assert len({d[0] for d in directives}) == len(directives), "a directive is repeated"
    return {d[0]: d[1:] for d in directives}


class TestPolicy:
    @pytest.mark.parametrize("path", ["/", "/index.html"])
    def test_the_page_gets_exactly_this_policy(self, server, path):
        status, headers, _ = request(server, "GET", path)
        assert status == 200
        assert policy(headers) == EXPECTED

    @pytest.mark.parametrize(
        ("method", "path", "body", "status"),
        [
            ("GET", "/app.js", None, 200),
            ("GET", "/style.css", None, 200),
            ("GET", "/missing", None, 404),
            ("GET", "/api/state", None, 403),  # refused: no token
            ("POST", "/api/unload", b"{}", 403),
            ("PUT", "/", None, 501),  # the library's own error page
        ],
    )
    def test_every_other_response_carries_it_too(self, server, method, path, body, status):
        headers = {"Content-Type": "application/json"} if body else {}
        got, sent, _ = request(server, method, path, body, headers)
        assert got == status
        assert policy(sent) == EXPECTED

    def test_a_foreign_host_refusal_carries_it(self, server):
        status, headers, _ = request(server, "GET", "/", host="evil.example")
        assert status == 403
        assert policy(headers) == EXPECTED

    def test_the_chat_stream_carries_it(self, server, app_module, monkeypatch):
        monkeypatch.setattr(app_module.STATE, "model", object())
        monkeypatch.setattr(app_module, "stream_chat", lambda *_: iter([("error", "stop")]))
        body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
        status, headers, _ = request(server, "POST", "/api/chat", body, good_headers(app_module))
        assert status == 200
        assert headers["Content-Type"] == "text/event-stream"
        assert policy(headers) == EXPECTED

    def test_the_style_hash_matches_the_pinned_window_library(self):
        pinned = f"pywebview=={PYWEBVIEW_VERSION}"
        assert pinned in (ROOT / "requirements.txt").read_text().split(), (
            "pywebview changed: re-read webview/js and check the app window under the policy"
        )
        # Where the library is installed, its real stylesheet must match as well.
        spec = importlib.util.find_spec("webview")
        if spec and spec.origin:
            script = (Path(spec.origin).parent / "js" / "customize.js").read_text()
            found = re.search(r"var disableTextCss = '([^']*)'", script)
            assert found
            assert source_hash(found.group(1)) in EXPECTED["style-src"]

    def test_the_window_gives_pywebview_no_functions_to_build_with_eval(self):
        # pywebview builds each exposed Python function with new Function(),
        # which this policy blocks.
        source = (ROOT / "app.py").read_text()
        for call in ("js_api", ".expose(", "evaluate_js", "load_css"):
            assert call not in source, call


class Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict]] = []
        self.script_text = ""
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        self._in_script = tag == "script"

    def handle_endtag(self, tag):
        self._in_script = False

    def handle_data(self, data):
        if self._in_script:
            self.script_text += data.strip()


def page() -> Tags:
    tags = Tags()
    tags.feed((UI / "index.html").read_text())
    return tags


class TestThePageFitsThePolicy:
    def test_scripts_are_files_on_this_origin(self):
        parsed = page()
        scripts = [attrs for tag, attrs in parsed.tags if tag == "script"]
        assert scripts
        assert all(re.match(r"/[^/]", attrs.get("src") or "") for attrs in scripts)
        assert parsed.script_text == ""

    def test_no_inline_handlers_script_urls_or_style_blocks(self):
        for tag, attrs in page().tags:
            assert tag not in {"style", "base", "object", "embed", "iframe", "form"}
            assert not [name for name in attrs if name.startswith("on")], (tag, attrs)
            for name in ("href", "src", "action"):
                url = (attrs.get(name) or "").strip().lower()
                assert not url.startswith(("javascript:", "http:", "https:", "//")), url

    def test_app_js_never_builds_code_or_inline_handlers(self):
        script = (UI / "app.js").read_text()
        for pattern in (
            r"\beval\s*\(",
            r"\bnew\s+Function\b",
            r"set(Timeout|Interval)\s*\(\s*[\"'`]",
            r"<\s*(script|style)\b",
            r"<[a-z][^>`]*\son[a-z]+\s*=",
            r"javascript:",
            r"setAttribute\(\s*[\"'`](on|style)",
        ):
            assert not re.search(pattern, script, re.IGNORECASE), pattern

    def test_the_page_carries_no_token_tag(self):
        # Row 550: the token arrives in the URL fragment, not in a served file.
        metas = [attrs for tag, attrs in page().tags if tag == "meta"]
        assert "mlx-token" not in [attrs.get("name") for attrs in metas]
        assert "__MLX_TOKEN__" not in (UI / "index.html").read_text()
