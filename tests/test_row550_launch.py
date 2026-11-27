"""Row 550: the token reaches our own window in the URL fragment, and nowhere else.

``main()`` runs here with the server, the workers and pywebview replaced: no port
is bound, no window or browser tab opens and no real instance is contacted.
"""

from __future__ import annotations

import os
import subprocess
import sys
import types

import pytest

OSASCRIPT = "/usr/bin/osascript"


@pytest.fixture
def launcher(app_module, monkeypatch):
    """main() with every outside effect recorded instead of done."""
    seen = types.SimpleNamespace(
        runs=[], tabs=[], windows=[], opened=[], rc=0, error=None, workspace=True
    )
    real_run = subprocess.run

    def run(argv, *args, **kwargs):
        if argv[0] != OSASCRIPT:
            return real_run(argv, *args, **kwargs)
        seen.runs.append((list(argv), kwargs))
        if seen.error:
            raise seen.error
        return subprocess.CompletedProcess(argv, seen.rc)

    def interrupt(_seconds):
        raise KeyboardInterrupt  # ends the browser mode wait loop

    def open_url(url):
        seen.opened.append(url)
        return seen.workspace

    # Stand-ins: the in-process fallback must never reach the real browser.
    shared = types.SimpleNamespace(openURL_=open_url)
    appkit, foundation = types.ModuleType("AppKit"), types.ModuleType("Foundation")
    appkit.NSWorkspace = types.SimpleNamespace(sharedWorkspace=lambda: shared)
    foundation.NSURL = types.SimpleNamespace(URLWithString_=lambda url: url)
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "Foundation", foundation)
    monkeypatch.setattr(app_module.subprocess, "run", run)
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: seen.tabs.append(url) or True)
    monkeypatch.setattr(app_module.time, "sleep", interrupt)
    monkeypatch.setattr(app_module, "instance_is_running", lambda: False)
    monkeypatch.setattr(app_module, "serve", lambda: types.SimpleNamespace(shutdown=lambda: None))
    for name in ("worker_check_versions", "restore_server_state", "stop_server"):
        monkeypatch.setattr(app_module, name, lambda: None)
    return seen


def stub_webview(monkeypatch, seen, start=lambda *a, **k: None):
    module = types.ModuleType("webview")
    module.create_window = lambda *a, **k: seen.windows.append((a, k)) or object()
    module.start = start
    monkeypatch.setitem(sys.modules, "webview", module)


def no_webview(monkeypatch):
    monkeypatch.setitem(sys.modules, "webview", None)  # makes the import fail


def logs(app_module) -> str:
    return "".join(p.read_text() for p in app_module.LOG_DIR.iterdir() if p.is_file())


def test_launch_url_carries_the_token_in_the_fragment(app_module):
    assert app_module.launch_url() == (
        f"http://127.0.0.1:{app_module.PORT}/#t={app_module.API_TOKEN}"
    )


def test_the_window_opens_the_launch_url_and_nothing_prints_it(
    app_module, launcher, monkeypatch, capsys
):
    stub_webview(monkeypatch, launcher)
    app_module.main()
    [(args, _)] = launcher.windows
    assert args[1] == app_module.launch_url()
    assert app_module.API_TOKEN not in args[0]
    assert app_module.API_TOKEN not in "".join(capsys.readouterr())
    assert app_module.API_TOKEN not in logs(app_module)
    assert launcher.runs == [] and launcher.tabs == []
    assert app_module.STATE.browser_mode is False


def test_the_browser_gets_the_url_on_stdin_never_on_a_command_line(
    app_module, launcher, monkeypatch, capsys
):
    no_webview(monkeypatch)
    app_module.main()
    [(argv, kwargs)] = launcher.runs
    assert argv == [OSASCRIPT]
    assert kwargs["input"] == f'open location "{app_module.launch_url()}"'.encode()
    assert kwargs["timeout"] == 10
    assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
    assert launcher.tabs == []
    assert app_module.STATE.browser_mode is True
    assert app_module.API_TOKEN not in "".join(capsys.readouterr())
    assert app_module.API_TOKEN not in logs(app_module)


FAILURES = [
    (1, None),
    (0, FileNotFoundError(OSASCRIPT)),
    (0, subprocess.TimeoutExpired([OSASCRIPT], 10)),
]


