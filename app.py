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
import json
import os
import platform
import re
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
APP_VERSION = "2.0.3"
APP_UPDATE_URL = os.environ.get("MLX_STUDIO_UPDATE_URL", "").strip()

HF_ORG = "mlx-community"
CACHE_DIR = Path.home() / ".cache" / "huggingface" / "hub"
APP_DIR = Path.home() / ".mlx_studio"
LOG_DIR = APP_DIR / "logs"
ACTIVITY_LOG = LOG_DIR / "activity.log"
APP_LOG = LOG_DIR / "app.log"
LAUNCH_LOG = LOG_DIR / "launch.log"
SERVER_LOG = LOG_DIR / "server.log"
UI_DIR = Path(__file__).resolve().parent / "ui"

# Rotation keeps the folder small enough to attach to a GitHub issue.
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUPS = 3

HOST = "127.0.0.1"
PORT = 8765
SERVER_PORT = 8080

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
# The logs record prompts, so keep the whole tree owner-only.
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

    Prompts live in activity.log; the bundle is written by the user and never
    uploaded anywhere automatically.
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
    },
    {
        "repo": "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit",
        "size_gb": 4.3,
        "params": "7B",
        "category": "Code",
        "use": "Code generation and refactors",
    },
    {
        "repo": "mlx-community/DeepSeek-R1-Distill-Qwen-7B-4bit",
        "size_gb": 4.3,
        "params": "7B",
        "category": "Reasoning",
        "use": "Step-by-step reasoning",
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
    installed = {m["repo"] for m in installed_models()}
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
        self.stop_flag = False
        self.server_proc: subprocess.Popen | None = None
        self.server_repo: str | None = None
        self.server_lines: list[str] = []
        self.server_background = False
        self.server_started_at: str | None = None
        self.server_last_error: str | None = None
        self.server_down_notice: str | None = None
        self.server_pid: int | None = None
        self.server_stopping = False
        self.window = None
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
        except Exception as exc:
            print(f"mlx cache release skipped: {exc}")
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
                "running": self.server_proc is not None or _pid_alive(self.server_pid),
                "repo": self.server_repo,
                "port": SERVER_PORT,
                "endpoint": f"http://{HOST}:{SERVER_PORT}/v1",
                "lines": self.server_lines[-200:],
                "background": self.server_background,
                "started_at": self.server_started_at,
                "last_error": self.server_last_error,
                "down_notice": self.server_down_notice,
                "pid": self.server_pid,
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
    except Exception as exc:
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
    except Exception as exc:
        with STATE.lock:
            STATE.loading_repo = None
            STATE.load_error = str(exc)[:500]
        log_activity("model", repo, "failed", str(exc)[:300])
        log_app("error", "model load failed", repo=repo, error=str(exc)[:500])


def worker_download(repo: str) -> None:
    STATE.downloads[repo] = {"repo": repo, "status": "running", "detail": "starting", "pct": 0}
    log_activity("download", repo, "running", "download started")
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(repo_id=repo, cache_dir=str(Path.home() / ".cache" / "huggingface"))
        STATE.downloads[repo] = {
            "repo": repo,
            "status": "success",
            "detail": "complete",
            "pct": 100,
        }
        log_activity("download", repo, "success", "download complete")
    except Exception as exc:
        STATE.downloads[repo] = {
            "repo": repo,
            "status": "failed",
            "detail": str(exc)[:200],
            "pct": 0,
        }
        log_activity("download", repo, "failed", str(exc)[:300])
        log_app("error", "download failed", repo=repo, error=str(exc)[:500])


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
        STATE.app_release = {
            "version": data.get("version"),
            "url": data.get("url"),
            "notes": data.get("notes") or "",
            "sha256": data.get("sha256"),
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


def _write_server_pidfile(pid: int, repo: str, background: bool) -> None:
    payload = {
        "pid": pid,
        "repo": repo,
        "background": background,
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "port": SERVER_PORT,
    }
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


def _watch_server(pid: int, repo: str, proc: subprocess.Popen | None) -> None:
    """Notice when the API server dies and record why, so the UI can warn.

    A detached child stays a zombie until it is reaped, and os.kill(pid, 0)
    still succeeds on a zombie, so poll the Popen handle when we have one and
    fall back to the pid probe only for a server adopted from a pidfile.
    """
    while True:
        if proc is not None:
            if proc.poll() is not None:
                break
        elif not _pid_alive(pid):
            break
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
        STATE.server_proc = None
        STATE.server_pid = None
        STATE.server_repo = None
        STATE.server_background = False
        STATE.server_started_at = None
        STATE.server_last_error = (
            f"exited with status {code}" if code is not None else "process gone"
        )
        STATE.server_down_notice = (
            f"API server for {repo.rsplit('/', maxsplit=1)[-1]} stopped unexpectedly "
            f"({STATE.server_last_error})"
        )
    _clear_server_pidfile(only_pid=pid)
    log_activity("server", repo, "failed", f"stopped unexpectedly ({STATE.server_last_error})")
    log_app("error", "api server died", repo=repo, pid=pid, exit_code=code)


def worker_server_start(repo: str, background: bool = False) -> None:
    stop_server()
    STATE.server_lines = []
    STATE.server_down_notice = None
    STATE.server_last_error = None
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
        if background:
            # Detached: survives the app window closing, output goes to a file
            # so the log folder still explains what happened.
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
            )
        else:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
            )
    except Exception as exc:
        STATE.server_lines.append(f"failed to start: {exc}")
        STATE.server_last_error = str(exc)[:300]
        log_app("error", "api server failed to start", repo=repo, error=str(exc)[:500])
        return

    STATE.server_proc = proc
    STATE.server_pid = proc.pid
    STATE.server_repo = repo
    STATE.server_background = background
    STATE.server_started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    STATE.server_stopping = False
    _write_server_pidfile(proc.pid, repo, background)
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

    if background:
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


