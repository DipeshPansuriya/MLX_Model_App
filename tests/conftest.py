"""Shared fixtures for the MLX Studio test suite.

``app.py`` resolves ``~/.mlx_studio`` and ``~/.cache/huggingface`` at import
time, so every test gets a throwaway ``HOME`` and a freshly imported module.
That keeps the developer's real logs and model cache untouched and gives each
test a clean ``STATE``.
"""

from __future__ import annotations

import contextlib
import importlib.util
import subprocess
import sys
import threading
import types
import urllib.request
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def free_memory(monkeypatch):
    """No test reads this Mac's free memory: vm_stat answers what the test sets.

    Set ``.bytes`` (None: it cannot be read); ``.reads`` counts the calls.
    """
    fake = types.SimpleNamespace(bytes=100 * 10**9, reads=0)
    real = subprocess.run

    def run(cmd, *args, **kwargs):
        if not (isinstance(cmd, list) and cmd[:1] == ["/usr/bin/vm_stat"]):
            return real(cmd, *args, **kwargs)
        fake.reads += 1
        if fake.bytes is None:
            raise OSError("vm_stat is not available")
        pages = ("free", "speculative", "purgeable")
        text = "Mach Virtual Memory Statistics: (page size of 1 bytes)\n"
        text += f"File-backed pages: {fake.bytes}.\n"
        text += "".join(f"Pages {name}: 0.\n" for name in pages)
        return subprocess.CompletedProcess(cmd, 0, text, "")

    monkeypatch.setattr(subprocess, "run", run)
    return fake


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    """Import app.py with HOME pointed at a temporary directory."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("MLX_STUDIO_UPDATE_URL", raising=False)
    # The model cache must never resolve into the developer's real home.
    for name in ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "XDG_CACHE_HOME"):
        monkeypatch.delenv(name, raising=False)
    cache = home / ".cache" / "huggingface" / "hub"
    with contextlib.suppress(ImportError):
        from huggingface_hub import constants

        monkeypatch.setattr(constants, "HF_HUB_CACHE", str(cache))

    name = f"mlxapp_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, ROOT / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        assert cache == module.CACHE_DIR
        yield module
    finally:
        sys.modules.pop(name, None)


@pytest.fixture
def server(app_module):
    """Run the real request handler on an ephemeral loopback port."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app_module.Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.fixture
def base_url(server, app_module, monkeypatch):
    """Server address; urllib requests to it carry the launch token like the UI does."""
    host, port = server.server_address[:2]
    original = urllib.request.Request

    def with_token(url, *args, **kwargs):
        request = original(url, *args, **kwargs)
        request.add_header("X-MLX-Token", app_module.API_TOKEN)
        return request

    monkeypatch.setattr(urllib.request, "Request", with_token)
    return f"http://{host}:{port}"
