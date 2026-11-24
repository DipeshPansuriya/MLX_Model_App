#!/usr/bin/env python3
"""
MLX Studio — a modern local-AI desktop app for Apple Silicon.

Architecture
------------
* A stdlib ThreadingHTTPServer exposes a small JSON + SSE API.
* A single-page web UI (ui/) is served from the same origin.
* pywebview hosts the UI in a native macOS window (falls back to the browser).

No Flask/FastAPI dependency: only stdlib + mlx + huggingface_hub.
"""

from __future__ import annotations

import json
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_NAME = "MLX Studio"
APP_VERSION = "2.0.0"
APP_UPDATE_URL = os.environ.get("MLX_STUDIO_UPDATE_URL", "").strip()

HF_ORG = "mlx-community"
CACHE_DIR = Path.home() / ".cache" / "huggingface" / "hub"
APP_DIR = Path.home() / ".mlx_studio"
LOG_FILE = APP_DIR / "activity.log"
UI_DIR = Path(__file__).resolve().parent / "ui"

HOST = "127.0.0.1"
PORT = 8765
SERVER_PORT = 8080

APP_DIR.mkdir(parents=True, exist_ok=True)
# The activity log records prompts, so keep it owner-only.
try:
    APP_DIR.chmod(0o700)
except OSError:
    pass


# --------------------------------------------------------------------------- #
# Activity log
# --------------------------------------------------------------------------- #
def log_activity(action: str, model: str, status: str, detail: str) -> None:
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "model": model,
        "status": status,
        "detail": detail,
    }
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        try:
            LOG_FILE.chmod(0o600)
        except OSError:
            pass
    except OSError as exc:
        print(f"log write failed: {exc}")


def read_activity(limit: int = 2000) -> list[dict]:
    if not LOG_FILE.exists():
        return []
    out: list[dict] = []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    out.append({"timestamp": "", "action": "raw", "model": "", "status": "info", "detail": line})
    except OSError:
        return []
    return out[-limit:]


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def run_cmd(cmd: list[str], timeout: int | None = None) -> tuple[bool, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = proc.stdout or ""
        if proc.stderr:
            out += ("\n" if out else "") + proc.stderr
        return proc.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "Command timed out."
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def pkg_version(name: str) -> str | None:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:  # noqa: BLE001
        return None


def latest_pypi_version(package: str) -> str | None:
    try:
        try:
            import truststore

            truststore.inject_into_ssl()
        except Exception:  # noqa: BLE001
            pass
        with urllib.request.urlopen(f"https://pypi.org/pypi/{package}/json", timeout=12) as resp:
            return json.loads(resp.read().decode())["info"]["version"]
    except Exception:  # noqa: BLE001
        return None


def vt(value: str | None) -> tuple:
    if not value:
        return ()
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.\-+]", value) if p)


def is_newer(candidate: str | None, current: str | None) -> bool:
    if not candidate:
        return False
    if not current:
        return True
    return vt(candidate) > vt(current)


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{int(num)} B" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def hardware() -> dict:
    chip = platform.machine()
    ok, out = run_cmd(["sysctl", "-n", "machdep.cpu.brand_string"])
    if ok and out.strip():
        chip = out.strip()
    ok, out = run_cmd(["sysctl", "-n", "hw.memsize"])
    ram = 0
    if ok and out.strip().isdigit():
        ram = int(out.strip())
    return {"chip": chip, "ram_bytes": ram, "ram": f"{ram / 1024**3:.0f} GB" if ram else ""}


def installed_models() -> list[dict]:
    models: list[dict] = []
    if not CACHE_DIR.exists():
        return models
    for entry in sorted(CACHE_DIR.iterdir()):
        if not entry.name.startswith("models--"):
            continue
        parts = entry.name.split("--")
        if len(parts) < 3:
            continue
        org = parts[1]
        name = "--".join(parts[2:])
        # Snapshot files are symlinks into ../blobs; follow them and de-duplicate
        # by inode so shared blobs are not counted twice.
        seen: set[tuple[int, int]] = set()
        size = 0
        for f in entry.rglob("*"):
            try:
                st = f.stat()  # follows symlinks
            except OSError:
                continue
            if not f.is_file():
                continue
            key = (st.st_dev, st.st_ino)
            if key in seen:
                continue
            seen.add(key)
            size += st.st_size
        models.append(
            {
                "repo": f"{org}/{name}",
                "path": str(entry),
                "size": size,
                "size_h": human_size(size),
            }
        )
    models.sort(key=lambda m: m["repo"])
    return models