@pytest.mark.parametrize(("rc", "error"), FAILURES)
def test_a_failed_osascript_opens_in_process_never_on_a_command_line(
    app_module, launcher, rc, error
):
    launcher.rc, launcher.error = rc, error
    url = app_module.launch_url()
    assert app_module.open_in_browser(url) is True
    assert launcher.opened == [url]
    assert launcher.tabs == []  # webbrowser would put the token on /usr/bin/open
    assert app_module.API_TOKEN not in logs(app_module)


@pytest.mark.parametrize("missing", [False, True])
def test_no_way_to_open_says_so_and_gives_nothing_away(
    app_module, launcher, monkeypatch, capsys, missing
):
    launcher.rc, launcher.workspace = 1, False
    if missing:
        monkeypatch.setitem(sys.modules, "AppKit", None)
    assert app_module.open_in_browser(app_module.launch_url()) is False
    assert launcher.tabs == []
    said = "".join(capsys.readouterr())
    assert "could not open the browser" in app_module.APP_LOG.read_text()
    assert "from its icon" in said
    assert app_module.API_TOKEN not in said + logs(app_module)


def test_pywebview_debug_logging_cannot_be_switched_on(app_module, launcher, monkeypatch):
    # At debug level pywebview logs the window address, which holds the token.
    monkeypatch.setenv("PYWEBVIEW_LOG", "debug")
    seen = []
    stub_webview(
        monkeypatch, launcher, lambda *a, **k: seen.append(os.environ.get("PYWEBVIEW_LOG"))
    )
    app_module.main()
    assert seen == [None]


@pytest.mark.parametrize(
    "url",
    [
        'http://127.0.0.1:8765/"\ndo shell script "true',
        "http://127.0.0.1:8765/ x",
        "https://evil.example/",
        "file:///etc/passwd",
        "http://127.0.0.1:8765/\\",
    ],
)
def test_a_url_that_could_break_out_of_the_script_is_refused(app_module, launcher, url):
    with pytest.raises(ValueError):
        app_module.open_in_browser(url)
    assert launcher.runs == [] and launcher.tabs == [] and launcher.opened == []


def test_a_window_that_fails_to_start_leaves_browser_mode_able_to_reopen(
    app_module, launcher, monkeypatch, capsys
):
    def start(*_args, **_kwargs):
        # A library error may quote the address it was loading.
        raise RuntimeError(f"cannot load {app_module.launch_url()}")

    stub_webview(monkeypatch, launcher, start)
    reopened = []

    def interrupt(_seconds):
        reopened.append((app_module.STATE.window, app_module.focus_window()))
        raise KeyboardInterrupt

    monkeypatch.setattr(app_module.time, "sleep", interrupt)
    app_module.main()
    assert reopened == [(None, True)]
    assert len(launcher.runs) == 2
    assert app_module.API_TOKEN not in "".join(capsys.readouterr())
    assert app_module.API_TOKEN not in logs(app_module)


class TestFocusInBrowserMode:
    def test_a_second_call_within_three_seconds_opens_no_second_tab(self, app_module, monkeypatch):
        opened, clock = [], [1000.0]
        monkeypatch.setattr(app_module, "open_in_browser", lambda u: opened.append(u) or True)
        monkeypatch.setattr(app_module.time, "monotonic", lambda: clock[0])
        app_module.STATE.browser_mode = True
        assert app_module.focus_window() is True
        assert app_module.focus_window() is False
        clock[0] += 2.9
        assert app_module.focus_window() is False
        clock[0] += 0.2
        assert app_module.focus_window() is True
        assert opened == [app_module.launch_url()] * 2

    def test_it_reports_a_browser_that_did_not_open(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module, "open_in_browser", lambda _url: False)
        app_module.STATE.browser_mode = True
        assert app_module.focus_window() is False

    def test_a_window_is_restored_and_shown_and_no_tab_opens(self, app_module, monkeypatch):
        done, opened = [], []
        monkeypatch.setattr(app_module, "open_in_browser", lambda u: opened.append(u) or True)
        app_module.STATE.window = types.SimpleNamespace(
            restore=lambda: done.append("restore"), show=lambda: done.append("show")
        )
        assert app_module.focus_window() is True
        assert done == ["restore", "show"] and opened == []

    def test_before_any_mode_is_known_nothing_opens(self, app_module, monkeypatch):
        opened = []
        monkeypatch.setattr(app_module, "open_in_browser", lambda u: opened.append(u) or True)
        assert app_module.focus_window() is False
        assert opened == []