def stop_server() -> None:
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
        elif pid and _pid_alive(pid):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGTERM)
            for _ in range(50):
                if not _pid_alive(pid):
                    break
                time.sleep(0.1)
            if _pid_alive(pid):
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGKILL)
    finally:
        with STATE.lock:
            STATE.server_proc = None
            STATE.server_pid = None
            STATE.server_repo = None
            STATE.server_background = False
            STATE.server_started_at = None
            STATE.server_down_notice = None
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
    if not isinstance(pid, int) or not _pid_alive(pid):
        _clear_server_pidfile()
        return
    STATE.server_pid = pid
    STATE.server_repo = data.get("repo")
    STATE.server_background = bool(data.get("background"))
    STATE.server_started_at = data.get("started_at")
    STATE.server_stopping = False
    log_app("info", "adopted running api server", pid=pid, repo=STATE.server_repo)
    threading.Thread(
        target=_watch_server, args=(pid, STATE.server_repo or "?", None), daemon=True
    ).start()


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
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
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
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
        except Exception:
            return {}

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
        body = self._body()

        if path == "/api/focus":
            # A second launch asks this instance to come forward instead of
            # starting a rival server on the same port.
            self._json({"ok": focus_window()})
            return

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
            background = bool(body.get("background"))
            if not repo:
                self._json({"error": "repo required"}, 400)
                return
            threading.Thread(
                target=worker_server_start, args=(repo, background), daemon=True
            ).start()
            self._json({"ok": True, "background": background})
            return

        if path == "/api/server/stop":
            # worker_server_start logs the stop once its output pump exits.
            stop_server()
            self._json({"ok": True})
            return

        if path == "/api/server/notice/clear":
            STATE.server_down_notice = None
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
            except Exception as exc:
                print(f"close_connection skipped: {exc}")


def focus_window() -> bool:
    """Bring the existing window to the front. Safe to call from a handler thread.

    pywebview's cocoa backend routes show/restore through AppHelper.callAfter, so
    this does not touch AppKit off the main thread.
    """
    window = STATE.window
    if window is None:
        return False
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

    try:
        import webview

        STATE.window = webview.create_window(
            f"{APP_NAME} {APP_VERSION}",
            url,
            width=1360,
            height=900,
            min_size=(1080, 720),
            background_color="#0f1115",
        )
        webview.start()
    except Exception as exc:
        log_app("warning", "native window unavailable, using browser", error=str(exc))
        print(f"native window unavailable ({exc}); opening browser")
        import webbrowser

        webbrowser.open(url)
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