def search_hf(query: str) -> list[dict]:
    try:
        from huggingface_hub import HfApi

        api = HfApi()
        found = api.list_models(author=HF_ORG, search=query or None, limit=80)
        return [{"repo": m.modelId, "downloads": getattr(m, "downloads", 0) or 0} for m in found]
    except Exception as exc:  # noqa: BLE001
        print(f"search failed: {exc}")
        return []


# --------------------------------------------------------------------------- #
# Application state
# --------------------------------------------------------------------------- #
class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.model = None
        self.tokenizer = None
        self.loaded_repo: str | None = None
        self.loading_repo: str | None = None
        self.load_error: str | None = None
        self.generating = False
        self.stop_flag = False
        self.server_proc: subprocess.Popen | None = None
        self.server_repo: str | None = None
        self.server_lines: list[str] = []
        self.downloads: dict[str, dict] = {}
        self.install: dict = {"running": False, "kind": "", "lines": []}
        self.versions: dict = {"checked_at": None, "mlx_latest": None, "mlx_lm_latest": None}
        self.app_release: dict = {}
        self.setup_lines: list[str] = []

    # -- models ---------------------------------------------------------- #
    def unload(self) -> str | None:
        repo = self.loaded_repo
        self.model = None
        self.tokenizer = None
        self.loaded_repo = None
        self.load_error = None
        try:
            import mlx.core as mx

            mx.clear_cache()
        except Exception:  # noqa: BLE001
            pass
        return repo

    def snapshot(self) -> dict:
        hw = hardware()
        models = installed_models()
        mlx_v = pkg_version("mlx")
        mlx_lm_v = pkg_version("mlx-lm")
        hf_v = pkg_version("huggingface-hub")
        return {
            "app": {
                "name": APP_NAME,
                "version": APP_VERSION,
                "latest": self.versions.get("app_latest"),
                "channel": self.versions.get("app_channel"),
                "update": is_newer(self.versions.get("app_latest"), APP_VERSION),
                "release": self.app_release,
            },
            "hardware": hw,
            "python": platform.python_version(),
            "versions": {
                "mlx": mlx_v,
                "mlx_lm": mlx_lm_v,
                "huggingface_hub": hf_v,
                "mlx_latest": self.versions.get("mlx_latest"),
                "mlx_lm_latest": self.versions.get("mlx_lm_latest"),
                "checked_at": self.versions.get("checked_at"),
                "mlx_update": is_newer(self.versions.get("mlx_latest"), mlx_v),
                "mlx_lm_update": is_newer(self.versions.get("mlx_lm_latest"), mlx_lm_v),
                "mlx_missing": not mlx_v,
                "mlx_lm_missing": not mlx_lm_v,
            },
            "models": models,
            "loaded": {
                "repo": self.loaded_repo,
                "loading": self.loading_repo,
                "error": self.load_error,
            },
            "server": {
                "running": self.server_proc is not None,
                "repo": self.server_repo,
                "port": SERVER_PORT,
                "endpoint": f"http://{HOST}:{SERVER_PORT}/v1",
                "lines": self.server_lines[-200:],
            },
            "downloads": list(self.downloads.values()),
            "install": {
                "running": self.install["running"],
                "kind": self.install["kind"],
                "lines": self.install["lines"][-400:],
            },
            "generating": self.generating,
        }


STATE = State()


