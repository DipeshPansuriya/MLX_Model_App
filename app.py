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

import contextlib
import fnmatch
import functools
import http.client
import json
import os
import platform
import queue
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import engine_runtime
import engines
import model_info

APP_NAME = "MLX Studio"
APP_VERSION = "2.0.12"
APP_UPDATE_URL = os.environ.get("MLX_STUDIO_UPDATE_URL", "").strip()

HF_ORG = "mlx-community"


def _hf_cache_dir() -> Path:
    """Where huggingface_hub, and so mlx_lm, keeps models (HF_HOME, HF_HUB_CACHE)."""
    try:
        from huggingface_hub import constants

        return Path(constants.HF_HUB_CACHE)
    except (ImportError, AttributeError):
        # Not installed yet (first run): follow the library's own lookup order.
        home = os.environ.get("HF_HOME") or os.path.join(
            os.environ.get("XDG_CACHE_HOME") or "~/.cache", "huggingface"
        )
        hub = (
            os.environ.get("HF_HUB_CACHE")
            or os.environ.get("HUGGINGFACE_HUB_CACHE")
            or os.path.join(home, "hub")
        )
        return Path(os.path.expandvars(os.path.expanduser(hub)))


CACHE_DIR = _hf_cache_dir()
APP_DIR = Path.home() / ".mlx_studio"
LOG_DIR = APP_DIR / "logs"
ACTIVITY_LOG = LOG_DIR / "activity.log"
APP_LOG = LOG_DIR / "app.log"
SERVER_LOG = LOG_DIR / "server.log"
UI_DIR = Path(__file__).resolve().parent / "ui"
# The API server starts through this wrapper, which checks every request.
# The command line of a server an older build started behind its own wrapper script:
# such a server is still recognised, so it can be adopted and stopped.
OLD_WRAPPER = "/mlx_guard.py"
LOADTEST_SCRIPT = Path(__file__).resolve().parent / "mlx_loadtest.py"
# The .app keeps this next to app.py, in Contents/Resources.
APP_ICON = Path(__file__).resolve().parent / "AppIcon.icns"

# Rotation keeps the folder small enough to attach to a GitHub issue.
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUPS = 3
LOG_LOCK = threading.Lock()

HOST = "127.0.0.1"
PORT = 8765
SERVER_PORT = 8080
# Chat asks the running API server with these settings.
CHAT_SETTINGS = {"max_tokens": 2048, "temperature": 0.7, "top_p": 0.95}
CHAT_CONNECT_SECONDS = 5
CHAT_BEAT_SECONDS = 1.0  # how often a silent wait is reported to the browser
CHAT_LINE_BYTES = 1024 * 1024
CHAT_ERROR_BYTES = 64 * 1024
CHAT_CUT_SHORT = "The API server stopped before the reply finished. Check the API Server page."
# mlx_lm cuts replies at 512 tokens when a client sends no max_tokens.
SERVER_MAX_TOKENS = 8192
# Limits for reading server.last.json: far above two real model ids.
LAST_MODELS_MAX_BYTES = 4096
MODEL_NAME_MAX = 200
# The limits the user may set in settings.json: (lowest, highest). A key that is
# absent keeps the built-in rule. Ports, hosts, the token, Origin/CSP checks and
# paths are deliberately not here.
SETTINGS = {
    "max_context_tokens": (4096, 100_000_000),  # OptiQ only: mlx_lm has no such flag
    "server_max_reply_tokens": (64, 131072),
    "chat_max_reply_tokens": (64, 131072),
    "prompt_cache_size": (1, 100),
    "prompt_cache_bytes": (100_000_000, 100_000_000_000),
    "chat_temperature": (0, 2),
    "chat_top_p": (0.05, 1),
    # (first, last) of CHOICE_SETTINGS; the read step is model_info.PREFILL_RANGE.
    "performance_mode": ("standard", "advanced"),
    "kv_bits": (4, 8),
    "kv_group_size": (32, 128),
    "prefill_step_size": (64, 2048),
}
# These take one of the listed values only (see model_info.KV_BITS, KV_GROUPS).
CHOICE_SETTINGS = {
    "performance_mode": ("standard", "save_memory", "advanced"),
    "kv_bits": (4, 8),
    "kv_group_size": (32, 64, 128),
}
# mlx_lm's own flags. Advanced passes the set ones; they are never merged by "lower".
PERFORMANCE_FLAGS = ("kv_bits", "kv_group_size", "prefill_step_size")
PERFORMANCE_SETTINGS = ("performance_mode", *PERFORMANCE_FLAGS)
# What "Save memory" passes: the pair measured on one model and one Mac.
SAVE_MEMORY_FLAGS = {"kv_bits": 8, "prefill_step_size": 256}
# These take decimals; every other key takes whole numbers.
DECIMAL_SETTINGS = ("chat_temperature", "chat_top_p")
# Sent with each Chat request, so a change needs no server restart.
CHAT_ONLY_SETTINGS = ("chat_max_reply_tokens", *DECIMAL_SETTINGS)
# The "load_test" block of settings.json. The offered sizes are only test sizes, none
# of them a chosen limit.
LOAD_TEST_SIZES = tuple(4096 << step for step in range(9))  # 4096 to 1,048,576
LOAD_TEST_SIZE_RANGE = (4096, 100_000_000)
LOAD_TEST_MAX_SIZES = 12  # every offered size and a few typed ones
LOAD_TEST_NUMBERS = {  # key: (default, lowest, highest)
    "reply_tokens": (256, 32, 1024),
    "rounds": (2, 1, 3),
    "timeout_seconds": (900, 60, 3600),
}
LOAD_TEST_LISTS = {"sizes": LOAD_TEST_SIZES, "last_sizes": ()}
LOADTEST_RESULT_BYTES = 256 * 1024
TEMPLATE_BYTES = 8 * 1024 * 1024
LOADTEST_RUNS_KEPT = 5  # for each model
LOADTEST_CANCEL_SECONDS = 10  # then the runner's process group is killed
LOADTEST_LINE_BYTES = 64 * 1024
LOADTEST_LOG_LINES = 50
LOADTEST_WAIT_SECONDS = 40  # longer than the runner waits for its server
# The keys a model may override under "models".
MODEL_SETTINGS = tuple(SETTINGS)
SETTINGS_MAX_BYTES = 64 * 1024
SETTINGS_LOCK = threading.Lock()
# How long a server may take to report ready. After it the app stops the server, and
# a server that this one replaced is started again.
START_WATCH_SECONDS = 600
# Versions verified for this release (mirrors requirements.txt).
PINNED_PACKAGES = {"mlx": "0.32.4", "mlx-lm": "0.32.2", "huggingface-hub": "2.4.0"}

# New on every launch. Handed to our own window in the URL fragment, never served,
# so an /api request carrying it came from our own UI.
API_TOKEN = secrets.token_urlsafe(32)
TOKEN_HEADER = "X-MLX-Token"  # noqa: S105 - a header name, not a secret
# A second launch is a different process and cannot know the token. Raising the
# window changes no data, and the Host, Origin and JSON checks still apply.
TOKEN_EXEMPT = frozenset({"/api/focus"})
# It asks this first, to learn that we are here. The reply is only the version.
OPEN_GETS = frozenset({"/api/ping"})
BAD_TOKEN = "invalid session token; open MLX Studio again from its icon"  # noqa: S105
# Browser mode: a second launch opens a new tab, no more often than this.
REOPEN_SECONDS = 3.0
REOPEN_LOCK = threading.Lock()

# Scripts run only from files this server sends: no inline script, handler or
# eval, so a missed escape in the UI cannot run code. The style hash is the
# <style> pywebview 6.2.1 adds to turn text selection off; style attributes are
# for the download progress bar; data: is the empty favicon.
CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'sha256-RoIvEyQwR8x7XAq0wvZTfMiswRYjOD0c8DenZVf2Kss='",
        "style-src-attr 'unsafe-inline'",
        "img-src 'self' data:",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    )
)

APP_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _build_static_allowlist(root: Path) -> dict[str, Path]:
    """Map relative posix path -> absolute file, for files under root only."""
    allowed: dict[str, Path] = {}
    if not root.is_dir():
        return allowed
    resolved_root = root.resolve()
    for entry in resolved_root.rglob("*"):
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in {".html", ".css", ".js", ".svg", ".png", ".ico"}:
            continue
        allowed[entry.relative_to(resolved_root).as_posix()] = entry
    return allowed


STATIC_FILES = _build_static_allowlist(UI_DIR)
# Error messages and server output in the logs can quote chat text, so keep the
# whole tree owner-only.
for _private in (APP_DIR, LOG_DIR):
    with contextlib.suppress(OSError):
        _private.chmod(0o700)


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
def _rotate(path: Path) -> None:
    """Keep path, path.1 … path.N, dropping the oldest. Best effort."""
    try:
        if not path.exists() or path.stat().st_size < LOG_MAX_BYTES:
            return
    except OSError:
        return
    with contextlib.suppress(OSError):
        for index in range(LOG_BACKUPS - 1, 0, -1):
            older = path.with_name(f"{path.name}.{index}")
            if older.exists():
                older.replace(path.with_name(f"{path.name}.{index + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))


def _migrate_legacy_logs() -> None:
    """Move 2.0.0-era logs from ~/.mlx_studio/ into ~/.mlx_studio/logs/."""
    for legacy in (APP_DIR / "activity.log", APP_DIR / "launch.log"):
        if not legacy.is_file():
            continue
        target = LOG_DIR / legacy.name
        with contextlib.suppress(OSError):
            if not target.exists():
                legacy.replace(target)
                target.chmod(0o600)
            else:
                legacy.unlink(missing_ok=True)


def _append(path: Path, line: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with LOG_LOCK:
            _rotate(path)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line if line.endswith("\n") else line + "\n")
            with contextlib.suppress(OSError):
                path.chmod(0o600)
    except OSError as exc:
        print(f"log write failed ({path.name}): {exc}")


def log_app(level: str, message: str, **fields) -> None:
    """Structured application log: lifecycle, HTTP errors, worker failures."""
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level": level,
        "message": message,
    }
    if fields:
        entry.update(fields)
    _append(APP_LOG, json.dumps(entry, ensure_ascii=False, default=str))


def log_activity(action: str, model: str, status: str, detail: str) -> None:
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "model": model,
        "status": status,
        "detail": detail,
    }
    _append(ACTIVITY_LOG, json.dumps(entry, ensure_ascii=False))


def _read_jsonl(path: Path, limit: int) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    out.append(
                        {
                            "timestamp": "",
                            "action": "raw",
                            "model": "",
                            "status": "info",
                            "detail": line,
                        }
                    )
    except OSError:
        return []
    return out[-limit:]


def read_activity(limit: int = 2000) -> list[dict]:
    return _read_jsonl(ACTIVITY_LOG, limit)


def read_app_log(limit: int = 500) -> list[dict]:
    return _read_jsonl(APP_LOG, limit)


def log_files() -> list[dict]:
    """Every file in the log folder, newest first, with size and mtime."""
    out: list[dict] = []
    if not LOG_DIR.is_dir():
        return out
    for path in sorted(LOG_DIR.iterdir()):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        out.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "size_h": human_size(stat.st_size),
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "path": str(path),
            }
        )
    out.sort(key=lambda f: f["modified"], reverse=True)
    return out


