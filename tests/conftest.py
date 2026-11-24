"""Shared fixtures for the MLX Studio test suite.

``app.py`` resolves ``~/.mlx_studio`` and ``~/.cache/huggingface`` at import
time, so every test gets a throwaway ``HOME`` and a freshly imported module.
That keeps the developer's real logs and model cache untouched and gives each
test a clean ``STATE``.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    """Import app.py with HOME pointed at a temporary directory."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("MLX_STUDIO_UPDATE_URL", raising=False)

    name = f"mlxapp_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, ROOT / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
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
def base_url(server):
    host, port = server.server_address[:2]
    return f"http://{host}:{port}"