# --------------------------------------------------------------------------- #
# Chat streaming
# --------------------------------------------------------------------------- #
def stream_chat(messages: list[dict]):
    """Yield ('token', text) / ('done', meta) / ('error', msg) tuples."""
    with STATE.lock:
        model = STATE.model
        tokenizer = STATE.tokenizer
        repo = STATE.loaded_repo
    if model is None or tokenizer is None:
        yield ("error", "No model is loaded. Load a model first.")
        return

    try:
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler

        prompt = tokenizer.apply_chat_template(messages, add_generation_prompt=True)
        sampler = make_sampler(temp=0.7, top_p=0.95)
        last = None
        started = time.time()
        for resp in stream_generate(model, tokenizer, prompt, max_tokens=2048, sampler=sampler):
            if STATE.stop_flag:
                break
            last = resp
            yield ("token", resp.text)
        elapsed = max(time.time() - started, 1e-6)
        meta = {
            "tokens": getattr(last, "generation_tokens", 0) if last else 0,
            "tps": round(getattr(last, "generation_tps", 0.0) if last else 0.0, 1),
            "peak_gb": round(getattr(last, "peak_memory", 0.0) if last else 0.0, 2),
            "prompt_tokens": getattr(last, "prompt_tokens", 0) if last else 0,
            "elapsed": round(elapsed, 2),
            "stopped": STATE.stop_flag,
            "repo": repo,
        }
        yield ("done", meta)
    except Exception as exc:  # noqa: BLE001
        yield ("error", str(exc))


# --------------------------------------------------------------------------- #
# Background workers
# --------------------------------------------------------------------------- #
def worker_load(repo: str) -> None:
    with STATE.lock:
        STATE.loading_repo = repo
        STATE.load_error = None
    log_activity("model", repo, "running", "load started")
    try:
        from mlx_lm import load

        model, tokenizer = load(repo)
        with STATE.lock:
            STATE.model = model
            STATE.tokenizer = tokenizer
            STATE.loaded_repo = repo
            STATE.loading_repo = None
        log_activity("model", repo, "success", "loaded into memory")
    except Exception as exc:  # noqa: BLE001
        with STATE.lock:
            STATE.loading_repo = None
            STATE.load_error = str(exc)[:500]
        log_activity("model", repo, "failed", str(exc)[:300])


def worker_download(repo: str) -> None:
    STATE.downloads[repo] = {"repo": repo, "status": "running", "detail": "starting", "pct": 0}
    log_activity("download", repo, "running", "download started")
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(repo_id=repo, cache_dir=str(Path.home() / ".cache" / "huggingface"))
        STATE.downloads[repo] = {"repo": repo, "status": "success", "detail": "complete", "pct": 100}
        log_activity("download", repo, "success", "download complete")
    except Exception as exc:  # noqa: BLE001
        STATE.downloads[repo] = {"repo": repo, "status": "failed", "detail": str(exc)[:200], "pct": 0}
        log_activity("download", repo, "failed", str(exc)[:300])


def worker_install(kind: str) -> None:
    STATE.install = {"running": True, "kind": kind, "lines": []}
    log_activity("install", "mlx + mlx-lm", "running", f"{kind} started")

    cmd = [sys.executable, "-m", "pip", "install"]
    if kind == "reinstall":
        cmd += ["--force-reinstall"]
    else:
        cmd += ["--upgrade"]
    cmd += ["mlx", "mlx-lm"]

    STATE.install["lines"].append("$ " + " ".join(cmd))
    ok = False
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        assert proc.stdout is not None
        for line in proc.stdout:
            STATE.install["lines"].append(line.rstrip())
        ok = proc.wait() == 0
    except Exception as exc:  # noqa: BLE001
        STATE.install["lines"].append(f"error: {exc}")

    log_activity("install", "mlx + mlx-lm", "success" if ok else "failed", f"{kind} finished")
    STATE.install["running"] = False
    worker_check_versions()


def worker_check_versions() -> None:
    mlx_latest = latest_pypi_version("mlx")
    mlx_lm_latest = latest_pypi_version("mlx-lm")
    STATE.versions = {
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mlx_latest": mlx_latest,
        "mlx_lm_latest": mlx_lm_latest,
        "app_latest": latest_app_version(),
        "app_channel": APP_UPDATE_URL or "local build",
    }
    log_activity(
        "update",
        "mlx + mlx-lm",
        "info",
        f"latest mlx={mlx_latest or '?'} mlx-lm={mlx_lm_latest or '?'}",
    )