def environment_report() -> dict:
    """Facts a maintainer needs to triage a report, with no prompts included."""
    hw = hardware()
    return {
        "app": {"name": APP_NAME, "version": APP_VERSION},
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "chip": hw["chip"],
        "ram": hw["ram"],
        "packages": {
            "mlx": pkg_version("mlx"),
            "mlx-lm": pkg_version("mlx-lm"),
            "huggingface-hub": pkg_version("huggingface-hub"),
            "pywebview": pkg_version("pywebview"),
        },
        "paths": {
            "app_dir": str(APP_DIR),
            "log_dir": str(LOG_DIR),
            "cache_dir": str(CACHE_DIR),
            "ui_dir": str(UI_DIR),
        },
        "ports": {"app": PORT, "server": SERVER_PORT},
        "env": {
            "MLX_STUDIO_UPDATE_URL": APP_UPDATE_URL or "",
            "HF_TOKEN": "set" if os.environ.get("HF_TOKEN") else "",
        },
        "models": [m["repo"] for m in installed_models()],
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def build_diagnostic_bundle() -> tuple[bytes, str]:
    """Zip the log folder plus an environment report, ready to attach to an issue.

    Chat text is not logged on purpose, but error messages and the background
    server's output (server.log) can quote it. The user saves the bundle; it is
    never uploaded anywhere automatically.
    """
    import io

    buffer = io.BytesIO()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"mlx-studio-diagnostics-{stamp}.zip"
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "environment.json", json.dumps(environment_report(), indent=2, ensure_ascii=False)
        )
        if LOG_DIR.is_dir():
            for path in sorted(LOG_DIR.iterdir()):
                if path.is_file():
                    with contextlib.suppress(OSError):
                        text = path.read_bytes().decode("utf-8", "surrogateescape")
                        text = engine_runtime.redact(text)
                        archive.writestr(
                            f"logs/{path.name}", text.encode("utf-8", "surrogateescape")
                        )
    return buffer.getvalue(), name


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def run_cmd(cmd: list[str], timeout: int | None = None) -> tuple[bool, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        out = proc.stdout or ""
        if proc.stderr:
            out += ("\n" if out else "") + proc.stderr
        return proc.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "Command timed out."
    except Exception as exc:
        return False, str(exc)


def pkg_version(name: str) -> str | None:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return None


def latest_pypi_version(package: str) -> str | None:
    try:
        try:
            import truststore

            truststore.inject_into_ssl()
        except Exception as exc:
            print(f"truststore unavailable, using default CA bundle: {exc}")
        with urllib.request.urlopen(f"https://pypi.org/pypi/{package}/json", timeout=12) as resp:
            return json.loads(resp.read().decode())["info"]["version"]
    except Exception:
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


@functools.lru_cache(maxsize=1)
def _probe_hardware() -> dict:
    chip = platform.machine()
    ok, out = run_cmd(["sysctl", "-n", "machdep.cpu.brand_string"])
    if ok and out.strip():
        chip = out.strip()
    ok, out = run_cmd(["sysctl", "-n", "hw.memsize"])
    ram = 0
    if ok and out.strip().isdigit():
        ram = int(out.strip())
    return {"chip": chip, "ram_bytes": ram, "ram": f"{ram / 1024**3:.0f} GB" if ram else ""}


def hardware() -> dict:
    """Chip and memory, probed once: they cannot change while the app runs."""
    return dict(_probe_hardware())


# Written into a model folder while this app downloads it; lists the expected files.
DOWNLOAD_MARKER = ".mlx_studio_download.json"
WEIGHT_SUFFIXES = (".safetensors", ".npz", ".gguf", ".bin")
# The files mlx_lm reads; a model without the others (README and so on) still runs.
RUNTIME_PATTERNS = (
    "*.json",
    "model*.safetensors",
    "*.txt",
    "*.jinja",
    "*.tiktoken",
    "tokenizer.model",
)
# Re-walk even an unchanged-looking model after this long (a growing partial file
# changes no directory timestamp).
MODEL_SCAN_TTL = 30.0
_model_cache: dict[str, tuple[tuple, float, dict]] = {}
_model_cache_lock = threading.Lock()


def invalidate_models() -> None:
    with _model_cache_lock:
        _model_cache.clear()


def _model_stamp(entry: Path) -> tuple:
    """Directory timestamps that move when files are added, removed or renamed."""
    paths = [entry, entry / "blobs", entry / "refs", entry / "snapshots"]
    with contextlib.suppress(OSError):
        paths += sorted((entry / "snapshots").iterdir())
    stamp = []
    for path in paths:
        with contextlib.suppress(OSError):
            stamp.append((path.name, path.stat().st_mtime_ns))
    return tuple(stamp)


def _snapshot_complete(snapshot: Path) -> bool:
    """Weights present, including every shard that an index or file name announces."""
    names = {p.relative_to(snapshot).as_posix() for p in snapshot.rglob("*") if p.is_file()}
    if not any(name.endswith(WEIGHT_SUFFIXES) for name in names):
        return False
    for name in names:
        if name.endswith(".safetensors.index.json"):
            try:
                shards = set(
                    json.loads((snapshot / name).read_text("utf-8"))["weight_map"].values()
                )
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                return False
            if not shards <= names:
                return False
        shard = re.fullmatch(r"(.*)-\d{5}-of-(\d{5})(\.\w+)", name)
        if shard:
            stem, total, suffix = shard.groups()
            if any(
                f"{stem}-{i:05d}-of-{total}{suffix}" not in names for i in range(1, int(total) + 1)
            ):
                return False
    return True


def _hub_reports_incomplete(repo: str) -> bool:
    """Ask the hub library, offline, whether its cached file listing has gaps."""
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(
            repo,
            cache_dir=str(CACHE_DIR),
            local_files_only=True,
            allow_patterns=list(RUNTIME_PATTERNS),
        )
    except Exception as exc:
        # Anything else (not installed, no refs) says nothing about completeness.
        return type(exc).__name__ == "IncompleteSnapshotError"
    return False


def _model_complete(entry: Path, repo: str) -> bool:
    snapshots = entry / "snapshots"
    try:
        commit = (entry / "refs" / "main").read_text("utf-8").strip()
    except OSError:
        commit = ""
    marker = entry / DOWNLOAD_MARKER
    if marker.is_file():
        try:
            wanted = json.loads(marker.read_text("utf-8"))
            revision = wanted.get("revision") or commit
            files = wanted["files"]
            needed = [n for n in files if any(fnmatch.fnmatch(n, p) for p in RUNTIME_PATTERNS)]
            if not any(n.endswith(WEIGHT_SUFFIXES) for n in needed):
                needed = files  # a layout these patterns do not describe
            if all((snapshots / revision / name).is_file() for name in needed):
                return True
            if not commit or commit == revision:
                return False
            # Another tool has since fetched a newer revision: judge that one.
            newer = snapshots / commit
            return (
                ("config.json" not in files or (newer / "config.json").is_file())
                and newer.is_dir()
                and _snapshot_complete(newer)
                and not _hub_reports_incomplete(repo)
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return False
    if any((entry / "blobs").glob("*.incomplete")):
        return False
    if commit and (snapshots / commit).is_dir():
        candidates = [snapshots / commit]
    else:
        candidates = [p for p in snapshots.glob("*") if p.is_dir()]
    return any(map(_snapshot_complete, candidates)) and not _hub_reports_incomplete(repo)


def _scan_model(entry: Path) -> dict:
    parts = entry.name.split("--")
    repo = f"{parts[1]}/{'--'.join(parts[2:])}"
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
    return {
        "repo": repo,
        "path": str(entry),
        "size": size,
        "size_h": human_size(size),
        "complete": _model_complete(entry, repo),
    }


def installed_models() -> list[dict]:
    """Every model folder in the cache; ``complete`` is False for partial downloads."""
    if not CACHE_DIR.exists():
        return []
    models: list[dict] = []
    for entry in sorted(CACHE_DIR.iterdir()):
        if not entry.name.startswith("models--") or len(entry.name.split("--")) < 3:
            continue
        stamp = _model_stamp(entry)
        with _model_cache_lock:
            cached = _model_cache.get(str(entry))
        if cached and cached[0] == stamp and time.monotonic() - cached[1] < MODEL_SCAN_TTL:
            model = cached[2]
        else:
            model = _scan_model(entry)
            with _model_cache_lock:
                _model_cache[str(entry)] = (stamp, time.monotonic(), model)
        models.append(dict(model))
    models.sort(key=lambda m: m["repo"])
    return models


def _complete_models() -> set[str]:
    """Installed models whose files are all there."""
    return {m["repo"] for m in installed_models() if m["complete"]}


# Bytes per stored value. In a quantised MLX model "U32" holds the packed weights
# and the Hub counts the parameters inside them, not the 32-bit words.
DTYPE_BYTES = {"F64": 8, "I64": 8, "F32": 4, "I32": 4, "BF16": 2, "F16": 2, "I16": 2}
DTYPE_BYTES |= {"I8": 1, "U8": 1, "BOOL": 1}
# MLX keeps a 16-bit scale and bias for each group of 64 quantised weights.
QUANT_OVERHEAD_BITS = 0.5


def _param_label(total: int) -> str:
    if total >= 1_000_000_000:
        return f"{total / 1e9:.1f}".removesuffix(".0") + "B"
    return f"{round(total / 1e6)}M"


def _estimated_size_gb(counts: dict, tags: list[str]) -> float | None:
    """Weight size from the parameter counts; None when it cannot be worked out."""
    bits = next((int(t[:-4]) for t in tags if re.fullmatch(r"\d{1,2}-bit", t)), None)
    total = 0.0
    for dtype, count in counts.items():
        if dtype == "U32" and bits:
            total += count * (bits + QUANT_OVERHEAD_BITS) / 8
        elif dtype in DTYPE_BYTES:
            total += count * DTYPE_BYTES[dtype]
        else:
            return None
    return round(total / 1e9, 2) or None


def _search_card(model, ram: int) -> dict:
    """One search hit with the fields of a catalogue card; None means unknown."""
    repo = model.id
    task = getattr(model, "pipeline_tag", None)
    card = {
        "repo": repo,
        "downloads": getattr(model, "downloads", 0) or 0,
        "params": None,
        "size_gb": None,
        "size_estimated": False,
        "category": None,
        "use": f"Task: {task.replace('-', ' ')}" if task else None,
    }
    known = next((m for m in MODEL_CATALOG if m["repo"] == repo), None)
    if known:
        card.update({k: known[k] for k in ("params", "size_gb", "category", "use")})
    else:
        info = getattr(model, "safetensors", None)
        counts = getattr(info, "parameters", None)
        if counts:
            card["params"] = _param_label(sum(counts.values()))
            card["size_gb"] = _estimated_size_gb(counts, getattr(model, "tags", None) or [])
            card["size_estimated"] = card["size_gb"] is not None
    size = card["size_gb"]
    card["fit"] = "unknown" if size is None else fit_rating(size, ram)
    return card


def search_hf(query: str) -> list[dict]:
    try:
        from huggingface_hub import HfApi

        api = HfApi()
        wanted = {"author": HF_ORG, "search": query or None, "limit": 80}
        try:
            found = list(
                api.list_models(
                    **wanted, expand=["downloads", "safetensors", "tags", "pipeline_tag"]
                )
            )
        except TypeError:  # an older huggingface_hub without "expand"
            found = list(api.list_models(**wanted))
        ram = hardware()["ram_bytes"]
        return [_search_card(m, ram) for m in found]
    except Exception as exc:
        print(f"search failed: {exc}")
        return []


# --------------------------------------------------------------------------- #
# Curated catalogue
# --------------------------------------------------------------------------- #
# Sizes are the on-disk 4-bit weight totals measured from the Hugging Face API,
# not parameter counts, because that is what has to fit in unified memory.
MODEL_CATALOG: list[dict] = [
    {
        "repo": "mlx-community/Qwen3-0.6B-4bit",
        "size_gb": 0.35,
        "params": "0.6B",
        "category": "Fast",
        "use": "Instant replies, autocomplete, smoke tests",
    },
    {
        "repo": "mlx-community/Qwen2.5-0.5B-Instruct-4bit",
        "size_gb": 0.29,
        "params": "0.5B",
        "category": "Fast",
        "use": "Smallest useful chat model",
    },
    {
        "repo": "mlx-community/gemma-3-1b-it-4bit",
        "size_gb": 0.77,
        "params": "1B",
        "category": "Fast",
        "use": "Short answers, low memory",
    },
    {
        "repo": "mlx-community/Llama-3.2-1B-Instruct-4bit",
        "size_gb": 0.71,
        "params": "1B",
        "category": "Fast",
        "use": "Cheap drafting and rewriting",
    },
    {
        "repo": "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
        "size_gb": 0.88,
        "params": "1.5B",
        "category": "Fast",
        "use": "Everyday chat without the wait",
    },
    {
        "repo": "mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit",
        "size_gb": 0.88,
        "params": "1.5B",
        "category": "Code",
        "use": "Inline code completion",
    },
    {
        "repo": "mlx-community/Qwen3-1.7B-4bit",
        "size_gb": 0.98,
        "params": "1.7B",
        "category": "Fast",
        "use": "Reasoning at small size",
    },
    {
        "repo": "mlx-community/gemma-2-2b-it-4bit",
        "size_gb": 1.49,
        "params": "2B",
        "category": "Balanced",
        "use": "Polite, compact assistant",
    },
    {
        "repo": "mlx-community/Qwen2.5-3B-Instruct-4bit",
        "size_gb": 1.75,
        "params": "3B",
        "category": "Balanced",
        "use": "Best quality per gigabyte",
    },
    {
        "repo": "mlx-community/Llama-3.2-3B-Instruct-4bit",
        "size_gb": 1.82,
        "params": "3B",
        "category": "Balanced",
        "use": "Strong English writing",
    },
    {
        "repo": "mlx-community/Phi-3.5-mini-instruct-4bit",
        "size_gb": 2.15,
        "params": "3.8B",
        "category": "Balanced",
        "use": "Reasoning and maths",
    },
    {
        "repo": "mlx-community/Qwen3-4B-4bit",
        "size_gb": 2.28,
        "params": "4B",
        "category": "Balanced",
        "use": "Modern general assistant",
    },
    {
        "repo": "mlx-community/gemma-3-4b-it-4bit",
        "size_gb": 3.44,
        "params": "4B",
        "category": "Balanced",
        "use": "Multilingual, long context",
    },
    {
        "repo": "mlx-community/Mistral-7B-Instruct-v0.3-4bit",
        "size_gb": 4.08,
        "params": "7B",
        "category": "Quality",
        "use": "Reliable instruction following",
    },
    {
        "repo": "mlx-community/Qwen2.5-7B-Instruct-1M-4bit",
        "size_gb": 4.3,
        "params": "7B",
        "category": "Quality",
        "use": "Long documents, 1M context",
        "tools": True,
    },
    {
        "repo": "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit",
        "size_gb": 4.3,
        "params": "7B",
        "category": "Code",
        "use": "Code generation and refactors",
        "tools": False,
    },
    {
        "repo": "mlx-community/DeepSeek-R1-Distill-Qwen-7B-4bit",
        "size_gb": 4.3,
        "params": "7B",
        "category": "Reasoning",
        "use": "Step-by-step reasoning",
        "tools": False,
    },
    {
        "repo": "mlx-community/Meta-Llama-3.1-8B-Instruct-4bit",
        "size_gb": 4.53,
        "params": "8B",
        "category": "Quality",
        "use": "Well-rounded default",
    },
    {
        "repo": "mlx-community/Qwen3-8B-4bit",
        "size_gb": 4.62,
        "params": "8B",
        "category": "Quality",
        "use": "Best all-round 8B",
    },
    {
        "repo": "mlx-community/gemma-2-9b-it-4bit",
        "size_gb": 5.22,
        "params": "9B",
        "category": "Quality",
        "use": "Careful, factual answers",
    },
    {
        "repo": "mlx-community/gemma-3-12b-it-4bit",
        "size_gb": 8.07,
        "params": "12B",
        "category": "Heavy",
        "use": "Needs a 24 GB machine or more",
    },
    {
        "repo": "mlx-community/Qwen3-14B-4bit",
        "size_gb": 8.32,
        "params": "14B",
        "category": "Heavy",
        "use": "Needs a 24 GB machine or more",
    },
    {
        "repo": "mlx-community/Qwen3-30B-A3B-4bit",
        "size_gb": 17.19,
        "params": "30B MoE",
        "category": "Heavy",
        "use": "MoE: fast tokens, large footprint",
    },
    {
        "repo": "mlx-community/Qwen3-32B-4bit",
        "size_gb": 18.45,
        "params": "32B",
        "category": "Heavy",
        "use": "Best quality that still fits 32 GB",
    },
    {
        "repo": "mlx-community/Llama-3.3-70B-Instruct-4bit",
        "size_gb": 39.71,
        "params": "70B",
        "category": "Heavy",
        "use": "Needs 64 GB or more",
    },
]

# Fit is the model's 4-bit weight size as a share of physical unified memory.
# The bands are chosen so a 32 GB machine reads: up to 8 GB great (fast, plenty
# of headroom), up to 14 GB good, up to 22 GB tight (close to the limit), and
# anything larger will not fit alongside macOS.
FIT_GREAT = 0.25
FIT_GOOD = 0.44
FIT_TIGHT = 0.70


def tool_support() -> dict[str, bool]:
    """Catalogue models whose tool calling was checked. Missing means unknown."""
    return {m["repo"]: m["tools"] for m in MODEL_CATALOG if "tools" in m}


def fit_rating(size_gb: float, ram_bytes: int) -> str:
    """Rate a model against installed memory: great / good / tight / too-large."""
    if ram_bytes <= 0:
        return "unknown"
    share = size_gb / (ram_bytes / 1024**3)
    if share <= FIT_GREAT:
        return "great"
    if share <= FIT_GOOD:
        return "good"
    if share <= FIT_TIGHT:
        return "tight"
    return "too-large"


def model_catalog() -> list[dict]:
    """The curated list annotated with a fit rating for this machine."""
    ram = hardware()["ram_bytes"]
    installed = {m["repo"] for m in installed_models() if m["complete"]}
    out = []
    for entry in MODEL_CATALOG:
        item = dict(entry)
        item["fit"] = fit_rating(entry["size_gb"], ram)
        item["installed"] = entry["repo"] in installed
        out.append(item)
    order = {"great": 0, "good": 1, "tight": 2, "too-large": 3, "unknown": 4}
    out.sort(key=lambda m: (order.get(m["fit"], 9), m["size_gb"]))
    return out


# --------------------------------------------------------------------------- #
# Application state
# --------------------------------------------------------------------------- #
class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.generating = False
        self.stop_event = threading.Event()  # replaced for every reply
        self.deleting: set[str] = set()
        self.download_cancels: dict[str, threading.Event] = {}
        self.server_lock = threading.RLock()  # one stop or start at a time
        self.server_pending: list[str] = []  # requested, not yet running
        self.server_stops = 0  # a queued start older than the last Stop is dropped
        self.server_proc: subprocess.Popen | None = None
        self.loadtest: dict | None = None  # the running or last load test
        self.loadtest_complaints: set[str] = set()
        self.server_repo: str | None = None
        self.server_repos: list[str] = []  # the first is server_repo
        self.server_started_with: str | None = None  # the settings of the last launch
        self.settings_complaint = ""  # the last settings.json problem logged
        self.free_read: tuple[float, int | None] | None = None  # see free_memory
        self.server_lines: list[str] = []
        self.server_background = False
        self.server_started_at: str | None = None
        self.server_last_error: str | None = None
        self.server_down_notice: str | None = None
        self.server_apply_notice: str | None = None  # a failed Apply, old set back
        # The last good (models, background), until its replacement is ready.
        self.server_fallback: tuple[list[str], bool] | None = None
        self.server_pid: int | None = None
        self.server_identity: str | None = None
        self.server_engine = "mlx"
        self.server_port = SERVER_PORT  # of a direct engine's server; MLX: SERVER_PORT
        self.server_snapshot: str | None = None  # the folder a direct engine serves
        self.server_verdict: str | None = None  # a direct engine: "ready" or "failed"
        self.server_anthropic = False  # a direct engine's /v1/messages route is on
        self.server_stopping = False
        self.window = None
        self.browser_mode = False  # no native window; the UI is a browser tab
        self.reopened_at: float | None = None
        self.downloads: dict[str, dict] = {}
        self.install: dict = {"running": False, "kind": "", "lines": []}
        self.versions: dict = {"checked_at": None, "mlx_latest": None, "mlx_lm_latest": None}
        self.app_release: dict = {}
        self.setup_lines: list[str] = []

    # -- models ---------------------------------------------------------- #
    # -- API server ------------------------------------------------------- #
    def cancel_pending_starts(self) -> None:
        with self.lock:
            self.server_stops += 1
            self.server_fallback = None

    # -- load test -------------------------------------------------------- #
    def testing(self, repo: str | None = None) -> bool:
        """Whether a load test is running (of ``repo``, when one is named)."""
        run = self.loadtest
        return bool(run and run["active"] and repo in (None, run["repo"]))

    # -- packages --------------------------------------------------------- #
    def reserve_install(self, kind: str) -> bool:
        """Claim the single pip slot; False while an install is running."""
        with self.lock:
            if self.install["running"] or self.testing():
                return False
            self.install = {"running": True, "kind": kind, "lines": []}
            return True

    # -- downloads -------------------------------------------------------- #
    def reserve_download(self, repo: str) -> threading.Event | None:
        """Claim the download slot for repo; None when it is already taken."""
        with self.lock:
            busy = repo in self.deleting or self.testing(repo)
            if busy or self.downloads.get(repo, {}).get("status") == "running":
                return None
            self.downloads[repo] = {
                "repo": repo,
                "status": "running",
                "detail": "starting",
                "pct": 0,
            }
            self.download_cancels[repo] = threading.Event()
            return self.download_cancels[repo]

    def in_use_locked(self, repo: str) -> str | None:
        """Why repo cannot be deleted now, or None. Caller holds the lock."""
        if self.testing(repo):
            return LOADTEST_BUSY
        serving = self.server_proc is not None or _pid_alive(self.server_pid)
        if repo in self.server_pending or (
            serving and repo in {self.server_repo, *self.server_repos}
        ):
            return "stop the API server first"
        if self.downloads.get(repo, {}).get("status") == "running":
            return "the model is downloading"
        if repo in self.deleting:
            return "the model is already being deleted"
        return None

    def snapshot(self) -> dict:
        hw = hardware()
        models = installed_models()
        mlx_v = pkg_version("mlx")
        mlx_lm_v = pkg_version("mlx-lm")
        hf_v = pkg_version("huggingface-hub")
        with self.lock:
            downloads = [dict(d) for d in self.downloads.values()]
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
                # An update means the app can apply one: its pinned version is newer.
                "mlx_target": PINNED_PACKAGES["mlx"],
                "mlx_lm_target": PINNED_PACKAGES["mlx-lm"],
                "mlx_update": bool(mlx_v) and is_newer(PINNED_PACKAGES["mlx"], mlx_v),
                "mlx_lm_update": bool(mlx_lm_v) and is_newer(PINNED_PACKAGES["mlx-lm"], mlx_lm_v),
                "huggingface_hub_target": PINNED_PACKAGES["huggingface-hub"],
                "huggingface_hub_update": bool(hf_v)
                and is_newer(PINNED_PACKAGES["huggingface-hub"], hf_v),
                "mlx_missing": not mlx_v,
                "mlx_lm_missing": not mlx_lm_v,
            },
            "models": models,
            "server": {
                "running": self.server_proc is not None or _pid_alive(self.server_pid),
                "repo": self.server_repo,
                "repos": list(self.server_repos),
                "performance": _performance_view(list(self.server_repos)),
                "settings": settings_view(),
                "model_settings": _read_settings()[1],
                "settings_pending": _settings_pending(
                    self.server_proc is not None or _pid_alive(self.server_pid)
                ),
                "port": _server_port(),
                "endpoint": f"http://{HOST}:{_server_port()}/v1",
                "engine": self.server_engine,
                "anthropic": self.server_anthropic,
                "engines": [engine.view(APP_DIR) for engine in engines.REGISTRY],
                "last_engine": _last_engine(),
                "last_anthropic": _last_anthropic(),
                "tool_support": tool_support(),
                "lines": self.server_lines[-200:],
                "background": self.server_background,
                "started_at": self.server_started_at,
                "last_error": self.server_last_error,
                "last_models": _last_models(),
                "down_notice": self.server_down_notice,
                "apply_notice": self.server_apply_notice,
                "starting": bool(self.server_pending),
                **_server_readiness(),
                "pid": self.server_pid,
            },
            "downloads": downloads,
            "loadtest": loadtest_summary(),
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
def _chat_refusal(stream: http.client.HTTPResponse) -> str:
    """The API server's own words for a refused request."""
    try:
        error = json.loads(stream.read(CHAT_ERROR_BYTES))["error"]
        if isinstance(error, dict):
            error = error["message"]
        said = str(error)[:500]
    except Exception:  # not the JSON mlx_lm sends: the status line has to do
        said = f"{stream.status} {stream.reason}"
    return f"The API server refused the request: {said}"


def _chat_target(repo: str, messages: list[dict], settings: dict) -> tuple[dict, int]:
    """What Chat asks the running server, and on which port."""
    engine = _running_engine()
    body = {
        "model": engine.request_model,
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        "stream": True,
        "stream_options": {"include_usage": True},
        **settings,
    }
    return body, _server_port()


def _chat_headers() -> dict:
    """The key of a direct engine, only while every listener on its port is our server."""
    engine = _running_engine()
    if not engine.direct:
        return {}
    key = engine.read_key(_key_file())
    if not key or not engine_runtime.listener_is(_server_port(), STATE.server_pid):
        raise engine_runtime.Refused(
            "The key was not sent: another program may listen on the server's port. "
            "Press Stop, then Start Server."
        )
    return {"Authorization": f"Bearer {key}"}


def _read_reply(conn: http.client.HTTPConnection, body: bytes, put, headers=None) -> None:
    """Send the request and ``put`` what comes back; runs in its own thread."""
    usage, finish = {}, None
    try:
        headers = {"Content-Type": "application/json", **(headers or {})}
        conn.request("POST", "/v1/chat/completions", body, headers)
        response = conn.getresponse()
        if response.status != 200:
            put(("error", _chat_refusal(response)))
            return
        while line := response.readline(CHAT_LINE_BYTES):
            line = line.decode("utf-8").strip()
            if line.startswith(": keepalive "):
                done, _, total = line.removeprefix(": keepalive ").partition("/")
                with contextlib.suppress(ValueError):  # no figures: only the progress is lost
                    put(("status", {"state": "prompt", "done": int(done), "total": int(total)}))
            if not line.startswith("data: "):
                continue
            if line == "data: [DONE]":
                put(("end", {"usage": usage, "finish": finish}))
                return
            data = json.loads(line.removeprefix("data: "))
            usage = data.get("usage") or usage
            for choice in data.get("choices") or []:
                finish = choice.get("finish_reason") or finish
                delta = choice.get("delta") or {}
                for kind, key in (
                    ("reasoning", "reasoning"),
                    ("reasoning", "reasoning_content"),
                    ("token", "content"),
                ):
                    if isinstance(delta.get(key), str) and delta[key]:
                        put((kind, delta[key]))
        put(("error", CHAT_CUT_SHORT))
    except (OSError, http.client.HTTPException):
        put(("error", CHAT_CUT_SHORT))
    except Exception as exc:  # a reply that is not what mlx_lm sends
        put(("error", f"The API server sent a reply that could not be read: {exc}"[:500]))


def _reply_stats(usage: dict, asked: float, first, ended: float, mode: str | None) -> dict:
    """The figures under a reply: the server's counts and this app's clock.

    None where a figure is unknown: nothing is estimated.
    """
    counts = {k: usage.get(f"{k}_tokens") for k in ("prompt", "completion")}
    prompt, reply = (v if type(v) is int and v >= 0 else None for v in counts.values())
    writing = ended - first if first else 0
    return {
        "first_s": round(first - asked, 2) if first else None,
        # One token, or no time between the first and last, is not a speed.
        "tps": round(reply / writing, 1) if reply and reply > 1 and writing >= 0.05 else None,
        "prompt_tokens": prompt,
        "reply_tokens": reply,
        "mode": mode,
    }


def stream_chat(repo: str, messages: list[dict], stop: threading.Event):
    """Ask the running API server, yielding ``(event, data)`` for the browser.

    ``status`` is repeated while nothing arrives, so the screen can say it is waiting
    and a browser that has gone away is noticed. Closing this generator closes the
    request to the server.
    """
    asked_body, port = _chat_target(repo, messages, chat_settings(repo))
    body = json.dumps(asked_body).encode("utf-8")
    conn = http.client.HTTPConnection(HOST, port, timeout=CHAT_CONNECT_SECONDS)
    try:
        conn.connect()
    except OSError:
        yield ("error", "The API server could not be reached. Check the API Server page.")
        return
    # Kept: http.client lets go of the socket once the reply headers are read.
    sock = conn.sock
    # Once closed here, a late reader thread must not open it again and ask after all.
    conn.auto_open = 0
    # No time limit from here: the server may be busy with another client for minutes.
    sock.settimeout(None)
    inbox: queue.Queue = queue.Queue()
    asked = time.monotonic()
    try:
        headers = _chat_headers()
    except engine_runtime.Refused as exc:
        conn.close()
        yield ("error", str(exc))
        return
    reader = (conn, body, inbox.put, headers) if headers else (conn, body, inbox.put)
    threading.Thread(target=_read_reply, args=reader, daemon=True).start()
    pieces, first = 0, None
    try:
        while True:
            try:
                item = inbox.get(timeout=CHAT_BEAT_SECONDS)
            except queue.Empty:
                item = None
            if stop.is_set():
                item = ("end", {"usage": {}, "finish": None})
            if item is None:
                yield ("status", {"state": "waiting"})
                continue
            kind, data = item
            if kind in ("token", "reasoning"):
                pieces += 1
                first = first or time.monotonic()
            if kind != "end":
                yield item
                if kind == "error":
                    return
                continue
            tokens = data["usage"].get("completion_tokens") or pieces
            ended = time.monotonic()
            elapsed = max(ended - first, 1e-6) if first else 0
            mode = performance_in_force(repo)
            yield (
                "done",
                {
                    "tokens": tokens,
                    "tps": round(tokens / elapsed, 1) if elapsed else 0.0,
                    "elapsed": round(elapsed, 2),
                    "stopped": stop.is_set(),
                    "truncated": data["finish"] == "length",
                    "repo": repo,
                    "stats": _reply_stats(data["usage"], asked, first, ended, mode),
                },
            )
            return
    finally:
        # Shutting the socket down wakes the reader and tells the server to stop.
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)
        conn.close()
        sock.close()


