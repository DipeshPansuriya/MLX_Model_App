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
import importlib.util
import json
import math
import os
import platform
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_NAME = "MLX Studio"
APP_VERSION = "2.0.10"
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
LAUNCH_LOG = LOG_DIR / "launch.log"
SERVER_LOG = LOG_DIR / "server.log"
UI_DIR = Path(__file__).resolve().parent / "ui"
# The API server starts through this wrapper, which checks every request.
GUARD_SCRIPT = Path(__file__).resolve().parent / "mlx_guard.py"
# The .app keeps this next to app.py, in Contents/Resources.
APP_ICON = Path(__file__).resolve().parent / "AppIcon.icns"

# Rotation keeps the folder small enough to attach to a GitHub issue.
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUPS = 3
LOG_LOCK = threading.Lock()

HOST = "127.0.0.1"
PORT = 8765
SERVER_PORT = 8080
# mlx_lm cuts replies at 512 tokens when a client sends no max_tokens.
SERVER_MAX_TOKENS = 8192
# Two models at once (see mlx_guard.install_multi). The share of memory they may
# use is an assumption carried from the 18 GB "safe" figure on a 32 GB Mac.
SERVER_MAX_MODELS = 2
SERVER_RAM_FRACTION = 0.52
# How long a reading of the free memory is shown before it is taken again.
FREE_CACHE_SECONDS = 10
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
                        archive.write(path, arcname=f"logs/{path.name}")
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
        self.model = None
        self.tokenizer = None
        self.loaded_repo: str | None = None
        self.loading_repo: str | None = None
        self.load_error: str | None = None
        self.generating = False
        self.stop_event = threading.Event()  # replaced for every reply
        self.deleting: set[str] = set()
        self.download_cancels: dict[str, threading.Event] = {}
        self.server_lock = threading.RLock()  # one stop or start at a time
        self.server_pending: list[str] = []  # requested, not yet running
        self.server_stops = 0  # a queued start older than the last Stop is dropped
        self.server_proc: subprocess.Popen | None = None
        self.server_repo: str | None = None
        self.server_repos: list[str] = []  # the first is server_repo
        self.server_context: int | None = None  # tokens per model, for two
        self.free_read: tuple[float, int | None] | None = None  # see free_memory
        self.server_lines: list[str] = []
        self.server_background = False
        self.server_started_at: str | None = None
        self.server_last_error: str | None = None
        self.server_down_notice: str | None = None
        self.server_pid: int | None = None
        self.server_identity: str | None = None
        self.server_unguarded = False  # adopted, and running without mlx_guard.py
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
    def _release_locked(self) -> str | None:
        repo = self.loaded_repo
        self.model = None
        self.tokenizer = None
        self.loaded_repo = None
        self.load_error = None
        return repo

    # -- API server ------------------------------------------------------- #
    def cancel_pending_starts(self) -> None:
        with self.lock:
            self.server_stops += 1

    # -- packages --------------------------------------------------------- #
    def reserve_install(self, kind: str) -> bool:
        """Claim the single pip slot; False while an install is running."""
        with self.lock:
            if self.install["running"]:
                return False
            self.install = {"running": True, "kind": kind, "lines": []}
            return True

    # -- downloads -------------------------------------------------------- #
    def reserve_download(self, repo: str) -> threading.Event | None:
        """Claim the download slot for repo; None when it is already taken."""
        with self.lock:
            if repo in self.deleting or self.downloads.get(repo, {}).get("status") == "running":
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
        if self.loaded_repo == repo:
            return "unload the model first"
        if self.loading_repo == repo:
            return "the model is loading"
        serving = self.server_proc is not None or _pid_alive(self.server_pid)
        if repo in self.server_pending or (
            serving
            and repo
            in {
                self.server_repo,
                *self.server_repos,
                *_served_models(),
                *_resident_models(_read_resident(self.server_pid)),
            }
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
            loaded = {
                "repo": self.loaded_repo,
                "loading": self.loading_repo,
                "error": self.load_error,
            }
        view = _resident_view(self.server_pid)
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
            "loaded": loaded,
            "server": {
                "running": self.server_proc is not None or _pid_alive(self.server_pid),
                "repo": self.server_repo,
                "repos": list(self.server_repos),
                **view,
                **_server_limits(list(self.server_repos), view),
                "port": SERVER_PORT,
                "endpoint": f"http://{HOST}:{SERVER_PORT}/v1",
                "tool_support": tool_support(),
                "lines": self.server_lines[-200:],
                "background": self.server_background,
                "started_at": self.server_started_at,
                "last_error": self.server_last_error,
                "down_notice": self.server_down_notice,
                "pid": self.server_pid,
                "unguarded": self.server_unguarded and _pid_alive(self.server_pid),
            },
            "downloads": downloads,
            "install": {
                "running": self.install["running"],
                "kind": self.install["kind"],
                "lines": self.install["lines"][-400:],
            },
            "generating": self.generating,
        }