def latest_app_version() -> str | None:
    """Look up the newest MLX Studio release from a manifest, if configured.

    Set MLX_STUDIO_UPDATE_URL to a JSON file shaped like
    {"version": "2.1.0", "url": "https://…/MLXStudio-2.1.0.dmg"} to enable it.
    Without a channel the app reports itself as a local build.
    """
    if not APP_UPDATE_URL:
        return None
    try:
        try:
            import truststore

            truststore.inject_into_ssl()
        except Exception:  # noqa: BLE001
            pass
        with urllib.request.urlopen(APP_UPDATE_URL, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        STATE.app_release = {
            "version": data.get("version"),
            "url": data.get("url"),
            "notes": data.get("notes") or "",
            "sha256": data.get("sha256"),
        }
        return data.get("version")
    except Exception:  # noqa: BLE001
        return None


def worker_server_start(repo: str) -> None:
    stop_server()
    STATE.server_lines = []
    cmd = [
        sys.executable,
        "-m",
        "mlx_lm",
        "server",
        "--model",
        repo,
        "--host",
        HOST,
        "--port",
        str(SERVER_PORT),
    ]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
    except Exception as exc:  # noqa: BLE001
        STATE.server_lines.append(f"failed to start: {exc}")
        return

    STATE.server_proc = proc
    STATE.server_repo = repo
    log_activity("server", repo, "running", f"listening on {HOST}:{SERVER_PORT}")

    assert proc.stdout is not None
    for line in proc.stdout:
        STATE.server_lines.append(line.rstrip())
        STATE.server_lines = STATE.server_lines[-400:]
    STATE.server_proc = None
    STATE.server_repo = None
    log_activity("server", repo, "info", "server stopped")


def stop_server() -> None:
    proc = STATE.server_proc
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    STATE.server_proc = None
    STATE.server_repo = None


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/{APP_VERSION}"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter console
        pass

    def handle_one_request(self) -> None:
        """Treat client disconnects as normal, not as server errors."""
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    # -- plumbing -------------------------------------------------------- #
    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"))

    MAX_BODY = 8 * 1024 * 1024  # 8 MiB: chat history is the largest legitimate body

    def _body(self) -> dict:
        raw = self.headers.get("Content-Length") or "0"
        try:
            length = int(raw)
        except (TypeError, ValueError):
            return {}
        if length <= 0 or length > self.MAX_BODY:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    # -- static ---------------------------------------------------------- #
    def _static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        root = UI_DIR.resolve()
        target = (root / rel).resolve()
        # is_relative_to compares path components, so a sibling such as
        # "ui-evil/" cannot pass by sharing the "ui" string prefix.
        if not target.is_relative_to(root) or not target.is_file():
            self._send(404, b"not found", "text/plain")
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(target.suffix, "application/octet-stream")
        self._send(200, target.read_bytes(), ctype)

    # -- GET ------------------------------------------------------------- #
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/api/state":
            self._json(STATE.snapshot())
            return
        if path == "/api/models":
            self._json({"models": installed_models()})
            return
        if path == "/api/search":
            q = (query.get("q") or [""])[0]
            self._json({"results": search_hf(q)})
            return
        if path == "/api/logs":
            self._json({"entries": read_activity()})
            return
        if path == "/api/versions":
            worker_check_versions()
            self._json(STATE.snapshot()["versions"])
            return
        if path == "/api/ping":
            self._json({"ok": True, "app": APP_VERSION})
            return
        self._static(path)

    # -- POST ------------------------------------------------------------ #
    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        body = self._body()

        if path == "/api/load":
            repo = body.get("repo") or ""
            if not repo:
                self._json({"error": "repo required"}, 400)
                return
            with STATE.lock:
                if STATE.loaded_repo == repo:
                    self._json({"ok": True, "already": True})
                    return
                if STATE.loading_repo:
                    self._json({"error": "another model is loading"}, 409)
                    return
            threading.Thread(target=worker_load, args=(repo,), daemon=True).start()
            self._json({"ok": True, "loading": repo})
            return

        if path == "/api/unload":
            repo = STATE.unload()
            if repo:
                log_activity("model", repo, "info", "unloaded from memory")
            self._json({"ok": True})
            return

        if path == "/api/chat":
            messages = body.get("messages") or []
            if not messages:
                self._json({"error": "messages required"}, 400)
                return
            self._stream_chat(messages)
            return

        if path == "/api/chat/stop":
            STATE.stop_flag = True
            self._json({"ok": True})
            return

        if path == "/api/download":
            repo = body.get("repo") or ""
            if not repo:
                self._json({"error": "repo required"}, 400)
                return
            if repo in STATE.downloads and STATE.downloads[repo]["status"] == "running":
                self._json({"error": "already downloading"}, 409)
                return
            threading.Thread(target=worker_download, args=(repo,), daemon=True).start()
            self._json({"ok": True})
            return

        if path == "/api/delete":
            repo = body.get("repo") or ""
            if STATE.loaded_repo == repo:
                self._json({"error": "unload the model first"}, 409)
                return
            match = next((m for m in installed_models() if m["repo"] == repo), None)
            if not match:
                self._json({"error": "not installed"}, 404)
                return
            shutil.rmtree(match["path"], ignore_errors=True)
            log_activity("delete", repo, "success", "removed from disk")
            self._json({"ok": True})
            return

        if path == "/api/server/start":
            repo = body.get("repo") or ""
            if not repo:
                self._json({"error": "repo required"}, 400)
                return
            threading.Thread(target=worker_server_start, args=(repo,), daemon=True).start()
            self._json({"ok": True})
            return

        if path == "/api/server/stop":
            # worker_server_start logs the stop once its output pump exits.
            stop_server()
            self._json({"ok": True})
            return

        if path == "/api/install":
            kind = body.get("kind") or "install"
            if STATE.install["running"]:
                self._json({"error": "install already running"}, 409)
                return
            threading.Thread(target=worker_install, args=(kind,), daemon=True).start()
            self._json({"ok": True})
            return

        if path == "/api/logs/clear":
            try:
                LOG_FILE.unlink(missing_ok=True)
            except OSError as exc:
                self._json({"error": str(exc)}, 500)
                return
            self._json({"ok": True})
            return

        self._json({"error": "unknown endpoint"}, 404)

    # -- SSE chat -------------------------------------------------------- #
    def _stream_chat(self, messages: list[dict]) -> None:
        with STATE.lock:
            if STATE.model is None:
                self._json({"error": "no model loaded"}, 409)
                return
            STATE.generating = True
            STATE.stop_flag = False

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def emit(event: str, data) -> None:
            payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
            self.wfile.write(payload.encode("utf-8"))
            self.wfile.flush()

        text_parts: list[str] = []
        repo = STATE.loaded_repo or "?"
        try:
            for kind, payload in stream_chat(messages):
                if kind == "token":
                    text_parts.append(payload)
                    emit("token", payload)
                elif kind == "done":
                    emit("done", payload)
                    log_activity(
                        "chat",
                        repo,
                        "success",
                        f"{payload.get('tokens', 0)} tokens @ {payload.get('tps', 0)} tok/s",
                    )
                else:
                    emit("error", payload)
                    log_activity("chat", repo, "failed", str(payload)[:300])
        except (BrokenPipeError, ConnectionResetError):
            log_activity("chat", repo, "failed", "client disconnected")
        finally:
            STATE.generating = False
            STATE.stop_flag = False
            try:
                self.close_connection = True
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def serve() -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main() -> None:
    if not UI_DIR.exists():
        print(f"UI directory missing: {UI_DIR}")
        sys.exit(1)

    httpd = serve()
    url = f"http://{HOST}:{PORT}/"
    print(f"{APP_NAME} {APP_VERSION} → {url}")

    threading.Thread(target=worker_check_versions, daemon=True).start()
    log_activity("app", "-", "info", f"{APP_NAME} {APP_VERSION} launched")

    try:
        import webview

        window = webview.create_window(
            f"{APP_NAME} {APP_VERSION}",
            url,
            width=1360,
            height=900,
            min_size=(1080, 720),
            background_color="#0f1115",
        )
        webview.start()
    except Exception as exc:  # noqa: BLE001
        print(f"native window unavailable ({exc}); opening browser")
        import webbrowser

        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        stop_server()
        httpd.shutdown()


if __name__ == "__main__":
    main()