# --------------------------------------------------------------------------- #
# Background workers
# --------------------------------------------------------------------------- #
class DownloadCancelled(Exception):
    pass


def _set_download(repo: str, status: str, detail: str, pct: int) -> None:
    with STATE.lock:
        STATE.downloads[repo] = {"repo": repo, "status": status, "detail": detail, "pct": pct}


def _byte_progress(cancel: threading.Event, report):
    """A stand-in for tqdm that reports bytes written and honours cancel."""

    class Progress:
        def __init__(self, *args, total=None, initial=0, **kwargs):
            self.total = total
            self.n = initial
            report(self.n)

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def update(self, n=1):
            if cancel.is_set():
                raise DownloadCancelled
            self.n += n or 0
            report(self.n)

        def update_transfer(self, n=0):
            # Network bytes; only written bytes count towards progress.
            if cancel.is_set():
                raise DownloadCancelled

        def _ignore(self, *args, **kwargs):
            pass

        close = refresh = reset = set_description = set_description_str = _ignore
        set_postfix_str = set_transfer_postfix_str = _ignore

    return Progress


def cancel_download(repo: str) -> bool:
    with STATE.lock:
        entry = STATE.downloads.get(repo)
        if not entry or entry["status"] != "running":
            return False
        STATE.download_cancels[repo].set()
        entry["detail"] = "Cancelling…"
    return True


def worker_download(repo: str) -> None:
    with STATE.lock:
        running = STATE.downloads.get(repo, {}).get("status") == "running"
        cancel = STATE.download_cancels.get(repo) if running else None
    if cancel is None:
        cancel = STATE.reserve_download(repo)
    if cancel is None:
        return
    log_activity("download", repo, "running", "download started")
    marker = CACHE_DIR / f"models--{repo.replace('/', '--')}" / DOWNLOAD_MARKER
    pct = 0
    try:
        from huggingface_hub import HfApi, hf_hub_download

        api = HfApi()
        repo_info = api.repo_info(repo_id=repo, repo_type="model", files_metadata=True)
        files = [
            (s.rfilename, getattr(s, "size", None) or 0) for s in repo_info.siblings if s.rfilename
        ]

        if not files:
            raise Exception("No files found in repository")

        total = sum(size for _, size in files)
        # Marks the folder as partial until every expected file is on disk.
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(
                {
                    "revision": getattr(repo_info, "sha", None),
                    "files": [name for name, _ in files],
                }
            ),
            encoding="utf-8",
        )
        done = 0
        for index, (filename, size) in enumerate(files):
            if cancel.is_set():
                raise DownloadCancelled
            # Without sizes from the Hub, fall back to counting files.
            pct = int(done * 100 / total) if total else int(index * 100 / len(files))
            label = f"Downloading {filename}"
            _set_download(repo, "running", label, pct)

            def report(received, done=done, size=size, label=label):
                nonlocal pct
                if total and not cancel.is_set():
                    pct = max(pct, min(99, int((done + min(received, size)) * 100 / total)))
                    _set_download(repo, "running", label, pct)

            hf_hub_download(
                repo_id=repo,
                filename=filename,
                cache_dir=str(CACHE_DIR),
                tqdm_class=_byte_progress(cancel, report),
            )
            done += size

        marker.unlink(missing_ok=True)
        _set_download(repo, "success", "complete", 100)
        log_activity("download", repo, "success", "download complete")
    except Exception as exc:
        if cancel.is_set():
            _set_download(repo, "cancelled", "cancelled; Download resumes it", pct)
            log_activity("download", repo, "info", "download cancelled")
        else:
            _set_download(repo, "failed", str(exc)[:200], 0)
            log_activity("download", repo, "failed", str(exc)[:300])
            log_app("error", "download failed", repo=repo, error=str(exc)[:500])
    finally:
        invalidate_models()


def install_specs() -> list[str]:
    """Pinned versions, except that a newer installed release is never downgraded."""
    specs = []
    for name, pin in PINNED_PACKAGES.items():
        current = pkg_version(name)
        specs.append(f"{name}=={current if is_newer(current, pin) and current else pin}")
    return specs


def worker_install(kind: str) -> None:
    STATE.install = {"running": True, "kind": kind, "lines": []}
    log_activity("install", "mlx + mlx-lm", "running", f"{kind} started")

    cmd = [sys.executable, "-m", "pip", "install"]
    if kind == "reinstall":
        cmd += ["--force-reinstall"]
    else:
        cmd += ["--upgrade"]
    cmd += install_specs()

    STATE.install["lines"].append("$ " + " ".join(cmd))
    ok = False
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        if proc.stdout is None:  # pragma: no cover - PIPE guarantees a stream
            STATE.install["lines"].append("error: no stdout stream")
            proc.kill()
            return
        for line in proc.stdout:
            STATE.install["lines"].append(line.rstrip())
        ok = proc.wait() == 0
        if not ok and any("externally-managed-environment" in x for x in STATE.install["lines"]):
            STATE.install["lines"].append(
                "This Python is managed by the system and refuses pip. Create a virtual "
                "environment (python3 -m venv ~/mlx-venv) and run MLX Studio with it."
            )
    except Exception as exc:
        STATE.install["lines"].append(f"error: {exc}")

    log_activity("install", "mlx + mlx-lm", "success" if ok else "failed", f"{kind} finished")
    log_app(
        "info" if ok else "error",
        f"package {kind} {'succeeded' if ok else 'failed'}",
        tail=STATE.install["lines"][-5:],
    )
    STATE.install["running"] = False
    worker_check_versions()


def worker_engine_install(engine: engines.Engine) -> None:
    """Install an engine from the lock the app ships; Setup shows the lines."""
    lines = STATE.install["lines"]
    log_activity("install", engine.name, "running", f"{engine.version} started")
    ok = False
    try:
        engine.install(APP_DIR, lines.append)
        lines.append(f"{engine.name} {engine.version} is installed.")
        ok = True
    except engine_runtime.Refused as exc:
        lines.append(str(exc))
    except Exception as exc:
        lines.append(f"error: {exc}")
    finally:
        STATE.install["running"] = False
    log_activity("install", engine.name, "success" if ok else "failed", "install finished")
    log_app("info" if ok else "error", "engine install finished", engine=engine.id, ok=ok)


def _engine_change(action: str, body: dict) -> tuple[int, dict]:
    """``install`` or ``remove`` an engine: ``(HTTP status, reply)``."""
    engine = engines.get(body.get("engine")) if isinstance(body.get("engine"), str) else None
    if engine is None or not engine.installable:
        return 400, {"error": "that engine cannot be installed or removed here"}
    with STATE.server_lock:  # no start is in between
        running = STATE.server_proc is not None or _pid_alive(STATE.server_pid)
        if STATE.server_pending or (running and STATE.server_engine == engine.id):
            return 409, {"error": "Stop the API server first."}
        if action == "install" and engine.state(APP_DIR) == "installed":
            return 409, {"error": engine_runtime.ALREADY_INSTALLED}
        if not STATE.reserve_install(f"{engine.id} {action}"):
            why = LOADTEST_BUSY if STATE.testing() else "install already running"
            return 409, {"error": why}
    if action == "install":
        threading.Thread(target=worker_engine_install, args=(engine,), daemon=True).start()
        return 200, {"ok": True}
    try:
        engine.remove(APP_DIR)
    except (engine_runtime.Refused, OSError) as exc:
        return 409, {"error": str(exc)[:300]}
    finally:
        STATE.install["running"] = False
    log_activity("install", engine.name, "success", "removed")
    return 200, {"ok": True}


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
        except Exception as exc:
            print(f"truststore unavailable, using default CA bundle: {exc}")
        with urllib.request.urlopen(APP_UPDATE_URL, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        url = str(data.get("url") or "")
        parsed = urllib.parse.urlparse(url)
        sha256 = str(data.get("sha256") or "").lower()
        STATE.app_release = {
            "version": data.get("version"),
            "url": url if parsed.scheme == "https" and parsed.netloc else None,
            "notes": data.get("notes") or "",
            "sha256": sha256 if re.fullmatch(r"[0-9a-f]{64}", sha256) else None,
        }
        return data.get("version")
    except Exception:
        return None


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _server_pidfile() -> Path:
    return APP_DIR / "server.pid"


def _snapshot_dir(repo: str) -> Path:
    """The cached snapshot to measure for ``repo``, even while it is downloaded again."""
    # repo may come from a request: it is only compared with listed names, never joined.
    name = f"models--{repo.replace('/', '--')}"
    try:
        entry = next((p for p in CACHE_DIR.iterdir() if p.name == name), None)
    except OSError:
        entry = None
    if entry is None:
        return CACHE_DIR
    try:
        commit = (entry / "refs" / "main").read_text("utf-8").strip()
    except (OSError, ValueError):
        commit = ""
    weights = model_info.weights_bytes
    found, whole = [], None
    try:
        found = list((entry / "snapshots").glob("*"))
        # refs/main's snapshot last, so it is preferred; the others newest last.
        found.sort(key=lambda p: (p.name == commit, p.stat().st_mtime))
        # A newer revision takes over refs/main before its weights have arrived.
        whole = next((p for p in reversed(found) if weights(p) and _snapshot_complete(p)), None)
    except OSError:
        pass  # a snapshot cannot be dated or read: the weights alone decide
    return whole or max(reversed(found), key=weights, default=entry)


def _ps_field(pid: int, field: str) -> str:
    """One ``ps`` column for a pid, in a fixed locale and zone. Empty if unknown."""
    try:
        proc = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", f"{field}="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _is_our_server(
    pid: int | None, repo: str | None, started: str | None, port=None, direct: str | None = None
) -> bool:
    """True only when ``pid`` is still the API server we launched.

    A pid is reused after a crash or reboot, so both the command line and, when
    recorded, the process start time must match before it is adopted or signalled.
    """
    if not repo or not _pid_alive(pid):
        return False
    command = f"{_ps_field(pid, 'command')} "
    launchers = (" -m mlx_lm server", OLD_WRAPPER)
    marks = [f"{start} --model {repo} --host " for start in launchers]
    if direct:  # a direct engine's own command line, see Engine.identity
        marks = [direct]
    if not any(mark in command for mark in marks):
        return False
    if f" --port {port or SERVER_PORT} " not in command:
        return False
    return not started or _ps_field(pid, "lstart") == started