def clear_mlx_cache() -> None:
    try:
        import mlx.core as mx

        mx.clear_cache()
    except Exception as exc:
        print(f"mlx cache release skipped: {exc}")


STATE = State()


# --------------------------------------------------------------------------- #
# Chat streaming
# --------------------------------------------------------------------------- #
def stream_chat(messages: list[dict], stop: threading.Event):
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
            if stop.is_set():
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
            "stopped": stop.is_set(),
            "repo": repo,
        }
        yield ("done", meta)
    except Exception as exc:
        yield ("error", str(exc))


# --------------------------------------------------------------------------- #
# Background workers
# --------------------------------------------------------------------------- #
def worker_load(repo: str) -> None:
    with STATE.lock:
        STATE.loading_repo = repo
        STATE.load_error = None
        # One model slot: free the old one before the new one takes memory.
        previous = STATE._release_locked()
    if previous:
        clear_mlx_cache()
        log_activity("model", previous, "info", "unloaded from memory")
    # Before the weights are read, so the server cannot take the same room.
    _chat_model_changed()
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
    except Exception as exc:
        with STATE.lock:
            STATE.loading_repo = None
            STATE.load_error = str(exc)[:500]
        log_activity("model", repo, "failed", str(exc)[:300])
        log_app("error", "model load failed", repo=repo, error=str(exc)[:500])
        _chat_model_changed()


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


def _served_file() -> Path:
    """Models the running API server has accepted requests for (see mlx_guard)."""
    return APP_DIR / "server.models"


def _served_models() -> set[str]:
    try:
        return set(_served_file().read_text("utf-8").split())
    except (OSError, ValueError):
        return set()


def _resident_file() -> Path:
    """What a two-model API server holds in memory (see mlx_guard.status_writer)."""
    return APP_DIR / "server.resident.json"


def _budget_file() -> Path:
    """The memory a two-model server may use now (read by mlx_guard at each load)."""
    return APP_DIR / "server.budget"


