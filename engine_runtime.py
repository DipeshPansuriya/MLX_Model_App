"""What every engine shares: child environment, server key, port ownership, install."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import secrets
import select
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

HOST = "127.0.0.1"
LSOF = "/usr/sbin/lsof"
KEY_PATTERN = re.compile(r"sk-optiq-[A-Za-z0-9_-]{16,}")
ALREADY_INSTALLED = "This engine is already installed. Remove it first to install it again."
# All the built-in MLX server and the load test runner get of the app's environment.
INHERITED = frozenset(
    [
        "PATH",
        "HOME",
        "TMPDIR",
        "USER",
        "LOGNAME",
        "LANG",
        "TZ",
        "__CF_USER_TEXT_ENCODING",
        "VIRTUAL_ENV",
        "HF_HOME",
        "HF_HUB_CACHE",
        "HUGGINGFACE_HUB_CACHE",
        "XDG_CACHE_HOME",
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "HF_HUB_DISABLE_TELEMETRY",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONUSERBASE",
        "PYTHONNOUSERSITE",
        "PYTHONUTF8",
        "PYTHONDONTWRITEBYTECODE",
    ]
)
INHERITED_PREFIXES = ("LC_", "MLX_STUDIO_")


class Refused(Exception):
    """Why an engine will not start or install, in words for the user."""


def minimal_environment(source=None) -> dict[str, str]:
    """The app's environment without keys, tokens or anything else not listed."""
    source = os.environ if source is None else source
    return {
        name: value
        for name, value in source.items()
        if name in INHERITED or name.startswith(INHERITED_PREFIXES)
    }


# --------------------------------------------------------------------------- #
# The server key
# --------------------------------------------------------------------------- #
def new_key() -> str:
    return "sk-optiq-" + secrets.token_urlsafe(32)


def write_key(path: Path, key: str) -> None:
    """Replace the key file; it is private from its first byte."""
    fresh = path.with_name(f"{path.name}.{secrets.token_hex(4)}")
    handle = os.open(fresh, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(key)
        os.replace(fresh, path)
    finally:
        fresh.unlink(missing_ok=True)


def redact(text: str) -> str:
    return KEY_PATTERN.sub("[key hidden]", text)


# --------------------------------------------------------------------------- #
# The port: who holds it, and does it enforce the key
# --------------------------------------------------------------------------- #
def listeners(port: int, run=subprocess.run) -> set[int] | None:
    """Every process listening on ``port`` at any address; None when unknown.

    macOS lets a wildcard or IPv6 socket sit beside one on 127.0.0.1, so the
    question names no address. Another user's sockets are not listed: see ``answers``.
    """
    try:
        done = run(
            [LSOF, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if done.returncode not in (0, 1):
            return None
        return {int(pid) for pid in done.stdout.split()}
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def listener_is(port: int, pid: int, run=subprocess.run) -> bool:
    """True only when ``pid`` holds every listener, so the key reaches nobody else."""
    return listeners(port, run=run) == {pid}


def answers(port: int) -> bool:
    """Whether anything accepts a connection on this Mac, IPv4 or IPv6."""
    for host in (HOST, "::1"):
        try:
            socket.create_connection((host, port), timeout=0.5).close()
            return True
        except OSError:
            pass
    return False


def port_problem(port: int, ours: int | None = None) -> str | None:
    """Why a server may not start on ``port``; ``ours`` is the one it replaces."""
    holders = listeners(port)
    if holders is None:
        return f"It cannot be checked who uses port {port}, so nothing was started."
    if holders - {ours} or (not holders and answers(port)):
        return f"Another program uses port {port}. Quit it, then start the server again."
    return None


def _status(port: int, method: str, path: str, key: str | None = None) -> int | None:
    conn = http.client.HTTPConnection(HOST, port, timeout=5)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body = None
    if method == "POST":
        body, headers["Content-Type"] = b"{}", "application/json"
    try:
        conn.request(method, path, body, headers)
        return conn.getresponse().status
    except (OSError, http.client.HTTPException):
        return None
    finally:
        conn.close()


def auth_state(port: int, key: str, anthropic: bool = False) -> str:
    """``ready``, ``starting``, or ``open``: it answered without the key, so stop it.

    The caller checks ``listener_is`` first: the last request carries the key.
    """
    routes = [("GET", "/v1/models"), ("POST", "/v1/chat/completions")]
    if anthropic:
        routes.append(("POST", "/v1/messages"))
    for method, path in routes:
        status = _status(port, method, path)
        if status is None:
            return "starting"
        if status != 401:
            return "open"
    return "ready" if _status(port, "GET", "/v1/models", key) == 200 else "starting"


def health(port: int) -> int | None:
    """mlx_lm's own /health: 200, or 503 once its model thread has failed."""
    return _status(port, "GET", "/health")


def warm_up(port: int, key: str | None, model: str, timeout: float) -> bool:
    """One tiny reply: the model really loads and answers. ``listener_is`` goes first."""
    body = {"model": model, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 1}
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    conn = http.client.HTTPConnection(HOST, port, timeout=timeout)
    try:
        conn.request("POST", "/v1/chat/completions", json.dumps(body), headers)
        # mlx_lm leaves a request unanswered when its model fails to load: watch for that.
        end = time.monotonic() + timeout
        while not key and not select.select([conn.sock], [], [], 1)[0]:
            if health(port) == 503 or time.monotonic() > end:
                return False
        return conn.getresponse().status == 200
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Install and remove, from a hash lock the app ships
# --------------------------------------------------------------------------- #
def file_sha256(path: Path) -> str:
    with open(path, "rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def refuse_links(app_dir: Path, folder: Path) -> None:
    """Refuse ``folder`` when it, or a folder between it and ``app_dir``, is a link."""
    for part in (folder, *folder.parents):
        if part == app_dir:
            return
        if part.is_symlink():
            raise Refused(f"{part} is a link. Delete the link, then try again.")


def run_step(command: list[str], env: dict, emit) -> int:
    with subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        env=env,
    ) as proc:
        for line in proc.stdout:
            emit(line.rstrip())
    return proc.returncode


def pip_failure(lines: list[str]) -> str:
    text = "\n".join(lines)
    if "DO NOT MATCH THE HASHES" in text:
        return "A downloaded file did not match its reviewed hash. Nothing from it is used."
    network = ("ConnectionError", "Max retries", "nodename nor servname", "timed out",
               "Temporary failure in name resolution", "No matching distribution")  # fmt: skip
    if any(sign in text for sign in network):
        return "No network, or PyPI could not be reached."
    return "pip failed; see the lines above."


def remove_folder(app_dir: Path, folder: Path) -> None:
    refuse_links(app_dir, folder)
    refuse_links(app_dir, folder / "venv")
    if folder.exists():
        shutil.rmtree(folder)  # links inside are removed, never followed


def redact_stream(source, sink) -> None:
    """Copy ``source`` to ``sink`` line by line, with keys hidden, until it ends."""
    while line := source.readline(1 << 20):
        sink.write(
            redact(line.decode("utf-8", "surrogateescape")).encode("utf-8", "surrogateescape")
        )
        sink.flush()


if __name__ == "__main__":  # the filter between a background server and its log
    redact_stream(sys.stdin.buffer, sys.stdout.buffer)