def _running_engine() -> engines.Engine:
    return engines.get(STATE.server_engine) or engines.MLX


def _server_port() -> int:
    return STATE.server_port if _running_engine().direct else SERVER_PORT


def _key_file() -> Path:
    """The running direct engine's key: private, new at each launch, gone at stop."""
    return APP_DIR / "server.key"


def _still_our_server(pid: int | None, repo: str | None) -> bool:
    """``_is_our_server`` for the engine that was started."""
    engine = _running_engine()
    if not engine.direct:
        return _is_our_server(pid, repo, STATE.server_identity)
    mark = engine.identity(STATE.server_snapshot)
    return _is_our_server(pid, repo, STATE.server_identity, STATE.server_port, direct=mark)


def _reset_engine() -> None:
    STATE.server_engine, STATE.server_port = "mlx", SERVER_PORT
    STATE.server_snapshot = STATE.server_verdict = None
    STATE.server_anthropic = False
    with contextlib.suppress(OSError):
        _key_file().unlink(missing_ok=True)


def _engine_refusal(engine_id, repos: list[str], port_too: bool = True) -> str | None:
    """Why this engine cannot start these models now. Nothing is changed or stopped."""
    engine = engines.get(engine_id)
    if engine is None:
        return "Unknown engine."
    if not engine.selectable:
        return f"{engine.name}: {engine.refusal}"
    if not engine.direct:
        return None
    if len(repos) > 1:
        return f"{engine.name} serves one model. Remove the second one."
    if STATE.install["running"] and STATE.install["kind"].startswith(engine.id):
        return f"{engine.name}: an install is running."
    try:
        engine.program(APP_DIR)
        engine.check_model(repos[0], _snapshot_dir(repos[0]))
    except engine_runtime.Refused as exc:
        return str(exc)
    if not port_too:
        return None
    # The server being replaced may hold the port; nothing else may.
    running = STATE.server_proc is not None or _pid_alive(STATE.server_pid)
    ours = STATE.server_pid if running else None
    return engine_runtime.port_problem(engine.port, ours=ours)


def _engines_view(repo: str) -> list[dict]:
    """Each engine and, for ``repo``, whether it could serve it and why not."""
    views = []
    for engine in engines.REGISTRY:
        why = _engine_refusal(engine.id, [repo], port_too=False)
        views.append({**engine.view(APP_DIR), "eligible": why is None, "reason": why})
    return views


def _write_server_pidfile(
    pid: int, repo: str, background: bool, repos: list[str] | None = None
) -> None:
    payload = {
        "pid": pid,
        # The first model: what the identity check reads, and all older builds know.
        "repo": repo,
        "repos": repos or [repo],
        "background": background,
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "port": _server_port(),
        "started": _ps_field(pid, "lstart"),
    }
    payload["settings"] = STATE.server_started_with
    if _running_engine().direct:
        payload.update(
            engine=STATE.server_engine,
            snapshot=STATE.server_snapshot,
            anthropic=STATE.server_anthropic,
        )
    STATE.server_identity = payload["started"]
    with contextlib.suppress(OSError):
        _server_pidfile().write_text(json.dumps(payload), encoding="utf-8")
        _server_pidfile().chmod(0o600)


def _clear_server_pidfile(only_pid: int | None = None) -> None:
    """Remove the pidfile, or only when it still describes ``only_pid``.

    A watcher for a server that has just been replaced must not delete the
    pidfile written for its successor, so callers that race pass their pid.
    """
    path = _server_pidfile()
    if only_pid is not None:
        try:
            current = json.loads(path.read_text(encoding="utf-8")).get("pid")
        except (OSError, json.JSONDecodeError):
            return
        if current != only_pid:
            return
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def _start_verdict(pid: int) -> str | None:
    """What the probe found: ``ready``, ``failed``, or None for not yet."""
    return STATE.server_verdict if pid == STATE.server_pid else None


def _server_readiness() -> dict:
    """``ready``: the running server can answer. ``load_failed``: it said it cannot."""
    with STATE.lock:
        proc, pid = STATE.server_proc, STATE.server_pid
        running = proc is not None or _pid_alive(pid)
    if not running:
        return {"ready": False, "load_failed": False}
    verdict = _start_verdict(pid)
    return {"ready": verdict == "ready", "load_failed": verdict == "failed"}


def _chat_problem(repo: str) -> str | None:
    """Why the API server cannot be asked for ``repo`` now, or None."""
    with STATE.lock:
        served = list(STATE.server_repos)
        pending = bool(STATE.server_pending)
        proc, pid = STATE.server_proc, STATE.server_pid
        first, identity = STATE.server_repo, STATE.server_identity
    if pending:
        return "The API server is starting. Wait until it is ready."
    # A pid is reused after a crash: no prompt goes to whoever has the port then.
    if proc:
        running = proc.poll() is None
    elif _running_engine().direct:
        running = _still_our_server(pid, first)
    else:
        running = _is_our_server(pid, first, identity)
    if not running:
        return "Start the API server first (API Server page)."
    # Chat is only for the model the server was started with.
    if repo not in served[:1]:
        return f"The API server does not serve {repo}."
    readiness = _server_readiness()
    if readiness["load_failed"]:
        return (
            "The API server could not load its model. "
            "On the API Server page, press Stop, then Start Server."
        )
    if not readiness["ready"]:
        return "The API server is starting. Wait until it is ready."
    return None


def _running_server() -> tuple[list[str], bool] | None:
    """``(models, background)`` of the server a new start is about to replace."""
    with STATE.lock:
        proc, pid = STATE.server_proc, STATE.server_pid
        repos, background = list(STATE.server_repos), STATE.server_background
    alive = proc.poll() is None if proc is not None else _pid_alive(pid)
    if not (alive and repos):
        return None
    # MLX stays a pair; another engine is named with its switch, to be restored as it was.
    if _running_engine().direct:
        return repos, background, STATE.server_engine, STATE.server_anthropic
    return repos, background


def _last_good_server() -> tuple[list[str], bool] | None:
    """What a new start falls back to. The caller holds ``server_lock``.

    The running server if it has reported ready, else the one it was replacing.
    """
    running = _running_server()
    if running and _start_verdict(STATE.server_pid) == "ready":
        return running
    return STATE.server_fallback or running


def _last_models_file() -> Path:
    """The last model set an API server reported ready; the card starts with it."""
    return APP_DIR / "server.last.json"


def _read_small(path: Path, limit: int) -> bytes:
    """A small regular file of ours, read without following a link or blocking."""
    # Not blocking and not a link: a pipe planted here must not stall /api/state.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        # Checked before reading; the descriptor is closed whatever it is.
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(limit + 1)
    finally:
        os.close(fd)
    if len(raw) > limit:
        raise ValueError("too large")
    return raw


def _write_private(path: Path, text: str) -> None:
    # A new name, created exclusively with mode 0600: a planted link is never followed.
    handle, temp = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(temp, path)
    finally:
        with contextlib.suppress(OSError):  # already gone after a good replace
            os.unlink(temp)


def _settings_file() -> Path:
    return APP_DIR / "settings.json"


def _valid_setting(key: str, value) -> bool:
    if choices := CHOICE_SETTINGS.get(key):
        return type(value) is type(choices[0]) and value in choices
    low, high = SETTINGS[key]
    kinds = (int, float) if key in DECIMAL_SETTINGS else (int,)
    return type(value) in kinds and low <= value <= high


def _valid_sizes(value, most: int, allow_none: bool = False) -> bool:
    """A list of different whole context sizes, none above ``most``."""
    return (
        isinstance(value, list)
        and (allow_none or bool(value))
        and len(value) <= LOAD_TEST_MAX_SIZES
        and all(type(n) is int and LOAD_TEST_SIZE_RANGE[0] <= n <= most for n in value)
        and len(set(value)) == len(value)
    )


def _valid_load_test(key: str, value) -> bool:
    if key in LOAD_TEST_LISTS:
        return _valid_sizes(value, LOAD_TEST_SIZE_RANGE[1], allow_none=key == "last_sizes")
    _, low, high = LOAD_TEST_NUMBERS[key]
    return type(value) is int and low <= value <= high


def _usable(entry: dict, keys, bad: list[str], where: str = "") -> dict[str, int]:
    """The valid values of ``entry``; the name of each unusable one goes in ``bad``."""
    found = {}
    for key, value in entry.items():
        if key in keys and _valid_setting(key, value):
            found[key] = value
        elif key in SETTINGS and value is not None:
            bad.append(f"{where}{key}")
    return found


def _valid_repo(repo) -> bool:
    return (
        isinstance(repo, str)
        and len(repo) <= MODEL_NAME_MAX
        and bool(model_info.REPO_ID.fullmatch(repo))
    )


def _read_settings() -> tuple[dict, dict]:
    """``(global values, {repo: values})``, only what is usable. Never raises."""
    return _read_all_settings()[:2]