def _read_resident(pid: int | None) -> dict:
    """The resident file, only when the server now running wrote it."""
    try:
        data = json.loads(_resident_file().read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and pid and data.get("pid") == pid else {}


def _resident_models(data: dict) -> list[str]:
    """Loaded models, the current one first."""
    names = data.get("resident")
    return [n for n in names if isinstance(n, str)] if isinstance(names, list) else []


def _resident_view(pid: int | None) -> dict:
    data = _read_resident(pid)
    events = data.get("events")
    sizes = ("budget_bytes", "active_bytes", "peak_bytes")
    oversize = data.get("oversize")
    measured = all(isinstance(data.get(key), int) for key in sizes)
    tokens = data.get("context_tokens")
    return {
        "context_tokens": tokens if isinstance(tokens, int) and tokens > 0 else None,
        "resident": _resident_models(data),
        # Loaded although it does not fit the budget.
        "oversize": [n for n in oversize if isinstance(n, str)]
        if isinstance(oversize, list)
        else [],
        "memory": {**{key: data[key] for key in sizes}, "measured": True} if measured else None,
        "events": [e for e in events if isinstance(e, dict)][-5:]
        if isinstance(events, list)
        else [],
        "multi_off": str(data.get("reason")) if data.get("multi") is False else None,
        "preload_done": data.get("preload_done") is True,
    }


GUARD_IMPORT_LOCK = threading.Lock()


@functools.lru_cache(maxsize=1)
def _guard_module():
    """mlx_guard.py as a module: the app and the server share one memory formula."""
    spec = importlib.util.spec_from_file_location("mlx_studio_guard", GUARD_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # A __pycache__ inside the app bundle would break its signature. The switch is
    # process wide, so two first calls must not restore it under each other.
    with GUARD_IMPORT_LOCK:
        before, sys.dont_write_bytecode = sys.dont_write_bytecode, True
        try:
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = before
    return module


def _snapshot_dir(repo: str) -> Path:
    # repo is validated against installed_models() in _second_model_problem()
    # and in the /api/load handler. The path is therefore safe.
    # repo comes from installed_models() which strips NUL bytes and validates.
    # nosec: path traversal is prevented by validation in _second_model_problem()
    allowed = _complete_models()
    if repo not in allowed:
        return CACHE_DIR
    # Explicitly validate path components to satisfy static analysis.
    if repo.count("/") != 1:
        return CACHE_DIR
    org, name = repo.split("/", 1)
    if not org or not name:
        return CACHE_DIR

    def _safe_entry(org: str, name: str) -> Path:
        """Build a cache entry path from validated org/name components.
        Called only after validation that org and name are non-empty and
        contain no path separators. nosec B108."""
        return CACHE_DIR / f"models--{org}--{name}"

    entry = _safe_entry(org, name)
    try:
        commit = (entry / "refs" / "main").read_text("utf-8").strip()
    except (OSError, ValueError):
        commit = ""
    if commit:
        return entry / "snapshots" / commit
    try:
        found = sorted((entry / "snapshots").glob("*"), key=lambda p: p.stat().st_mtime)
    except (OSError, ValueError):  # ValueError: a name with a NUL byte
        found = []
    return found[-1] if found else entry


def _server_budget() -> int:
    """Memory the server's models may use; the app's own chat model comes off it."""
    budget = int(SERVER_RAM_FRACTION * (hardware().get("ram_bytes") or 0))
    chat = STATE.loaded_repo or STATE.loading_repo
    if chat:
        budget -= _guard_module().weights_bytes(_snapshot_dir(chat))
    return max(budget, 0)


def _publish_server_budget() -> None:
    """Tell a two-model server what the Chat model leaves it, from its next load."""
    temp = _budget_file().with_suffix(".budget.tmp")
    # 1, not 0, when nothing is left: the guard ignores a figure below 1.
    left = str(max(_server_budget(), 1))
    with contextlib.suppress(OSError):
        temp.unlink(missing_ok=True)
        # A new private file: a link planted under this name is never written through.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        with os.fdopen(os.open(temp, flags, 0o600), "w", encoding="utf-8") as handle:
            handle.write(left)
        temp.replace(_budget_file())


def _chat_model_changed() -> None:
    if len(STATE.server_repos) > 1:
        _publish_server_budget()


def free_memory(fresh: bool = False) -> int | None:
    """Memory free now, or None (see mlx_guard.free_bytes). A decision reads it afresh."""
    last = STATE.free_read
    if fresh or last is None or time.monotonic() - last[0] >= FREE_CACHE_SECONDS:
        last = STATE.free_read = (time.monotonic(), _guard_module().free_bytes())
    return last[1]


def _server_models(repos: list[str]) -> list[tuple[str, dict, int]]:
    """``(repo, config, weight bytes)`` for each model."""
    guard = _guard_module()
    snapshots = [(repo, _snapshot_dir(repo)) for repo in repos]
    return [(repo, guard.read_config(s), guard.weights_bytes(s)) for repo, s in snapshots]


def _kv_per_token(models: list) -> int:
    """The larger cache cost of one token: it sizes the context and the prompt cache."""
    guard = _guard_module()
    return max(
        guard.kv_bytes_per_token(config) or guard.UNKNOWN_KV_PER_TOKEN for _, config, _ in models
    )


def _server_need(models: list, tokens: int, cache: int) -> dict:
    """What the server takes holding ``models`` with ``tokens`` of context each."""
    guard = _guard_module()
    weights = sum(size for _, _, size in models)
    reply = max(guard.kv_reserve(config, tokens) for _, config, _ in models)
    return {
        "weights_bytes": weights,
        "reply_bytes": reply,
        "cache_bytes": cache,
        "margin_bytes": guard.MEMORY_MARGIN,
        "total_bytes": weights + reply + cache + guard.MEMORY_MARGIN,
    }


def _running_needs(asked: list[str], view: dict) -> list[dict]:
    """The running two-model server's need: the pair asked for, then the two loaded now."""
    guard = _guard_module()
    # A server that does not report its context was started with the ceiling.
    tokens = view["context_tokens"] or STATE.server_context or guard.KV_TOKENS
    cache = guard.CACHED_CONVERSATIONS * _kv_per_token(_server_models(asked)) * tokens
    pairs = [asked, view["resident"]] if len(view["resident"]) > 1 else [asked]
    return [_server_need(_server_models(pair), tokens, cache) for pair in pairs]


def _server_limits(asked: list[str], view: dict) -> dict:
    """For the snapshot: a two-model server's context, and whether free memory ran low."""
    limits = {"context_tokens": None, "memory_low": None}
    if len(asked) < 2 or view["multi_off"] is not None:
        return limits
    limits["context_tokens"] = view["context_tokens"] or STATE.server_context
    # Without a measurement there is nothing to compare the free memory with, and a
    # half-done preload would look like memory other apps took.
    free = free_memory() if view["preload_done"] and view["memory"] else None
    if free is None:
        return limits
    guard = _guard_module()
    need = max(figures["total_bytes"] for figures in _running_needs(asked, view))
    # The running process has taken its overhead already, so that is not still to come.
    # ADR-010 deviation 1: subtract PROCESS_OVERHEAD to fire later (accepted).
    left = need - guard.PROCESS_OVERHEAD - view["memory"]["active_bytes"]
    short = left - (free - guard.FREE_RESERVE)
    if short > 0:
        limits["memory_low"] = {"free_bytes": free, "short_by_bytes": short}
    return limits


def chat_load_problem(repo: str) -> str | None:
    """Why this Chat model cannot be loaded beside a two-model API server, or None."""
    with STATE.lock:
        pid, asked = STATE.server_pid, list(STATE.server_repos)
        running = STATE.server_proc is not None or _pid_alive(pid)
    view = _resident_view(pid)
    if not running or len(asked) < 2 or view["multi_off"] is not None:
        return None
    share = int(SERVER_RAM_FRACTION * (hardware().get("ram_bytes") or 0))
    chat = _guard_module().weights_bytes(_snapshot_dir(repo))
    # The pair asked for, which can come back, and the two loaded now.
    needs = _running_needs(asked, view)
    need = max(figures["total_bytes"] for figures in needs)
    server = need
    if view["memory"]:
        now = needs[-1]
        used = view["memory"]["active_bytes"] + now["reply_bytes"] + now["margin_bytes"]
        server = max(server, used)
    if not share or not chat:
        return None
    if server + chat > share:
        return (
            "not enough memory: the API server keeps two models (about "
            f"{server / 1e9:.1f} GB) and this model adds {chat / 1e9:.1f} GB, over the "
            f"{share / 1e9:.1f} GB allowed. Stop the API server or choose a smaller model."
        )
    free = free_memory(fresh=True)
    if free is None:
        return None
    guard = _guard_module()
    held = view["memory"]["active_bytes"] if view["memory"] else 0
    more = max(0, need - held)
    if chat + more <= free - guard.FREE_RESERVE:
        return None
    return (
        f"not enough free memory: {free / 1e9:.1f} GB free, and this model "
        f"({chat / 1e9:.1f} GB), what the API server may still take ({more / 1e9:.1f} GB) and "
        f"{guard.FREE_RESERVE / 1e9:.1f} GB for macOS need more. Close apps, stop the API "
        "server or choose a smaller model."
    )


def _running_server_bytes() -> int:
    """What a running server holds: a start replaces it, so that memory comes back."""
    memory = _resident_view(STATE.server_pid)["memory"]
    return memory["active_bytes"] if memory else 0


def server_memory_estimate(repos: list[str], fresh: bool = False) -> dict:
    """Calculated memory for the API server holding ``repos`` together.

    For two, the context per model follows the smaller of the fixed share of RAM and
    what is free now. One model is only described: nothing is read or refused.
    """
    guard = _guard_module()
    static = _server_budget()
    pair = len(repos) > 1
    free = free_memory(fresh) if pair else None
    held = _running_server_bytes() if free is not None else 0
    budget = static
    if free is not None:
        budget = max(0, min(static, held + free - guard.FREE_RESERVE))
    models = _server_models(repos)
    kv = _kv_per_token(models)
    weights = sum(size for _, _, size in models)
    context = guard.context_tokens(budget, weights, kv) if pair else guard.KV_TOKENS
    # A refused pair is described at the smallest context it would be started with.
    tokens = max(context, guard.MIN_CONTEXT)
    cache = guard.CACHED_CONVERSATIONS * kv * tokens if pair else 0
    need = _server_need(models, tokens, cache)
    items = [
        guard.Resident(repo, size, guard.kv_reserve(config, tokens))
        for repo, config, size in models
    ]
    # The guard's own rule, with the earlier model and a full prompt cache in memory.
    earlier = weights - items[-1].weights_bytes + cache
    plan = guard.admission_plan(items[:-1], items[-1], earlier, 0, budget, len(items))
    fits = context >= guard.MIN_CONTEXT and not (plan.evict or plan.alone)
    refused = None
    if pair and not fits:
        least = need["total_bytes"]
        if free is not None and static >= least:
            # Said as free memory to reach, so it compares with the free figure.
            # "needed" is the total free memory required: need(8K) + FREE_RESERVE - held.
            wanted = least + guard.FREE_RESERVE - held
            refused = (
                f"Not enough free memory for two models: {math.floor(free / 1e9 * 10) / 10:.1f} GB free, "
                f"{math.ceil(wanted / 1e9 * 10) / 10:.1f} GB needed for 8K each. "
                "Close other apps or start one model."
            )
        else:
            refused = (
                f"Not enough memory for two models: {static / 1e9:.1f} GB allowed for the "
                f"API server, {math.ceil(least / 1e9 * 10) / 10:.1f} GB needed for 8K each. "
                "Choose smaller models or start one model."
            )
    free_for_one = max(budget - weights - guard.MEMORY_MARGIN, 0)
    described = []
    for repo, config, size in models:
        per_token, limit = guard.kv_bytes_per_token(config), guard.context_limit(config)
        if pair:
            safe = min(context, limit or context)
        elif per_token:
            safe = int(free_for_one // (guard.REPLY_FACTOR * per_token))
            safe = min(safe, limit or safe)
        else:
            safe = None
        described.append(
            {
                "repo": repo,
                "weights_bytes": size,
                "safe_context": safe,
                "context_limit": limit,
            }
        )
    return {
        "models": described,
        **need,
        "budget_bytes": budget,
        "free_bytes": free,
        "held_bytes": held,
        "context_tokens": context,
        "cache_cap_bytes": cache,
        "fits": fits,
        "refused": refused,
        "free_is_limit": free is not None and held + free - guard.FREE_RESERVE < static,
    }


def _second_model_problem(repo: str, also) -> str | None:
    """Why the ``also`` field of a server request cannot be used, or None."""
    if not isinstance(also, list) or not all(isinstance(name, str) for name in also):
        return "also must be a list of model names"
    if len(also) >= SERVER_MAX_MODELS:
        return "only one more model can be kept loaded"
    if not also:
        return None
    if also[0] == repo:
        return "the second model must be different from the first"
    installed = {m["repo"] for m in installed_models() if m["complete"]}
    missing = [name for name in (repo, *also) if name not in installed]
    return f"{missing[0]} is not installed completely" if missing else None


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


def _is_our_server(pid: int | None, repo: str | None, started: str | None, port=None) -> bool:
    """True only when ``pid`` is still the API server we launched.

    A pid is reused after a crash or reboot, so both the command line and, when
    recorded, the process start time must match before it is adopted or signalled.
    """
    if not repo or not _pid_alive(pid):
        return False
    command = f"{_ps_field(pid, 'command')} "
    # The second form is a background server started by a build without the guard.
    launchers = (f"/{GUARD_SCRIPT.name}", " -m mlx_lm server")
    if not any(f"{start} --model {repo} --host " in command for start in launchers):
        return False
    if f" --port {port or SERVER_PORT} " not in command:
        return False
    return not started or _ps_field(pid, "lstart") == started


def _is_guarded(pid: int) -> bool:
    """True only when the command line shows the server runs behind the guard."""
    return f"/{GUARD_SCRIPT.name} --model " in _ps_field(pid, "command")


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
        "port": SERVER_PORT,
        "started": _ps_field(pid, "lstart"),
    }
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
    for stale in (path, _resident_file(), _budget_file()):
        with contextlib.suppress(OSError):
            stale.unlink(missing_ok=True)


def _watch_server(pid: int, repo: str, proc: subprocess.Popen | None) -> None:
    """Notice when the API server dies and record why, so the UI can warn.

    A detached child stays a zombie until it is reaped, and os.kill(pid, 0)
    still succeeds on a zombie, so poll the Popen handle when we have one and
    fall back to the pid probe only for a server adopted from a pidfile.
    """
    checks = 0
    while True:
        if proc is not None:
            if proc.poll() is not None:
                break
        elif not _pid_alive(pid):
            break
        elif checks % 15 == 14 and not _is_our_server(pid, repo, STATE.server_identity):
            break  # the pid now belongs to another process
        checks += 1
        time.sleep(2)
    if STATE.server_stopping:
        return
    code = None
    if proc is not None:
        with contextlib.suppress(Exception):
            code = proc.returncode
    with STATE.lock:
        # A restart may already have installed a successor; only the watcher for
        # the current pid is allowed to declare the server down.
        if STATE.server_pid != pid:
            return
        names = " and ".join(
            name.rsplit("/", maxsplit=1)[-1] for name in STATE.server_repos or [repo]
        )
        STATE.server_proc = None
        STATE.server_pid = None
        STATE.server_repo = None
        STATE.server_repos = []
        STATE.server_background = False
        STATE.server_started_at = None
        STATE.server_unguarded = False
        STATE.server_last_error = (
            f"exited with status {code}" if code is not None else "process gone"
        )
        if code == -signal.SIGKILL:
            STATE.server_last_error += ", killed, possibly out of memory"
        STATE.server_down_notice = (
            f"API server for {names} stopped unexpectedly ({STATE.server_last_error})"
        )
    _clear_server_pidfile(only_pid=pid)
    log_activity("server", repo, "failed", f"stopped unexpectedly ({STATE.server_last_error})")
    log_app("error", "api server died", repo=repo, pid=pid, exit_code=code)


def worker_server_start(
    repo: str, background: bool = False, ticket: int | None = None, also: list[str] | None = None
) -> None:
    proc = None
    try:
        with STATE.server_lock:
            # Stop can win the lock while this start is still queued.
            if ticket is None or ticket == STATE.server_stops:
                proc = _launch_server(repo, background, *([also] if also else []))
            else:
                log_activity("server", repo, "info", "start cancelled by Stop")
    finally:
        with STATE.lock:
            for name in (repo, *(also or [])):
                if name in STATE.server_pending:
                    STATE.server_pending.remove(name)
    if proc is None or background:
        return

    if proc.stdout is None:  # pragma: no cover - PIPE guarantees a stream
        STATE.server_lines.append("error: no stdout stream")
        return
    for line in proc.stdout:
        STATE.server_lines.append(line.rstrip())
        STATE.server_lines = STATE.server_lines[-400:]
    proc.wait()
    with contextlib.suppress(Exception):
        STATE.server_last_error = f"exited with status {proc.returncode}"


def _launch_server(
    repo: str, background: bool, also: list[str] | None = None
) -> subprocess.Popen | None:
    """Replace any running API server. The caller holds ``STATE.server_lock``."""
    stop_server()
    STATE.server_lines = []
    STATE.server_down_notice = None
    STATE.server_last_error = None
    repos = [repo, *(also or [])]
    # Read again now that the old server has given its memory back.
    limits = server_memory_estimate(repos, fresh=True) if len(repos) > 1 else None
    STATE.server_context = None
    if limits and not limits["fits"]:
        STATE.server_last_error = "not started: not enough memory"
        STATE.server_down_notice = limits["refused"]
        STATE.server_lines.append(limits["refused"])
        log_activity("server", repo, "failed", limits["refused"])
        return None
    cmd = [
        sys.executable,
        str(GUARD_SCRIPT),
        "--model",
        repo,
        "--host",
        HOST,
        "--port",
        str(SERVER_PORT),
        "--max-tokens",
        str(SERVER_MAX_TOKENS),
        # mlx_lm defaults to "*", which lets any web page read replies.
        "--allowed-origins",
        f"http://{HOST}:{PORT},http://localhost:{PORT}",
    ]
    if limits:
        STATE.server_context = limits["context_tokens"]
        # Last on the line: the identity check reads from --model onwards.
        cmd += [
            "--prompt-cache-bytes",
            str(limits["cache_cap_bytes"]),
            "--studio-also-model",
            repos[1],
            "--studio-max-models",
            str(SERVER_MAX_MODELS),
            "--studio-budget-bytes",
            str(max(limits["budget_bytes"], 1)),
            "--studio-kv-tokens",
            str(limits["context_tokens"]),
        ]
    # A request must never make the server download a model.
    env = {
        **os.environ,
        "HF_HUB_OFFLINE": "1",
        "MLX_STUDIO_SERVED_FILE": str(_served_file()),
        "MLX_STUDIO_RESIDENT_FILE": str(_resident_file()),
        "MLX_STUDIO_BUDGET_FILE": str(_budget_file()),
    }
    for stale in (_served_file(), _resident_file(), _budget_file()):
        with contextlib.suppress(OSError):
            stale.unlink(missing_ok=True)
    if len(repos) > 1:
        _publish_server_budget()
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
            proc = subprocess.Popen(
                cmd,
                stdout=handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )
        else:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
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
        f"listening on {HOST}:{SERVER_PORT}" + (" (background)" if background else ""),
    )
    log_app(
        "info",
        "api server started",
        repo=repo,
        pid=proc.pid,
        background=background,
        port=SERVER_PORT,
    )
    threading.Thread(target=_watch_server, args=(proc.pid, repo, proc), daemon=True).start()
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
        elif _is_our_server(pid, repo, STATE.server_identity):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGTERM)
            for _ in range(50):
                if not _pid_alive(pid):
                    break
                time.sleep(0.1)
            if _is_our_server(pid, repo, STATE.server_identity):
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
            STATE.server_unguarded = False
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
    if not isinstance(pid, int) or not _is_our_server(
        pid, data.get("repo"), data.get("started"), data.get("port")
    ):
        log_app("info", "dropped a stale api server pidfile", pid=pid)
        _clear_server_pidfile()
        return
    STATE.server_pid = pid
    STATE.server_identity = data.get("started")
    STATE.server_repo = data.get("repo")
    repos = data.get("repos")
    known = isinstance(repos, list) and repos and all(isinstance(n, str) for n in repos)
    STATE.server_repos = repos if known else [data.get("repo")]
    STATE.server_background = bool(data.get("background"))
    STATE.server_started_at = data.get("started_at")
    STATE.server_stopping = False
    _chat_model_changed()  # no Chat model yet: drops what the last session took off
    # A build without the guard may have left it. Restarting is the user's call.
    STATE.server_unguarded = not _is_guarded(pid)
    if STATE.server_unguarded:
        log_app(
            "warning",
            "adopted an api server running without the request guard",
            pid=pid,
            repo=STATE.server_repo,
        )
        log_activity(
            "server",
            STATE.server_repo,
            "info",
            "running without the request guard; restart it on the API Server page",
        )
    else:
        log_app("info", "adopted running api server", pid=pid, repo=STATE.server_repo)
    threading.Thread(
        target=_watch_server, args=(pid, STATE.server_repo or "?", None), daemon=True
    ).start()


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
# POST routes that act on a model named by the "repo" field.
REPO_ROUTES = frozenset(
    {
        "/api/load",
        "/api/download",
        "/api/download/cancel",
        "/api/delete",
        "/api/reveal",
        "/api/server/start",
        "/api/server/estimate",
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
        # Every value counts, as in mlx_guard.py: a second header must not slip by.
        own = {f"http://{host}" for host in allowed}
        if any(origin not in own for origin in self.headers.get_all("Origin") or []):
            return "forbidden origin"
        return None

    def _post_refusal(self, path: str) -> tuple[int, str] | None:
        reason = self._foreign_request()
        if reason:
            return 403, reason
        # Exactly one, as in mlx_guard.py.
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

        if path == "/api/load":
            if STATE.loaded_repo != repo:
                # Any other name would be read as a folder path, or downloaded.
                if repo not in _complete_models():
                    self._json({"error": f"{repo} is not installed completely"}, 400)
                    return
                problem = chat_load_problem(repo)
                if problem:
                    log_activity("model", repo, "failed", problem)
                    self._json({"error": problem}, 409)
                    return
            with STATE.lock:
                already = STATE.loaded_repo == repo
                if already:
                    busy = None
                elif STATE.loading_repo:
                    busy = "another model is loading"
                elif STATE.generating:
                    busy = "wait for the reply to finish, or stop it"
                elif repo in STATE.deleting:
                    busy = "the model is being deleted"
                else:
                    busy = None
                    STATE.loading_repo = repo  # reserved before the worker runs
                    STATE.load_error = None
            if already:
                self._json({"ok": True, "already": True})
            elif busy:
                self._json({"error": busy}, 409)
            else:
                threading.Thread(target=worker_load, args=(repo,), daemon=True).start()
                self._json({"ok": True, "loading": repo})
            return

        if path == "/api/unload":
            with STATE.lock:
                generating = STATE.generating
                previous = None if generating else STATE._release_locked()
            if generating:
                self._json({"error": "wait for the reply to finish, or stop it"}, 409)
                return
            if previous:
                clear_mlx_cache()
                log_activity("model", previous, "info", "unloaded from memory")
                _chat_model_changed()
            self._json({"ok": True})
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
            self._stream_chat(messages)
            return

        if path == "/api/chat/stop":
            with STATE.lock:
                STATE.stop_event.set()
            self._json({"ok": True})
            return

        if path == "/api/download":
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

        if path in ("/api/server/start", "/api/server/estimate"):
            also = [] if body.get("also") is None else body["also"]
            problem = _second_model_problem(repo, also)
            if problem:
                self._json({"error": problem}, 400)
                return
            repos = [repo, *also]
            if path == "/api/server/estimate":
                # A pair is checked above; one name alone is checked here.
                if not also and repo not in _complete_models():
                    self._json({"error": f"{repo} is not installed completely"}, 400)
                    return
                self._json(server_memory_estimate(repos))
                return
            estimate = server_memory_estimate(repos, fresh=True) if also else None
            if estimate and not estimate["fits"]:
                self._json({"error": estimate["refused"], "estimate": estimate}, 409)
                return
            background = bool(body.get("background"))
            with STATE.lock:
                deleting = any(name in STATE.deleting for name in repos)
                if not deleting:
                    STATE.server_pending.extend(repos)
                    ticket = STATE.server_stops
            if deleting:
                self._json({"error": "the model is being deleted"}, 409)
                return
            threading.Thread(
                target=worker_server_start, args=(repo, background, ticket, also), daemon=True
            ).start()
            self._json({"ok": True, "background": background})
            return

        if path == "/api/server/stop":
            # worker_server_start logs the stop once its output pump exits.
            STATE.cancel_pending_starts()
            stop_server()
            self._json({"ok": True})
            return

        if path == "/api/server/notice/clear":
            STATE.server_down_notice = None
            self._json({"ok": True})
            return

        if path == "/api/install":
            kind = body.get("kind")
            kind = kind if isinstance(kind, str) and kind else "install"
            if not STATE.reserve_install(kind):
                self._json({"error": "install already running"}, 409)
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
    def _stream_chat(self, messages: list[dict]) -> None:
        stop = threading.Event()
        with STATE.lock:
            if STATE.generating:
                busy = "a reply is already being generated"
            elif STATE.loading_repo:
                busy = "a model is loading"
            elif STATE.model is None:
                busy = "no model loaded"
            else:
                busy = None
                STATE.generating = True
                STATE.stop_event = stop
                repo = STATE.loaded_repo or "?"
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
            for kind, payload in stream_chat(messages, stop):
                if kind == "token":
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
            with STATE.lock:
                STATE.generating = False
            try:
                self.close_connection = True
            except Exception as exc:
                print(f"close_connection skipped: {exc}")


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