def _read_all_settings() -> tuple[dict, dict, dict]:
    """The global values, each model's own and the load test block. Never raises."""
    shared, models, load_test, bad, problem = {}, {}, {}, [], ""
    try:
        data = json.loads(_read_small(_settings_file(), SETTINGS_MAX_BYTES).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        shared = _usable(data, SETTINGS, bad)
        entries = data.get("models", {})
        if not isinstance(entries, dict):
            entries, bad = {}, [*bad, "models"]
        for repo, entry in entries.items():
            if not (_valid_repo(repo) and isinstance(entry, dict)):
                bad.append("models entry")
                continue
            if own := _usable(entry, MODEL_SETTINGS, bad, "models: "):
                models[repo] = own
        block = data.get("load_test", {})
        if not isinstance(block, dict):
            block, bad = {}, [*bad, "load_test"]
        for key, value in block.items():
            if key in LOAD_TEST_LISTS or key in LOAD_TEST_NUMBERS:
                if _valid_load_test(key, value):
                    load_test[key] = value
                else:
                    bad.append(f"load_test: {key}")
        if bad:
            names = ", ".join(sorted(set(bad)))
            problem = f"settings.json: unusable value for {names}; using the default"
    except FileNotFoundError:
        pass
    except Exception as exc:  # parsing can fail in more ways than ValueError
        problem = f"settings.json ignored, using the defaults ({type(exc).__name__})"
    if problem and problem != STATE.settings_complaint:
        log_app("warning", problem)
    STATE.settings_complaint = problem  # each problem is said once
    return shared, models, load_test


def user_settings(repo: str | None = None) -> dict[str, int | None]:
    """Every key of SETTINGS for ``repo``: its own value, else the global one, else None."""
    shared, models = _read_settings()
    return {**dict.fromkeys(SETTINGS), **shared, **models.get(repo, {})}


def server_settings(repos: list[str]) -> dict[str, int | None]:
    """What the server for the model in ``repos`` is started with (none: the global values).

    The performance settings are left out: see ``performance_flags``.
    """
    own = user_settings(repos[0] if repos else None)
    return {key: None if key in PERFORMANCE_SETTINGS else own[key] for key in SETTINGS}


def save_settings(changes: dict, repo: str | None = None) -> None:
    """Merge checked ``changes`` into the global or one model's values; None removes."""
    with SETTINGS_LOCK:
        shared, models, load_test = _read_all_settings()
        target = shared if repo is None else models.setdefault(repo, {})
        for key, value in changes.items():
            if value is None:
                target.pop(key, None)
            else:
                target[key] = value
        _store_settings(shared, models, load_test)


def save_load_test(changes: dict | None) -> None:
    """Merge checked ``changes`` into the load test block; None removes."""
    with SETTINGS_LOCK:
        shared, models, load_test = _read_all_settings()
        merged = {} if changes is None else {**load_test, **changes}
        kept = {key: value for key, value in merged.items() if value is not None}
        _store_settings(shared, models, kept)


def reset_settings(repo: str | None = None) -> None:
    """Drop the global values, or one model's own; the rest is kept."""
    with SETTINGS_LOCK:
        shared, models, load_test = _read_all_settings()
        if repo is None:
            shared = {}
        else:
            models.pop(repo, None)
        if shared or any(models.values()) or load_test:
            _store_settings(shared, models, load_test)
        else:
            _settings_file().unlink(missing_ok=True)


def _store_settings(shared: dict, models: dict, load_test: dict) -> None:
    models = {repo: own for repo, own in models.items() if own}
    data = {**shared, **({"models": models} if models else {})}
    if load_test:
        data["load_test"] = load_test
    text = json.dumps(data)
    if len(text.encode("utf-8")) > SETTINGS_MAX_BYTES:
        raise ValueError("too many model settings: reset some models first")
    APP_DIR.mkdir(parents=True, exist_ok=True)
    _write_private(_settings_file(), text)


def _settings_change_problem(body: dict) -> str | None:
    """Why a request to change or reset settings is refused, or None."""
    repo = body.get("repo")
    if repo is not None and not _valid_repo(repo):
        return "repo must be a model id"
    if body.get("reset") is True:
        return None
    if "load_test" in body:
        if repo is not None:
            return "load_test is set for the whole app, not for one model."
        if problem := _load_test_problem(body["load_test"]):
            return problem
        if "values" not in body:
            return None
    changes = body.get("values")
    if not isinstance(changes, dict) or not changes:
        return "values required: an object of settings"
    for key, value in changes.items():
        if key not in SETTINGS:
            return "unknown setting"
        if repo is not None and key not in MODEL_SETTINGS:
            return f"{key} is set for the whole app, not for one model."
        if value is not None and not _valid_setting(key, value):
            low, high = SETTINGS[key]
            if choices := CHOICE_SETTINGS.get(key):
                options = ", ".join(map(str, choices))
                return f"{key} must be one of {options}, or empty."
            kind = "a number" if key in DECIMAL_SETTINGS else "a whole number"
            return f"{key} must be {kind} from {low} to {high}, or empty."
    return _performance_problem(repo, changes) if repo is not None else None


def _performance_problem(repo: str, changes: dict) -> str | None:
    """Why ``repo`` cannot use the compressed memory these changes would ask for."""
    if not any(changes.get(key) is not None for key in PERFORMANCE_SETTINGS):
        return None
    shared, models = _read_settings()
    own = {**models.get(repo, {}), **changes}
    chosen = {**shared, **{key: value for key, value in own.items() if value is not None}}
    why = _blocked_reason(repo, _chosen_flags(chosen))
    return why and f"{repo} cannot use compressed memory: {why}. Choose Standard."


def _model_config(repo: str) -> dict:
    try:
        return model_info.read_config(_snapshot_dir(repo))
    except Exception:  # not downloaded or unreadable: nothing is known
        return {}


def _chosen_flags(values: dict) -> dict[str, int]:
    """The mlx_lm flags that ``values``' performance mode stands for."""
    mode = values.get("performance_mode")
    if mode == "save_memory":
        return dict(SAVE_MEMORY_FLAGS)
    if mode == "advanced":
        return {k: values[k] for k in PERFORMANCE_FLAGS if values.get(k) is not None}
    return {}


def _blocked_reason(repo: str, flags: dict) -> str | None:
    if "kv_bits" not in flags:
        return None
    return model_info.kv_problem(_model_config(repo), flags.get("kv_group_size", 64))


def performance_flags(repo: str | None = None) -> tuple[dict, str | None]:
    """``(flags, why a model gets none)`` for ``repo``; None: the global choice."""
    flags = _chosen_flags(user_settings(repo))
    why = _blocked_reason(repo, flags) if repo else None
    return ({}, why) if why else (flags, None)


def performance_label(flags: dict) -> str:
    chosen = {k: flags[k] for k in PERFORMANCE_FLAGS if flags.get(k) is not None}
    if not chosen:
        return "Standard"
    if chosen == SAVE_MEMORY_FLAGS:
        return "Save memory"
    words = {
        "kv_bits": "{}-bit memory",
        "kv_group_size": "groups of {}",
        "prefill_step_size": "read step {}",
    }
    return f"Advanced ({', '.join(words[k].format(v) for k, v in chosen.items())})"


def performance_in_force(repo: str) -> str | None:
    """The mode the ready server was started with for ``repo``; None when unknown."""
    started = STATE.server_started_with
    if started is None or STATE.server_verdict != "ready" or repo not in STATE.server_repos:
        return None
    try:
        return performance_label(json.loads(started).get("performance") or {})
    except (ValueError, AttributeError):
        return None


def _performance_view(repos: list[str]) -> dict:
    """The saved mode and, for the served model, the one it was started with."""
    models = {}
    for repo in repos:
        flags, why = performance_flags(repo)
        models[repo] = {
            "saved": performance_label(flags),
            "in_force": performance_in_force(repo),
            "note": f"Standard is used: {why}." if why else None,
        }
    return {"saved": performance_label(performance_flags()[0]), "models": models}


def _load_test_problem(changes) -> str | None:
    if changes is None:
        return None
    if not isinstance(changes, dict) or not changes:
        return "load_test must be an object of values, or empty."
    for key, value in changes.items():
        if key not in LOAD_TEST_LISTS and key not in LOAD_TEST_NUMBERS:
            return "unknown load test setting"
        if value is not None and not _valid_load_test(key, value):
            if key in LOAD_TEST_LISTS:
                return (
                    f"{key} must be a list of up to {LOAD_TEST_MAX_SIZES} different sizes "
                    f"from {LOAD_TEST_SIZE_RANGE[0]}."
                )
            _, low, high = LOAD_TEST_NUMBERS[key]
            return f"{key} must be a whole number from {low} to {high}, or empty."
    return None


def load_test_view() -> dict:
    """Each load test value as it applies, with its default and range."""
    saved = _read_all_settings()[2]
    view = {}
    for key, default in LOAD_TEST_LISTS.items():
        low, high = LOAD_TEST_SIZE_RANGE
        view[key] = {"value": saved.get(key, list(default)), "default": list(default)}
        view[key].update(min=low, max=high)
    for key, (default, low, high) in LOAD_TEST_NUMBERS.items():
        view[key] = {"value": saved.get(key, default), "default": default, "min": low, "max": high}
    return view


def chat_settings(repo: str | None = None) -> dict:
    """What a Chat request sends, with the user's values for ``repo``."""
    own = user_settings(repo)
    chosen = {key: own[f"chat_{key}"] for key in ("temperature", "top_p")}
    chosen["max_tokens"] = own["chat_max_reply_tokens"]
    return {**CHAT_SETTINGS, **{k: v for k, v in chosen.items() if v is not None}}


def _launch_stamp(engine: engines.Engine, settings: dict, repo: str) -> str:
    """The set values a server start depends on: only what its command is given."""
    used = {k: settings[k] for k in engine.setting_keys if settings.get(k) is not None}
    if flags := performance_flags(repo)[0]:
        used["performance"] = flags
    return json.dumps(used, sort_keys=True)


def _settings_pending(running: bool) -> bool:
    """Whether the running server was started with other settings than are saved."""
    repos = list(STATE.server_repos)
    if not (running and repos):
        return False
    stamp = _launch_stamp(_running_engine(), server_settings(repos), repos[0])
    return (STATE.server_started_with or "{}") != stamp


def settings_view(repo: str | None = None) -> dict:
    """For the card and the Configuration page: each key's value, range and effect.

    With ``repo``: also its own value and the one it inherits from the global values.
    """
    automatic = {
        "max_context_tokens": "OptiQ: its own default. MLX: not available",
        "server_max_reply_tokens": str(SERVER_MAX_TOKENS),
        "chat_max_reply_tokens": str(CHAT_SETTINGS["max_tokens"]),
        "prompt_cache_size": "the engine's own default",
        "prompt_cache_bytes": "MLX: no limit. OptiQ: an eighth of this Mac's memory",
        "chat_temperature": str(CHAT_SETTINGS["temperature"]),
        "chat_top_p": str(CHAT_SETTINGS["top_p"]),
        "performance_mode": "Standard",
        "kv_bits": "off, full precision",
        "kv_group_size": "64",
        "prefill_step_size": "2048",
    }
    shared, models = _read_settings()
    shown = {}
    for key, value in user_settings(repo).items():
        effective = f"automatic ({automatic[key]})" if value is None else str(value)
        low, high = SETTINGS[key]
        shown[key] = {"value": value, "min": low, "max": high, "effective": effective}
        if key in CHOICE_SETTINGS:
            shown[key]["choices"] = list(CHOICE_SETTINGS[key])
        if repo is not None:
            shown[key]["own"] = models.get(repo, {}).get(key)
            shown[key]["inherited"] = shared.get(key)
    return shown


def config_view(repo: str | None = None) -> dict:
    """The Configuration page: every setting for ``repo`` (None: the global values)."""
    settings = settings_view(repo)
    for key, entry in settings.items():
        entry.setdefault("own", entry["value"])
        entry.setdefault("inherited", None)
        entry["decimal"] = key in DECIMAL_SETTINGS
        entry["global_only"] = key not in MODEL_SETTINGS
    return {
        "repo": repo,
        "settings": settings,
        "load_test": load_test_view(),
        "performance": _config_performance(repo),
    }


def _config_performance(repo: str | None) -> dict:
    """The mode chosen for ``repo`` and what its model cannot do, with the reason."""
    mode = user_settings(repo)["performance_mode"] or "standard"
    if repo is None:
        return {"mode": mode, "reason": None, "blocked": {}}
    config, blocked = _model_config(repo), {}
    if why := model_info.kv_problem(config):
        blocked["save_memory"] = why
    groups = {str(g): model_info.kv_problem(config, g) for g in model_info.KV_GROUPS}
    if groups := {group: why for group, why in groups.items() if why}:
        blocked["kv_group_size"] = groups
    return {"mode": mode, "reason": performance_flags(repo)[1], "blocked": blocked}


def _last_models() -> list[str]:
    """The remembered ids. Anything unexpected in the file's place reads as none."""
    try:
        raw = _read_small(_last_models_file(), LAST_MODELS_MAX_BYTES)
        repos = json.loads(raw.decode("utf-8"))["repos"]
        if not isinstance(repos, list) or not all(
            isinstance(name, str) and 0 < len(name) <= MODEL_NAME_MAX and name.isprintable()
            for name in repos
        ):
            return []
    except Exception:  # parsing can fail in more ways than ValueError
        return []
    return repos[:1]


def _last_engine() -> str:
    """The engine of the last server reported ready; OptiQ when none is saved."""
    try:
        raw = _read_small(_last_models_file(), LAST_MODELS_MAX_BYTES)
        # A saved start that names no engine was an MLX one.
        engine = engines.get(json.loads(raw.decode("utf-8")).get("engine"))
    except Exception:  # parsing can fail in more ways than ValueError
        return engines.OPTIQ.id
    return engine.id if engine and engine.selectable else engines.OPTIQ.id


def _last_anthropic() -> bool:
    """The Anthropic switch of the remembered start. On unless it was saved off."""
    try:
        raw = _read_small(_last_models_file(), LAST_MODELS_MAX_BYTES)
        return json.loads(raw.decode("utf-8")).get("anthropic") is not False
    except Exception:  # parsing can fail in more ways than ValueError
        return True


def _remember_models(pid: int) -> None:
    with STATE.lock:
        repos = list(STATE.server_repos) if STATE.server_pid == pid else []
    if not repos:
        return
    with contextlib.suppress(OSError):
        saved = {"repos": repos}
        if _running_engine().direct:
            saved["engine"] = STATE.server_engine
            saved["anthropic"] = STATE.server_anthropic
        _write_private(_last_models_file(), json.dumps(saved))


def _try_launch(*args) -> subprocess.Popen | None:
    """``_launch_server``, with an unexpected error recorded instead of raised."""
    try:
        return _launch_server(*args)
    except Exception as exc:
        # Nothing half started stays behind.
        with contextlib.suppress(Exception):
            stop_server()
        STATE.server_lines.append(f"failed to start: {exc}")
        STATE.server_last_error = str(exc)[:300] or type(exc).__name__
        log_app("error", "api server start failed", repo=args[0], error=str(exc)[:500])
        return None


def _bring_back(previous: tuple[list[str], bool], why: str, stops: int) -> subprocess.Popen | None:
    """Start the replaced server again. The caller holds ``STATE.server_lock``.

    One try: the start made here has nothing to fall back to.
    """
    if stops != STATE.server_stops:  # the user pressed Stop meanwhile
        return None
    STATE.server_fallback = None
    repos, background, *engine = previous
    why = why.rstrip(".")
    name = repos[0].rsplit("/", maxsplit=1)[-1]
    proc = _try_launch(repos[0], background, *((None, *engine) if engine else ()))
    if proc is None:
        notice = STATE.server_down_notice = (
            f"The new server did not start: {why}. Starting {name} again failed too: "
            f"{(STATE.server_down_notice or STATE.server_last_error or '').rstrip('.')}. "
            "The API server is stopped."
        )
    else:
        notice = f"The new server did not start: {why}. {name} was started again."
        with STATE.lock:
            if STATE.server_pid == proc.pid:
                STATE.server_apply_notice = notice
            else:  # it has died already: one notice says both
                STATE.server_down_notice = f"{notice} {STATE.server_down_notice}"
    log_activity("server", repos[0], "failed", notice)
    return proc


def _pump_output(proc: subprocess.Popen) -> None:
    """Keep a foreground server's output until it exits."""
    if proc.stdout is None:  # pragma: no cover - PIPE guarantees a stream
        STATE.server_lines.append("error: no stdout stream")
        return
    for line in proc.stdout:
        STATE.server_lines.append(engine_runtime.redact(line.rstrip()))
        STATE.server_lines = STATE.server_lines[-400:]
    proc.wait()
    with contextlib.suppress(Exception):
        STATE.server_last_error = f"exited with status {proc.returncode}"


def _probe_direct(pid: int, adopted: bool = False) -> str | None:
    """Look at the running server once. Returns why it must be stopped, or None.

    Nothing is sent until every listener on the port is seen to be this process. A
    keyed engine is checked again while it runs. Sets ``server_verdict`` when it
    answers. ``adopted``: an MLX server left by an earlier session, not warmed again.
    """
    engine, port = _running_engine(), _server_port()
    ready = STATE.server_verdict == "ready"
    if STATE.server_pid != pid or (ready and not engine.direct):
        return None
    holders = engine_runtime.listeners(port)
    if holders is None:
        return None  # unknown: nothing is sent, and the next look tries again
    if holders - {pid}:
        unsent = "; the key was not sent to it" if engine.direct else ""
        return f"another program listens on port {port}{unsent}"
    if not holders or ready:
        return None
    key = None
    if engine.direct:
        key = engine.read_key(_key_file())
        if not key:
            return "its key file is missing"
        answer = engine_runtime.auth_state(port, key, anthropic=STATE.server_anthropic)
        if answer == "open":
            return "it answered without the key"
        if answer != "ready":
            return None
    elif not adopted:
        health = engine_runtime.health(port)
        if health == 503:
            return "its model could not be loaded"
        if health != 200:
            return None
    if not engine_runtime.listener_is(port, pid):
        return None
    if (engine.direct or not adopted) and not engine_runtime.warm_up(
        port, key, engine.request_model, START_WATCH_SECONDS
    ):
        return "it could not answer a first request (see the server output)"
    if STATE.server_pid == pid:
        STATE.server_verdict = "ready"
    return None


def _end_direct(pid: int, proc: subprocess.Popen | None, repo: str) -> None:
    """Kill a direct engine's server that may not go on."""
    if proc is not None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    elif _still_our_server(pid, repo):
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)


def _watch_server(
    pid: int,
    repo: str,
    proc: subprocess.Popen | None,
    previous: tuple[list[str], bool] | None = None,
    stops: int = 0,
) -> None:
    """Notice when the API server dies and record why, so the UI can warn.

    A detached child stays a zombie until it is reaped, and os.kill(pid, 0)
    still succeeds on a zombie, so poll the Popen handle when we have one and
    fall back to the pid probe only for a server adopted from a pidfile.

    ``previous`` is the server this one replaced: it is started again when this
    one exits, or cannot load its model, before it has reported that it is ready.
    """
    checks = 0
    failed = None
    unheard = True  # this server has not yet said whether it came up
    deadline = time.monotonic() + START_WATCH_SECONDS
    direct = _running_engine().direct
    stopped_why = None  # the app stopped the server, and why
    while True:
        # Until it answers at every look, then every half minute.
        if STATE.server_pid == pid and not STATE.server_stopping:
            came_up = STATE.server_verdict == "ready"
            if not came_up or (direct and checks % 15 == 14):
                stopped_why = _probe_direct(pid, adopted=proc is None)
                late = STATE.server_verdict != "ready" and time.monotonic() > deadline
                if late and not stopped_why:
                    stopped_why = "it did not answer in time"
                alive = proc.poll() is None if proc else _pid_alive(pid)
                if stopped_why and alive and not STATE.server_stopping:
                    STATE.server_verdict = "failed"
                    STATE.server_lines.append(f"stopped by MLX Studio: {stopped_why}")
                    _end_direct(pid, proc, repo)
                    failed = stopped_why
                    break
                stopped_why = None
        if previous or unheard:
            verdict = _start_verdict(pid)
            if unheard and verdict == "ready":
                _remember_models(pid)
            unheard = verdict is None and time.monotonic() <= deadline
        if previous:
            if verdict == "failed":
                failed = "its model could not be loaded"
                break
            if verdict == "ready" or time.monotonic() > deadline:
                previous = None
                with STATE.server_lock:
                    # A newer start keeps its own fallback.
                    if STATE.server_pid == pid:
                        STATE.server_fallback = None
        if proc is not None:
            if proc.poll() is not None:
                break
        elif not _pid_alive(pid):
            break
        elif checks % 15 == 14 and not _still_our_server(pid, repo):
            break  # the pid now belongs to another process
        checks += 1
        time.sleep(2)
    if STATE.server_stopping:
        return
    code = None
    if proc is not None:
        with contextlib.suppress(Exception):
            code = proc.returncode
    if previous:
        with STATE.server_lock:
            # Not after a Stop, nor when a newer start is running or waiting.
            ours = STATE.server_pid == pid and not STATE.server_pending
            ours = ours and stops == STATE.server_stops
            if ours:
                if failed is None:
                    last = STATE.server_lines[-1][:200] if STATE.server_lines else ""
                    failed = f"it exited with status {code}" + (f" ({last})" if last else "")
                restored = _bring_back(previous, failed, stops)
        if ours:
            if restored is not None and not previous[1]:
                _pump_output(restored)
            return
        if failed and proc.poll() is None:
            return  # still alive, so not to be reported as stopped
    with STATE.lock:
        # A restart may already have installed a successor; only the watcher for
        # the current pid is allowed to declare the server down.
        if STATE.server_pid != pid:
            return
        name = repo.rsplit("/", maxsplit=1)[-1]
        STATE.server_proc = None
        STATE.server_pid = None
        STATE.server_repo = None
        STATE.server_repos = []
        STATE.server_background = False
        STATE.server_started_at = None
        engine = _running_engine()
        _reset_engine()
        STATE.server_last_error = (
            f"exited with status {code}" if code is not None else "process gone"
        )
        if stopped_why:
            STATE.server_last_error = stopped_why
        elif code == -signal.SIGKILL:
            STATE.server_last_error += ", killed, possibly out of memory"
        STATE.server_down_notice = (
            f"API server for {name} stopped unexpectedly ({STATE.server_last_error})"
        )
        if engine.direct:
            STATE.server_down_notice += f". Engine: {engine.name}; see Server output."
        if STATE.server_apply_notice:  # a restored server: one notice, not two
            STATE.server_down_notice = f"{STATE.server_apply_notice} {STATE.server_down_notice}"
            STATE.server_apply_notice = None
    _clear_server_pidfile(only_pid=pid)
    log_activity("server", repo, "failed", f"stopped unexpectedly ({STATE.server_last_error})")
    log_app("error", "api server died", repo=repo, pid=pid, exit_code=code)


def _server_command(repo: str, settings: dict, port: int, performance: dict | None = None):
    """mlx_lm's own server for ``repo`` on ``port``. Only its own flags are passed."""
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
        str(port),
        "--max-tokens",  # a default: a request may ask for more
        str(settings["server_max_reply_tokens"] or SERVER_MAX_TOKENS),
        # mlx_lm defaults to "*", which lets any web page read replies.
        "--allowed-origins",
        f"http://{HOST}:{PORT},http://localhost:{PORT}",
    ]
    for key in ("prompt_cache_size", "prompt_cache_bytes"):
        if settings[key]:
            cmd += [f"--{key.replace('_', '-')}", str(settings[key])]
    for key in PERFORMANCE_FLAGS:
        if performance and key in performance:
            cmd += [f"--{key.replace('_', '-')}", str(performance[key])]
    return cmd


def _server_env() -> dict:
    return {
        # No key or token of the app's environment reaches the server.
        **engine_runtime.minimal_environment(),
        # A request must never make the server download a model.
        "HF_HUB_OFFLINE": "1",
    }


def worker_server_start(
    repo: str,
    background: bool = False,
    ticket: int | None = None,
    engine: str = "mlx",
    anthropic: bool = True,
) -> None:
    proc = None
    with STATE.server_lock:
        try:
            stops = STATE.server_stops if ticket is None else ticket
            # Stop can win the lock while this start is still queued.
            refused = _engine_refusal(engine, [repo])
            if stops == STATE.server_stops and refused:
                _refuse_start(repo, refused)
            elif stops == STATE.server_stops:
                previous = _last_good_server()
                with STATE.lock:
                    if stops == STATE.server_stops:  # not after a Stop just now
                        STATE.server_fallback = previous
                args = [previous] if previous else []
                if engine != "mlx":
                    args = [previous, engine, anthropic]
                proc = _try_launch(repo, background, *args)
                if proc is None and previous:
                    why = STATE.server_down_notice or STATE.server_last_error or "unknown error"
                    proc, background = _bring_back(previous, why, stops), previous[1]
                elif proc is None:
                    with STATE.lock:
                        # A refusal has its own words; after a Stop there is no news.
                        if stops == STATE.server_stops and not STATE.server_down_notice:
                            why = (STATE.server_last_error or "unknown error").rstrip(".")
                            STATE.server_down_notice = f"The API server did not start: {why}."
            else:
                log_activity("server", repo, "info", "start cancelled by Stop")
        finally:
            with STATE.lock:
                if repo in STATE.server_pending:
                    STATE.server_pending.remove(repo)
    if proc is not None and not background:
        _pump_output(proc)


def _refuse_start(repo: str, why: str) -> None:
    """Say why nothing was started; a running server stays as it is."""
    with STATE.lock:
        if STATE.server_proc is not None or _pid_alive(STATE.server_pid):
            STATE.server_apply_notice = f"Nothing was changed. {why}"
        else:
            STATE.server_down_notice = f"The API server did not start: {why}"
    log_activity("server", repo, "failed", why)


def _launch_server(
    repo: str,
    background: bool,
    previous: tuple[list[str], bool] | None = None,
    engine: str = "mlx",
    anthropic: bool = True,
) -> subprocess.Popen | None:
    """Replace any running API server. The caller holds ``STATE.server_lock``."""
    if STATE.testing():
        STATE.server_down_notice = f"The API server did not start: {LOADTEST_BUSY}."
        return None
    stop_server()
    STATE.server_lines = []
    STATE.server_down_notice = None
    STATE.server_apply_notice = None
    STATE.server_last_error = None
    if engine != "mlx":
        return _launch_direct(engines.get(engine), repo, background, previous, anthropic)
    settings = server_settings([repo])
    cmd = _server_command(repo, settings, SERVER_PORT, performance_flags(repo)[0])
    STATE.server_started_with = _launch_stamp(engines.MLX, settings, repo)
    STATE.server_verdict = None
    # Its own folder as the working directory: ``-m`` looks for modules there first.
    return _spawn_server(cmd, _server_env(), [repo], background, previous, cwd=str(APP_DIR))


def _direct_settings(settings: dict) -> dict:
    """The saved settings, with the defaults a direct engine must be given."""
    reply = settings["server_max_reply_tokens"] or SERVER_MAX_TOKENS
    passed = {**settings, "server_max_reply_tokens": reply}
    ram = hardware().get("ram_bytes")
    if ram and not passed["prompt_cache_bytes"]:
        # Without the flag OptiQ keeps about 0.5 GB, too little for a follow-up.
        passed["prompt_cache_bytes"] = ram // 8
    return passed


def _launch_direct(
    engine: engines.Engine, repo: str, background: bool, previous, anthropic: bool
) -> subprocess.Popen | None:
    """Start a direct engine. The old server is stopped. Refusals raise."""
    if why := _engine_refusal(engine.id, [repo]):
        raise engine_runtime.Refused(why)
    snapshot = _snapshot_dir(repo)
    settings = server_settings([repo])
    stamp = _launch_stamp(engine, settings, repo)
    command = engine.command(
        engine.program(APP_DIR),
        snapshot,
        engine.port,
        settings=_direct_settings(settings),
        performance=performance_flags(repo)[0],
        origins=f"http://{HOST}:{PORT}",
        anthropic=anthropic,
    )
    root = APP_DIR / "engines" / engine.id / "run"
    key = engine.new_key()
    env = engine.environment(root, key)
    engine_runtime.write_key(_key_file(), key)
    STATE.server_started_with = stamp
    STATE.server_engine, STATE.server_port = engine.id, engine.port
    STATE.server_snapshot, STATE.server_verdict = str(snapshot), None
    STATE.server_anthropic = engine.anthropic and anthropic
    proc = _spawn_server(command, env, [repo], background, previous, cwd=str(root))
    if proc is None:
        _reset_engine()
    return proc


def _redacting_pipe(log) -> int:
    """A pipe into ``log`` that hides keys. It outlives the app, as the server does.

    Returns the end to write to; the caller closes it.
    """
    source, sink = os.pipe()
    try:
        subprocess.Popen(
            [sys.executable, "-I", engine_runtime.__file__],
            stdin=source,
            stdout=log,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env={},
        )
    except BaseException:
        os.close(sink)
        raise
    finally:
        os.close(source)
    return sink


def _spawn_server(
    cmd: list[str],
    env: dict,
    repos: list[str],
    background: bool,
    previous=None,
    cwd: str | None = None,
) -> subprocess.Popen | None:
    """Run ``cmd`` as the API server, record it and watch it."""
    repo, port = repos[0], _server_port()
    where = {"cwd": cwd} if cwd else {}
    try:
        if background:
            # Detached: survives the app window closing, output goes to a file
            # so the log folder still explains what happened.
            with LOG_LOCK:
                _rotate(SERVER_LOG)
            handle = open(SERVER_LOG, "a", encoding="utf-8")  # noqa: SIM115 - kept open
            with contextlib.suppress(OSError):
                SERVER_LOG.chmod(0o600)
            handle.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} start {repo} ===\n")
            handle.flush()
            # A direct engine has a key: its output goes through a filter that hides it.
            sink = _redacting_pipe(handle) if _running_engine().direct else None
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=handle if sink is None else sink,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                    env=env,
                    **where,
                )
            finally:
                if sink is not None:
                    os.close(sink)  # the server holds the only other copy
        else:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
                **where,
            )
    except Exception as exc:
        STATE.server_lines.append(f"failed to start: {exc}")
        STATE.server_last_error = str(exc)[:300]
        log_app("error", "api server failed to start", repo=repo, error=str(exc)[:500])
        return None

    STATE.server_proc = proc
    STATE.server_pid = proc.pid
    STATE.server_repo = repo
    STATE.server_repos = repos
    STATE.server_background = background
    STATE.server_started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    STATE.server_stopping = False
    _write_server_pidfile(proc.pid, repo, background, repos)
    log_activity(
        "server",
        repo,
        "running",
        f"listening on {HOST}:{port}" + (" (background)" if background else ""),
    )
    log_app(
        "info",
        "api server started",
        repo=repo,
        pid=proc.pid,
        background=background,
        port=port,
    )
    watch = (proc.pid, repo, proc)
    if previous:
        watch += (previous, STATE.server_stops)
    threading.Thread(target=_watch_server, args=watch, daemon=True).start()
    return proc


def stop_server() -> None:
    with STATE.server_lock:
        _stop_server_locked()


def _stop_server_locked() -> None:
    proc = STATE.server_proc
    pid = STATE.server_pid
    repo = STATE.server_repo
    STATE.server_stopping = True
    try:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        elif _still_our_server(pid, repo):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGTERM)
            for _ in range(50):
                if not _pid_alive(pid):
                    break
                time.sleep(0.1)
            if _still_our_server(pid, repo):
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGKILL)
        elif _pid_alive(pid):
            log_app("warning", "pid is not our api server; not signalled", pid=pid, repo=repo)
    finally:
        with STATE.lock:
            STATE.server_proc = None
            STATE.server_pid = None
            STATE.server_identity = None
            STATE.server_repo = None
            STATE.server_repos = []
            STATE.server_background = False
            STATE.server_started_at = None
            STATE.server_down_notice = None
            STATE.server_apply_notice = None
            _reset_engine()
        _clear_server_pidfile(only_pid=pid)
        if repo:
            log_activity("server", repo, "info", "server stopped")
            log_app("info", "api server stopped", repo=repo, pid=pid)
        STATE.server_stopping = False


def restore_server_state() -> None:
    """Adopt a background server left running by a previous app session."""
    path = _server_pidfile()
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _clear_server_pidfile()
        return
    pid = data.get("pid")
    engine = engines.get(data.get("engine"))
    extra = {}
    if engine is not None and engine.direct:
        extra = {"direct": engine.identity(data.get("snapshot"))}
    if (
        engine is None
        or not engine.selectable
        or not isinstance(pid, int)
        or not _is_our_server(pid, data.get("repo"), data.get("started"), data.get("port"), **extra)
    ):
        log_app("info", "dropped a stale api server pidfile", pid=pid)
        _clear_server_pidfile()
        _reset_engine()
        return
    STATE.server_started_with = data.get("settings")
    STATE.server_verdict = None
    if engine.direct and type(data.get("port")) is int:
        STATE.server_engine, STATE.server_port = engine.id, data["port"]
        STATE.server_snapshot, STATE.server_verdict = data.get("snapshot"), None
        STATE.server_anthropic = data.get("anthropic") is True
    STATE.server_pid = pid
    STATE.server_identity = data.get("started")
    STATE.server_repo = data.get("repo")
    repos = data.get("repos")
    known = isinstance(repos, list) and repos and all(isinstance(n, str) for n in repos)
    STATE.server_repos = repos if known else [data.get("repo")]
    STATE.server_background = bool(data.get("background"))
    STATE.server_started_at = data.get("started_at")
    STATE.server_stopping = False
    log_app("info", "adopted running api server", pid=pid, repo=STATE.server_repo)
    if OLD_WRAPPER in _ps_field(pid, "command"):
        log_activity(
            "server",
            STATE.server_repo,
            "info",
            "started by an earlier version of MLX Studio; restart it on the API Server page",
        )
    threading.Thread(
        target=_watch_server, args=(pid, STATE.server_repo or "?", None), daemon=True
    ).start()


# --------------------------------------------------------------------------- #
# Load test (ADR-012): mlx_loadtest.py runs it; only the app writes its files
# --------------------------------------------------------------------------- #
LOADTEST_BUSY = "a load test is running; cancel it first"


def _loadtest_file() -> Path:
    return APP_DIR / "loadtest.json"


def _loadtest_pidfile() -> Path:
    return APP_DIR / "loadtest.pid"


def _loadtest_command() -> list[str]:
    return [sys.executable, str(LOADTEST_SCRIPT)]


def _loadtest_complain(message: str) -> None:
    if message not in STATE.loadtest_complaints:
        STATE.loadtest_complaints.add(message)
        log_app("warning", message)


def _read_loadtest() -> list[dict]:
    """The stored runs, oldest first. Never raises."""
    try:
        raw = _read_small(_loadtest_file(), LOADTEST_RESULT_BYTES)
        runs = json.loads(raw.decode("utf-8"))["runs"]
        return [
            run
            for run in runs
            if isinstance(run, dict)
            and isinstance(run.get("id"), str)
            and isinstance(run.get("repo"), str)
            and isinstance(run.get("rows"), list)
            and all(isinstance(row, dict) for row in run["rows"])
        ]
    except FileNotFoundError:
        return []
    except Exception as exc:  # parsing can fail in more ways than ValueError
        _loadtest_complain(f"loadtest.json cannot be read ({type(exc).__name__})")
        return []


def _save_loadtest(record: dict) -> None:
    """Store one run, keeping the last few of each model."""
    runs = _read_loadtest()
    for at, run in enumerate(runs):
        if run["id"] == record["id"]:
            runs[at] = record
            break
    else:
        runs.append(record)
    kept, seen = [], {}
    for run in reversed(runs):
        seen[run["repo"]] = seen.get(run["repo"], 0) + 1
        if seen[run["repo"]] <= LOADTEST_RUNS_KEPT:
            kept.insert(0, run)
    text = json.dumps({"runs": kept})
    while kept and len(text.encode("utf-8")) > LOADTEST_RESULT_BYTES:
        del kept[0]
        text = json.dumps({"runs": kept})
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        _write_private(_loadtest_file(), text)
    except OSError as exc:
        _loadtest_complain(f"loadtest.json cannot be written ({type(exc).__name__})")


def _stored_runs(repo: str) -> list[dict]:
    """The runs of ``repo``, with the last one from memory if it could not be stored."""
    runs = [run for run in _read_loadtest() if run["repo"] == repo]
    last = STATE.loadtest
    ended = last and not last["active"] and last["repo"] == repo
    if ended and all(run["id"] != last["record"]["id"] for run in runs):
        runs.append(last["record"])
    return runs


def _loadtest_fingerprint(repo: str, engine_id: str = "mlx") -> dict:
    """What a result depends on; the max context is left out, as each test sets its own."""
    unused = (*CHAT_ONLY_SETTINGS, "max_context_tokens")
    settings = {
        k: v
        for k, v in server_settings([repo]).items()
        if k not in (*unused, *PERFORMANCE_SETTINGS)
    }
    engine = engines.get(engine_id)
    # Named only for another engine, so MLX results made before stay current.
    other = {"engine": engine.id, "engine_version": engine.version} if engine.direct else {}
    return {
        **other,
        "revision": _snapshot_dir(repo).name,
        "mlx": pkg_version("mlx"),
        "mlx_lm": pkg_version("mlx-lm"),
        "ram_bytes": hardware().get("ram_bytes"),
        # Standard adds nothing, so results made without a mode stay current.
        "settings": {**settings, **performance_flags(repo)[0]},
    }


def _model_max_size(repo: str) -> int:
    limit = model_info.context_limit(_model_config(repo))
    return min(limit or LOAD_TEST_SIZE_RANGE[1], LOAD_TEST_SIZE_RANGE[1])


def _offered_sizes(repo: str, values: dict) -> list[int]:
    """The saved sizes, and the model's own limit so that its top size can be picked."""
    sizes = values["sizes"]["value"]
    own = model_info.context_limit(_model_config(repo))
    low, high = LOAD_TEST_SIZE_RANGE
    if own and low <= own <= high and own not in sizes:
        return sorted([*sizes, own])
    return list(sizes)


def _loadtest_blocked_locked(repo: str) -> str | None:
    """Why no load test of ``repo`` may start now. The caller holds ``STATE.lock``."""
    if STATE.testing():
        return "a load test is already running"
    if STATE.server_pending or STATE.server_proc is not None or _pid_alive(STATE.server_pid):
        return "stop the API server first"
    if STATE.install["running"]:
        return "an install is running"
    if STATE.downloads.get(repo, {}).get("status") == "running":
        return "the model is downloading"
    if repo in STATE.deleting:
        return "the model is being deleted"
    return None


def _direct_test_launch(engine, repo: str, settings: dict) -> tuple[list[str], dict, dict]:
    """``(command, runner environment, job fields)`` for a direct engine's test.

    The key travels only in the environment; the job file never holds it.
    """
    command = engine.command(
        engine.program(APP_DIR),
        _snapshot_dir(repo),
        0,  # the runner puts a spare port here
        settings=_direct_settings(settings),
        performance=performance_flags(repo)[0],
        origins=f"http://{HOST}:{PORT}",
        anthropic=False,
    )
    root = APP_DIR / "engines" / engine.id / "run"
    key = engine.new_key()
    server_env = engine.environment(root, key)
    del server_env[engine.key_env]
    env = {
        **engine_runtime.minimal_environment(),
        "HF_HUB_OFFLINE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        engine.key_env: key,
    }
    fields = {
        "engine": engine.id,
        "key_env": engine.key_env,
        "server_env": server_env,
        "server_dir": str(root),
    }
    return command, env, fields


def _chat_template(snapshot: Path) -> str:
    """The model's chat template text, from the first file that holds one."""
    for name in ("chat_template.jinja", "chat_template.json", "tokenizer_config.json"):
        try:
            with open(snapshot / name, "rb") as handle:
                text = handle.read(TEMPLATE_BYTES).decode("utf-8")
            if name.endswith(".json"):
                text = json.loads(text)["chat_template"]
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            continue
        if text:
            return str(text)  # a list of named templates is searched as a whole
    return ""


def thinking_support(repo: str) -> dict:
    """Whether a request can switch thinking on and off (``enable_thinking``).

    Both engines hand ``chat_template_kwargs`` to the template, so it decides.
    """
    found = "enable_thinking" in _chat_template(_snapshot_dir(repo))
    reason = None if found else "This model's chat template has no enable_thinking switch."
    return {"supported": found, "reason": reason}


def _loadtest_job(
    repo: str,
    sizes: list[int],
    run_id: str,
    workdir: Path,
    engine_id: str = "mlx",
    thinking: bool | None = None,
) -> tuple[dict, dict]:
    """The runner's job and the environment its server inherits."""
    engine = engines.get(engine_id)
    values = {key: entry["value"] for key, entry in load_test_view().items()}
    settings = server_settings([repo])
    # The largest size asked for, plus room for its follow-up reply (a direct engine
    # is started with it as its max context).
    asked = {**settings, "max_context_tokens": max(sizes) + values["reply_tokens"]}
    fields = {}
    if engine.direct:
        command, env, fields = _direct_test_launch(engine, repo, asked)
    else:
        command = _server_command(repo, asked, 0, performance_flags(repo)[0])
        env = _server_env()
        env["PYTHONDONTWRITEBYTECODE"] = "1"  # nothing is written into the app bundle
    snapshot = _snapshot_dir(repo)
    config = model_info.read_config(snapshot)
    if thinking is not None:
        fields["thinking"] = thinking
    job = {
        "run_id": run_id,
        "repo": repo,
        "sizes": sorted(sizes),
        **{key: values[key] for key in LOAD_TEST_NUMBERS},
        "context_limit": model_info.context_limit(config),
        "request_model": engine.request_model,
        "snapshot": str(snapshot),
        "command": command,
        "workdir": str(workdir),
        **fields,
    }
    return job, env


def start_loadtest(
    repo: str, sizes, engine_id: str = "mlx", thinking: bool | None = None
) -> tuple[int, dict]:
    """``(HTTP status, reply)``. Starts nothing while a server or other work runs."""
    engine = engines.get(engine_id)
    if engine is None or not engine.selectable:
        return 400, {"error": "that engine cannot be tested"}
    if repo not in _complete_models():
        return 400, {"error": f"{repo} is not installed completely"}
    most = _model_max_size(repo)
    if not _valid_sizes(sizes, most):
        return 400, {
            "error": f"sizes: 1 to {LOAD_TEST_MAX_SIZES} different whole numbers "
            f"from {LOAD_TEST_SIZE_RANGE[0]} to {most}"
        }
    if thinking is not None and type(thinking) is not bool:
        return 400, {"error": "thinking must be true or false"}
    support = thinking_support(repo)
    if thinking and not support["supported"]:
        return 400, {"error": support["reason"]}
    # Named in every request where the model has the switch; not given means on.
    thinking = thinking is not False if support["supported"] else None
    if engine.direct and (why := _engine_refusal(engine.id, [repo], port_too=False)):
        return 409, {"error": why}
    run_id = secrets.token_hex(8)
    with STATE.server_lock:
        with STATE.lock:
            if blocked := _loadtest_blocked_locked(repo):
                return 409, {"error": blocked}
            # No queued start or restore may follow.
            STATE.server_stops += 1
            STATE.server_fallback = None
            record = {
                "id": run_id,
                "repo": repo,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": "running",
                "reason": "",
                "sizes": sorted(sizes),
                "rows": [],
            }
            if engine.direct:
                record["engine"] = engine.id
            if thinking is not None:
                record["thinking"] = thinking
            run = {"active": True, "repo": repo, "record": record, "status": "running"}
            run.update(progress={}, cancel=False, proc=None, server_pid=None)
            previous, STATE.loadtest = STATE.loadtest, run
        workdir = None
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            workdir = Path(tempfile.mkdtemp(prefix="loadtest-run-", dir=APP_DIR))
            job, env = _loadtest_job(repo, sizes, run_id, workdir, engine.id, thinking)
            where, after = {}, "--max-tokens"
            if engine.direct:
                where = {"cwd": job["server_dir"]}
                run["pids"] = {"repo": repo, "engine": engine.id, "snapshot": job["snapshot"]}
            record.update(
                fingerprint=_loadtest_fingerprint(repo, engine.id),
                flags=job["command"][
                    job["command"].index("--port") + 2
                    if engine.direct
                    else job["command"].index(after) :
                ],
                values={key: job[key] for key in LOAD_TEST_NUMBERS},
            )
            with open(workdir / "runner.log", "wb") as errors:
                proc = subprocess.Popen(
                    _loadtest_command(),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=errors,
                    text=True,
                    env=env,
                    start_new_session=True,
                    **where,
                )
            run["proc"] = proc
            _write_loadtest_pidfile(run, runner=proc.pid)
            proc.stdin.write(json.dumps(job) + "\n")
            proc.stdin.flush()
        except Exception as exc:
            if run["proc"]:
                _kill_runner(run["proc"])
            if workdir:
                shutil.rmtree(workdir, ignore_errors=True)
            _loadtest_pidfile().unlink(missing_ok=True)
            STATE.loadtest = previous
            log_app("error", "load test failed to start", repo=repo, error=repr(exc)[:500])
            return 500, {"error": f"the load test could not start ({type(exc).__name__})"}
    _save_loadtest(record)
    log_activity("loadtest", repo, "running", f"load test started: {record['sizes']}")
    run["thread"] = threading.Thread(target=_watch_loadtest, args=(run, workdir), daemon=True)
    run["thread"].start()
    return 200, {"ok": True, "run_id": run_id}


def _write_loadtest_pidfile(run: dict, **pids) -> None:
    """Who to end if this app dies: each pid with its start time."""
    saved = run.setdefault("pids", {"repo": run["repo"]})
    for name, pid in pids.items():
        saved[name], saved[f"{name}_started"] = pid, _ps_field(pid, "lstart")
    with contextlib.suppress(OSError):
        _write_private(_loadtest_pidfile(), json.dumps(saved))


def _loadtest_event(run: dict, event: dict) -> None:
    record, kind = run["record"], event.get("event")
    if kind == "progress":
        fields = {key: value for key, value in event.items() if key != "event"}
        last = run["progress"]
        if fields.get("size") == last.get("size"):
            fields = {**last, **fields}
            if "done" not in event and "step" in event:
                fields.pop("done", None)
                fields.pop("total", None)
        run["progress"] = fields
    elif kind == "server" and type(event.get("pid")) is int:
        run["server_pid"] = event["pid"]
        _write_loadtest_pidfile(run, server=event["pid"])
    elif kind == "row" and isinstance(event.get("row"), dict):
        record["rows"] = [*record["rows"], event["row"]]
        _save_loadtest(record)
    elif kind == "done":
        record["status"] = str(event.get("status"))
        record["reason"] = str(event.get("reason") or "")[:500]


def _log_tail(path: Path) -> list[str]:
    try:
        with open(path, "rb") as handle:
            handle.seek(max(0, os.fstat(handle.fileno()).st_size - 64 * 1024))
            text = handle.read().decode("utf-8", "replace")
    except OSError:
        return []
    return text.splitlines()[-LOADTEST_LOG_LINES:]


def _watch_loadtest(run: dict, workdir: Path) -> None:
    """Follow the runner to its end, then remove its files."""
    proc, record = run["proc"], run["record"]
    try:
        while line := proc.stdout.readline(LOADTEST_LINE_BYTES):
            try:
                _loadtest_event(run, json.loads(line))
            except Exception as exc:  # one bad line must not end the watch
                log_app("warning", "load test: unusable event", error=type(exc).__name__)
        proc.wait(timeout=LOADTEST_WAIT_SECONDS)
    except Exception as exc:
        log_app("error", "load test watch failed", error=repr(exc)[:300])
    finally:
        _kill_runner(proc)
        # The server does not exit by itself when the runner is gone.
        _end_test_server(run.get("pids", {}))
        if record["status"] == "running":
            if run["cancel"]:
                record["status"], record["reason"] = "cancelled", "cancelled by you"
            else:
                record["status"] = "failed"
                record["reason"] = "the test runner stopped unexpectedly"
        if record["status"] == "failed":
            lines = _log_tail(workdir / "runner.log") + _log_tail(workdir / "server.log")
            log_app(
                "error",
                "load test failed",
                repo=record["repo"],
                reason=record["reason"],
                log=lines[-LOADTEST_LOG_LINES:],
            )
        shutil.rmtree(workdir, ignore_errors=True)
        _loadtest_pidfile().unlink(missing_ok=True)
        _save_loadtest(record)
        log_activity("loadtest", record["repo"], record["status"], record["reason"])
        with STATE.lock:
            run["status"], run["active"] = record["status"], False


def _kill_runner(proc: subprocess.Popen) -> None:
    """End the runner's process group; the watcher then ends its test server."""
    if proc.poll() is None:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            proc.kill()
            proc.wait(timeout=5)


def cancel_loadtest() -> bool:
    """Ask the runner to stop; False when no test runs."""
    with STATE.lock:
        run = STATE.loadtest
        if not (run and run["active"] and run["proc"]):
            return False
        run["cancel"], run["status"] = True, "cancelling"
    proc = run["proc"]
    with contextlib.suppress(OSError, ValueError):
        proc.stdin.write("cancel\n")
        proc.stdin.flush()
    timer = threading.Timer(LOADTEST_CANCEL_SECONDS, _kill_runner, args=(proc,))
    timer.daemon = True
    timer.start()
    return True


def shutdown_loadtest() -> None:
    """Quitting the app: end the test and wait until its files are cleared."""
    if not cancel_loadtest():
        return
    run = STATE.loadtest
    if thread := run.get("thread"):
        thread.join(timeout=LOADTEST_CANCEL_SECONDS)
        _kill_runner(run["proc"])
        thread.join(timeout=5)


def _end_left(saved: dict, name: str, marks: tuple[str, ...]) -> None:
    """Kill the process stored as ``name``, only while it is still the one we started."""
    pid, started = saved.get(name), saved.get(f"{name}_started")
    if type(pid) is not int or not started or not _pid_alive(pid):
        return
    command = f"{_ps_field(pid, 'command')} "
    if _ps_field(pid, "lstart") == started and all(mark in command for mark in marks):
        with contextlib.suppress(OSError):
            if os.getpgid(pid) == pid:
                os.killpg(pid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        log_app("info", "ended a left over load test process", pid=pid, kind=name)


def _end_test_server(saved: dict) -> None:
    """End a load test's server, if one is left: it does not exit when the runner is gone."""
    engine = engines.get(saved.get("engine"))
    if engine is not None and engine.direct:
        marks = (engine.identity(saved.get("snapshot")),)
    else:
        repo = saved.get("repo")
        marks = tuple(
            f"{start} --model {repo} --host " for start in (" -m mlx_lm server", OLD_WRAPPER)
        )
    for mark in marks:
        _end_left(saved, "server", (mark,))


def restore_loadtest() -> None:
    """Clear what a killed app left: end only processes that are still ours."""
    try:
        saved = json.loads(_read_small(_loadtest_pidfile(), LAST_MODELS_MAX_BYTES))
        saved["repo"]
    except Exception:  # missing or unusable: nothing is signalled
        saved = {}
    _end_left(saved, "runner", (str(LOADTEST_SCRIPT),))
    _end_test_server(saved)
    for record in _read_loadtest():
        if record.get("status") == "running":
            record["status"] = "interrupted"
            record["reason"] = "MLX Studio stopped during the test"
            _save_loadtest(record)
    for left in APP_DIR.glob("loadtest-run-*"):
        if left.is_dir() and not left.is_symlink():
            shutil.rmtree(left, ignore_errors=True)
    with contextlib.suppress(OSError):
        _loadtest_pidfile().unlink(missing_ok=True)


def loadtest_summary() -> dict | None:
    """The short form for polling."""
    run = STATE.loadtest
    if not run:
        return None
    record = run["record"]
    return {
        **run["progress"],
        "active": run["active"],
        "repo": run["repo"],
        "run_id": record["id"],
        "status": run["status"],
        "reason": record["reason"],
    }


def loadtest_view(repo: str) -> dict:
    """The Load Test page for ``repo``."""
    current = functools.cache(lambda engine: _loadtest_fingerprint(repo, engine))
    most = _model_max_size(repo)
    values = load_test_view()
    with STATE.lock:
        blocked = _loadtest_blocked_locked(repo)
    run = STATE.loadtest
    return {
        "repo": repo,
        "status": loadtest_summary(),
        "rows": list(run["record"]["rows"]) if run and run["repo"] == repo else [],
        "results": [
            {**record, "stale": record.get("fingerprint") != current(record.get("engine", "mlx"))}
            for record in _stored_runs(repo)
        ],
        "sizes": {
            "min": LOAD_TEST_SIZE_RANGE[0],
            "max": most,
            "offered": [
                {"size": size, "allowed": size <= most} for size in _offered_sizes(repo, values)
            ],
        },
        "load_test": values,
        "thinking": thinking_support(repo),
        "blocked": blocked,
    }


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
# POST routes that act on a model named by the "repo" field.
REPO_ROUTES = frozenset(
    {
        "/api/chat",
        "/api/download",
        "/api/download/cancel",
        "/api/delete",
        "/api/reveal",
        "/api/server/start",
        "/api/loadtest/start",
    }
)


class BadBody(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


class Handler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/{APP_VERSION}"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter console
        """Keep the console clean but record failed requests in app.log.

        BaseHTTPRequestHandler passes the status code as a string, so coerce
        before comparing.
        """
        with contextlib.suppress(Exception):
            code = int(args[1]) if len(args) > 1 else 0
            if code >= 400:
                log_app("warning", "http error", status=code, detail=fmt % args)

    def handle_one_request(self) -> None:
        """Treat client disconnects as normal, not as server errors."""
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError):
            self.close_connection = True
        except Exception as exc:
            log_app("error", "request failed", error=repr(exc), path=self.path)
            raise

    # -- plumbing -------------------------------------------------------- #
    def end_headers(self) -> None:
        # Here so the chat stream and the library's own error pages get them too.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        super().end_headers()

    def _allowed_hosts(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _foreign_request(self) -> str | None:
        """Why a request did not come from this app's own origin, or None.

        The Host check stops DNS rebinding; the Origin check stops other sites.
        """
        allowed = self._allowed_hosts()
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1 or hosts[0].strip().lower() not in allowed:
            return "forbidden host"
        # Every value counts: a second header must not slip by.
        own = {f"http://{host}" for host in allowed}
        if any(origin not in own for origin in self.headers.get_all("Origin") or []):
            return "forbidden origin"
        return None

    def _post_refusal(self, path: str) -> tuple[int, str] | None:
        reason = self._foreign_request()
        if reason:
            return 403, reason
        # Exactly one.
        types = self.headers.get_all("Content-Type") or []
        if len(types) != 1 or types[0].split(";")[0].strip().lower() != "application/json":
            return 415, "Content-Type must be application/json"
        if path not in TOKEN_EXEMPT and not self._has_token():
            return 403, BAD_TOKEN
        return None

    def _has_token(self) -> bool:
        sent = (self.headers.get(TOKEN_HEADER) or "").encode("utf-8")
        return secrets.compare_digest(sent, API_TOKEN.encode())

    def _reject(self, code: int, message: str) -> None:
        """Refuse a request and close the connection.

        The unread body must never be parsed as a second request, so it is
        discarded (bounded in size and time) after the answer is sent.
        """
        self.close_connection = True
        self._send(
            code,
            json.dumps({"error": message}).encode("utf-8"),
            extra_headers={"Connection": "close"},
        )
        with contextlib.suppress(OSError, ValueError):
            left = min(int(self.headers.get("Content-Length") or 0), 4 * self.MAX_BODY)
            self.connection.settimeout(1)
            while left > 0:
                chunk = self.rfile.read(min(left, 65536))
                if not chunk:
                    break
                left -= len(chunk)

    def _send(
        self,
        code: int,
        body: bytes,
        ctype: str = "application/json",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # A framed copy of the UI would send the right Origin and token.
        self.send_header("X-Frame-Options", "DENY")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"))

    MAX_BODY = 8 * 1024 * 1024  # 8 MiB: chat history is the largest legitimate body

    def _body(self) -> dict:
        """The JSON object sent with a POST; raises BadBody for anything else."""
        if self.headers.get("Transfer-Encoding"):
            raise BadBody(411, "Content-Length is required")
        raw = (self.headers.get("Content-Length") or "0").strip()
        if not raw.isdecimal():
            raise BadBody(400, "invalid Content-Length")
        length = int(raw)
        if length > self.MAX_BODY:
            raise BadBody(413, "request body too large")
        if not length:
            return {}
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, RecursionError):
            raise BadBody(400, "body is not valid JSON") from None
        if not isinstance(body, dict):
            raise BadBody(400, "body must be a JSON object")
        return body

    # -- static ---------------------------------------------------------- #
    # The request path is never used to build a filesystem path. It is used as a
    # dictionary key into a map built by scanning ui/ at startup, so traversal is
    # impossible by construction rather than by validation.
    def _static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        target = STATIC_FILES.get(rel)
        if target is None:
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
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        reason = self._foreign_request()
        if reason:
            self._reject(403, reason)
            return
        # Static files stay open; they carry no token.
        if path.startswith("/api/") and path not in OPEN_GETS and not self._has_token():
            self._reject(403, BAD_TOKEN)
            return

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
        if path == "/api/logs/files":
            self._json({"dir": str(LOG_DIR), "files": log_files()})
            return
        if path == "/api/logs/app":
            limit = 500
            raw = (query.get("limit") or [""])[0]
            if raw.isdigit():
                limit = max(1, min(int(raw), 5000))
            self._json({"entries": read_app_log(limit)})
            return
        if path == "/api/logs/export":
            body_bytes, name = build_diagnostic_bundle()
            self._send(
                200,
                body_bytes,
                "application/zip",
                extra_headers={"Content-Disposition": f'attachment; filename="{name}"'},
            )
            log_app("info", "diagnostic bundle exported", file=name, bytes=len(body_bytes))
            return
        if path == "/api/environment":
            self._json(environment_report())
            return
        if path == "/api/catalog":
            self._json({"catalog": model_catalog(), "hardware": hardware()})
            return
        if path == "/api/versions":
            worker_check_versions()
            self._json(STATE.snapshot()["versions"])
            return
        if path in ("/api/config", "/api/loadtest"):
            repo = (query.get("repo") or [""])[0]
            if path == "/api/config" and not repo:
                self._json(config_view())
            elif not _valid_repo(repo):
                self._json({"error": "repo must be a model id"}, 400)
            elif path == "/api/config":
                self._json(config_view(repo))
            else:
                self._json(loadtest_view(repo))
            return
        if path == "/api/engines":
            repo = (query.get("repo") or [""])[0]
            if not _valid_repo(repo):
                self._json({"error": "repo must be a model id"}, 400)
            else:
                self._json({"engines": _engines_view(repo)})
            return
        if path == "/api/ping":
            self._json({"ok": True, "app": APP_VERSION})
            return
        self._static(path)

    # -- POST ------------------------------------------------------------ #
    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        refusal = self._post_refusal(path)
        if refusal:
            self._reject(*refusal)
            return
        try:
            body = self._body()
        except BadBody as exc:
            self._reject(exc.code, str(exc))
            return
        repo = body.get("repo")
        repo = repo if isinstance(repo, str) else ""

        if path == "/api/focus":
            # A second launch asks this instance to come forward instead of
            # starting a rival server on the same port.
            self._json({"ok": focus_window()})
            return

        if path in REPO_ROUTES and not repo:
            self._json({"error": "repo required (a string)"}, 400)
            return

        if path == "/api/chat":
            messages = body.get("messages")
            if not (
                isinstance(messages, list)
                and messages
                and all(
                    isinstance(m, dict)
                    and isinstance(m.get("role"), str)
                    and isinstance(m.get("content"), str)
                    for m in messages
                )
            ):
                self._json({"error": "messages required: a list of {role, content}"}, 400)
                return
            self._stream_chat(repo, messages)
            return

        if path == "/api/chat/stop":
            with STATE.lock:
                STATE.stop_event.set()
            self._json({"ok": True})
            return

        if path == "/api/loadtest/start":
            engine = body.get("engine", "mlx")
            if not isinstance(engine, str):
                code, answer = 400, {"error": "engine must be a name"}
            else:
                code, answer = start_loadtest(repo, body.get("sizes"), engine, body.get("thinking"))
            self._json(answer, code)
            return

        if path == "/api/loadtest/cancel":
            if cancel_loadtest():
                self._json({"ok": True})
            else:
                self._json({"error": "no load test is running"}, 404)
            return

        if path == "/api/download":
            if STATE.testing(repo):
                self._json({"error": LOADTEST_BUSY}, 409)
                return
            if STATE.reserve_download(repo) is None:
                self._json({"error": "already downloading"}, 409)
                return
            threading.Thread(target=worker_download, args=(repo,), daemon=True).start()
            self._json({"ok": True})
            return

        if path == "/api/download/cancel":
            if cancel_download(repo):
                self._json({"ok": True})
            else:
                self._json({"error": "not downloading"}, 404)
            return

        if path == "/api/delete":
            self._delete_model(repo)
            return

        if path == "/api/reveal":
            match = next((m for m in installed_models() if m["repo"] == repo), None)
            if not match:
                self._json({"error": "not installed"}, 404)
                return
            ok, out = run_cmd(["open", "-R", match["path"]], timeout=10)
            if ok:
                self._json({"ok": True})
            else:
                self._json({"error": f"could not open Finder: {out.strip()[:200]}"}, 500)
            return

        if path == "/api/server/start":
            if body.get("also"):
                self._json({"error": "the server serves one model"}, 400)
                return
            engine = body.get("engine", "mlx")
            if not isinstance(engine, str) or engines.get(engine) is None:
                self._json({"error": "unknown engine"}, 400)
                return
            # Nothing else is launched, and a running server is not stopped for it.
            if repo not in _complete_models():
                self._json({"error": f"{repo} is not installed completely"}, 400)
                return
            background = bool(body.get("background"))
            options = ()
            if engine != "mlx":
                if why := _engine_refusal(engine, [repo]):
                    self._json({"error": why}, 409)
                    return
                options = (engine, body.get("anthropic") is not False)
            with STATE.lock:
                deleting = repo in STATE.deleting
                testing = STATE.testing()
                if not (deleting or testing):
                    STATE.server_pending.append(repo)
                    ticket = STATE.server_stops
            if deleting or testing:
                why = "the model is being deleted" if deleting else LOADTEST_BUSY
                self._json({"error": why}, 409)
                return
            threading.Thread(
                target=worker_server_start,
                args=(repo, background, ticket, *options),
                daemon=True,
            ).start()
            self._json({"ok": True, "background": background})
            return

        if path == "/api/server/stop":
            # worker_server_start logs the stop once its output pump exits.
            STATE.cancel_pending_starts()
            stop_server()
            self._json({"ok": True})
            return

        if path == "/api/server/settings":
            problem = _settings_change_problem(body)
            if problem:
                self._json({"error": problem}, 400)
                return
            try:
                if body.get("reset") is True:
                    reset_settings(body.get("repo"))
                else:
                    if "load_test" in body:
                        save_load_test(body["load_test"])
                    if "values" in body:
                        save_settings(body["values"], body.get("repo"))
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
                return
            except OSError as exc:
                self._json({"error": f"could not save the settings: {exc.strerror}"}, 500)
                return
            self._json({"ok": True})
            return

        if path == "/api/server/key":
            # Only when asked, only while our server alone holds the port; never logged.
            if not (_running_engine().direct and STATE.server_verdict == "ready"):
                self._json({"error": "no server with a key is ready"}, 409)
                return
            try:
                header = _chat_headers()["Authorization"]
            except engine_runtime.Refused as exc:
                self._json({"error": str(exc)}, 409)
                return
            self._json({"key": header.removeprefix("Bearer ")})
            return

        if path in ("/api/engines/install", "/api/engines/remove"):
            code, answer = _engine_change(path.rsplit("/", 1)[1], body)
            self._json(answer, code)
            return

        if path == "/api/server/notice/clear":
            STATE.server_down_notice = None
            STATE.server_apply_notice = None
            self._json({"ok": True})
            return

        if path == "/api/install":
            kind = body.get("kind")
            kind = kind if isinstance(kind, str) and kind else "install"
            if not STATE.reserve_install(kind):
                why = LOADTEST_BUSY if STATE.testing() else "install already running"
                self._json({"error": why}, 409)
                return
            threading.Thread(target=worker_install, args=(kind,), daemon=True).start()
            self._json({"ok": True})
            return

        if path == "/api/logs/clear":
            cleared = []
            for target in sorted(LOG_DIR.glob("activity.log*")):
                try:
                    target.unlink(missing_ok=True)
                    cleared.append(target.name)
                except OSError as exc:
                    self._json({"error": str(exc)}, 500)
                    return
            log_app("info", "activity log cleared", files=cleared)
            self._json({"ok": True, "cleared": cleared})
            return

        self._json({"error": "unknown endpoint"}, 404)

    def _delete_model(self, repo: str) -> None:
        with STATE.lock:
            busy = STATE.in_use_locked(repo)
            if not busy:
                STATE.deleting.add(repo)  # blocks load, download and serve meanwhile
        if busy:
            self._json({"error": busy}, 409)
            return
        try:
            match = next((m for m in installed_models() if m["repo"] == repo), None)
            if not match:
                self._json({"error": "not installed"}, 404)
                return
            try:
                shutil.rmtree(match["path"])
            except OSError as exc:
                log_activity("delete", repo, "failed", str(exc)[:300])
                log_app("error", "delete failed", repo=repo, error=str(exc)[:500])
                self._json({"error": f"could not delete: {exc}"}, 500)
                return
            log_activity("delete", repo, "success", "removed from disk")
            self._json({"ok": True})
        finally:
            invalidate_models()
            with STATE.lock:
                STATE.deleting.discard(repo)

    # -- SSE chat -------------------------------------------------------- #
    def _stream_chat(self, repo: str, messages: list[dict]) -> None:
        stop = threading.Event()
        busy = _chat_problem(repo)
        with STATE.lock:
            if not busy and STATE.generating:
                busy = "a reply is already being generated"
            elif not busy:
                STATE.generating = True
                STATE.stop_event = stop
        if busy:
            self._json({"error": busy}, 409)
            return

        def emit(event: str, data) -> None:
            payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
            self.wfile.write(payload.encode("utf-8"))
            self.wfile.flush()

        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            with contextlib.closing(stream_chat(repo, messages, stop)) as reply:
                for kind, payload in reply:
                    emit(kind, payload)
                    if kind == "done":
                        log_activity(
                            "chat",
                            repo,
                            "success",
                            f"{payload['tokens']} tokens @ {payload['tps']} tok/s",
                        )
                    elif kind == "error":
                        log_activity("chat", repo, "failed", str(payload)[:300])
        except (BrokenPipeError, ConnectionResetError):
            log_activity("chat", repo, "failed", "client disconnected")
        finally:
            with STATE.lock:
                STATE.generating = False
            self.close_connection = True


def launch_url() -> str:
    """The address of our own UI, with the session token. Never print or log it.

    A fragment is not sent over HTTP; the page keeps it and clears the address bar.
    """
    return f"http://{HOST}:{PORT}/#t={API_TOKEN}"


def open_in_browser(url: str) -> bool:
    """Open our URL in the default browser without putting it on a command line.

    Other users can read command lines with ps, so osascript gets it on stdin, and
    the fallback opens it inside this process. ``webbrowser`` is not used: it runs
    ``/usr/bin/open <url>``.
    """
    # Nothing that could end the AppleScript string.
    if not re.fullmatch(r"http://[\w.:/#=-]+", url):
        raise ValueError("not an MLX Studio address")
    try:
        done = subprocess.run(
            ["/usr/bin/osascript"],
            input=f'open location "{url}"'.encode(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        if done.returncode == 0:
            return True
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        from AppKit import NSWorkspace
        from Foundation import NSURL

        if NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url)):
            return True
    except Exception as exc:  # the type only: a message may quote the address
        log_app("warning", "in-process open failed", error=type(exc).__name__)
    advice = f"could not open the browser: quit {APP_NAME} and open it again from its icon"
    log_app("warning", advice)
    print(advice)
    return False


def focus_window() -> bool:
    """Bring the existing window to the front. Safe to call from a handler thread.

    pywebview's cocoa backend routes show/restore through AppHelper.callAfter, so
    this does not touch AppKit off the main thread. In browser mode only this
    process knows the token, so it opens the tab again.
    """
    window = STATE.window
    if window is None:
        if not STATE.browser_mode:
            return False
        with REOPEN_LOCK:
            now = time.monotonic()
            if STATE.reopened_at is not None and now - STATE.reopened_at < REOPEN_SECONDS:
                return False
            STATE.reopened_at = now
        return open_in_browser(launch_url())
    try:
        window.restore()
        window.show()
        return True
    except Exception as exc:
        log_app("warning", "could not focus window", error=str(exc)[:200])
        return False


def instance_is_running() -> bool:
    """True when another MLX Studio already answers on the app port."""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/api/ping", timeout=2) as resp:
            data = json.loads(resp.read().decode())
        return bool(data.get("ok"))
    except Exception:
        return False


def request_focus_from_running_instance() -> bool:
    """Ask the instance already on the port to raise its window."""
    try:
        req = urllib.request.Request(
            f"http://{HOST}:{PORT}/api/focus",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return bool(json.loads(resp.read().decode()).get("ok"))
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def adopt_bundle_identity() -> None:
    """In the .app, show its name and icon: the launcher execs Python's own app.

    The icon is what the Dock and the confirm() boxes show. Does nothing outside
    the bundle. Cosmetic, so a failure is only logged.
    """
    in_bundle = APP_ICON.parent.parts[-2:] == ("Contents", "Resources")
    if sys.platform != "darwin" or not in_bundle or not APP_ICON.is_file():
        return
    # The name first: AppKit reads it when the application object is made.
    try:
        from Foundation import NSBundle

        bundle = NSBundle.mainBundle()
        for info in (bundle.localizedInfoDictionary(), bundle.infoDictionary()):
            if info is not None:
                info["CFBundleName"] = APP_NAME
    except Exception as exc:
        log_app("warning", "could not set the app name", error=str(exc)[:200])
    try:
        from AppKit import NSApplication, NSImage

        image = NSImage.alloc().initWithContentsOfFile_(str(APP_ICON))
        if image is None:
            raise ValueError("unreadable icon file")
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(0)  # a Dock app first, the order pywebview uses
        app.setApplicationIconImage_(image)
        # NSAlert, the confirm() box, still drew Python's icon after that call
        # (macOS 27): it looks the icon up by this name, so the name moves too.
        old = NSImage.imageNamed_("NSApplicationIcon")
        if old is not None:
            old.setName_(None)
        image.setName_("NSApplicationIcon")
    except Exception as exc:
        log_app("warning", "could not set the app icon", error=str(exc)[:200])


def serve() -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main() -> None:
    if not UI_DIR.exists():
        print(f"UI directory missing: {UI_DIR}")
        sys.exit(1)

    # Single instance: a second launch must raise the existing window, not fail
    # on a busy port and leave the user with nothing.
    if instance_is_running():
        focused = request_focus_from_running_instance()
        log_app("info", "second launch forwarded to the running instance", focused=focused)
        print(f"{APP_NAME} is already running; focused the existing window")
        sys.exit(0)

    try:
        httpd = serve()
    except OSError as exc:
        # Lost the race between the check above and the bind.
        if request_focus_from_running_instance():
            print(f"{APP_NAME} is already running; focused the existing window")
            sys.exit(0)
        log_app("error", "could not bind the app port", port=PORT, error=str(exc))
        raise

    url = f"http://{HOST}:{PORT}/"
    print(f"{APP_NAME} {APP_VERSION} → {url}")

    _migrate_legacy_logs()
    log_app(
        "info",
        "started",
        version=APP_VERSION,
        python=platform.python_version(),
        platform=platform.platform(),
        log_dir=str(LOG_DIR),
        pid=os.getpid(),
    )
    threading.Thread(target=worker_check_versions, daemon=True).start()
    log_activity("app", "-", "info", f"{APP_NAME} {APP_VERSION} launched")
    restore_server_state()
    restore_loadtest()

    # At debug level pywebview logs the window address, which holds the token.
    os.environ.pop("PYWEBVIEW_LOG", None)
    try:
        import webview

        adopt_bundle_identity()
        STATE.window = webview.create_window(
            f"{APP_NAME} {APP_VERSION}",
            launch_url(),
            width=1360,
            height=900,
            min_size=(1080, 720),
            background_color="#0f1115",
        )
        webview.start()
    except Exception as exc:
        # A library error may quote the address it was loading.
        reason = str(exc).replace(API_TOKEN, "<token>")
        log_app("warning", "native window unavailable, using browser", error=reason)
        print(f"native window unavailable ({reason}); opening browser")
        STATE.window = None  # it may exist but never have started
        STATE.browser_mode = True
        open_in_browser(launch_url())
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        STATE.window = None
        shutdown_loadtest()  # also when a background API server is kept
        # A background server is meant to outlive the window; leave it running
        # and let the pidfile adopt it on the next launch.
        if STATE.server_background:
            log_app(
                "info", "window closed; background api server left running", pid=STATE.server_pid
            )
        else:
            stop_server()
        httpd.shutdown()
        log_app("info", "stopped", pid=os.getpid())


if __name__ == "__main__":
    main()
