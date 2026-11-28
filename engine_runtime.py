"""What every engine shares: child environment, server key, port ownership, install."""

from __future__ import annotations

import contextlib
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
import threading
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


def reasoning_flags(choice: str | None) -> list[str]:
    """The server flag for "reasoning" ``on`` or ``off``; nothing when unset."""
    if choice not in ("on", "off"):
        return []
    value = "true" if choice == "on" else "false"
    return ["--chat-template-args", f'{{"enable_thinking":{value}}}']


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
# --------------------------------------------------------------------------- #
# Test Connection: one short reply and one tool call
# --------------------------------------------------------------------------- #
TEST_REPLY_TOKENS = 512
TEST_REPLY_BYTES = 256 * 1024
# Offered to the model, never run. ``unit`` is optional, to see if null is sent.
TEST_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name."},
                "unit": {
                    "type": "string",
                    "enum": ["celsius", "fahrenheit"],
                    "description": "Optional. Leave out if not asked for.",
                },
            },
            "required": ["city"],
        },
    },
}
UNREADABLE = "The reply could not be read."


def _result(status: str, detail: str) -> dict[str, str]:
    return {"status": status, "detail": detail}


def _ask(port: int, body: dict, headers, timeout: float) -> tuple[int | None, bytes | str, str]:
    """``(HTTP status, reply, key sent)``; no status: the reply is why there is none.

    ``headers()`` is called once connected and may refuse: then nothing is sent.
    ``timeout`` covers the whole exchange, so a trickling server cannot hold it.
    """
    conn = http.client.HTTPConnection(HOST, port, timeout=timeout)
    expired = threading.Event()
    late = f"No answer within {timeout:g} seconds."
    timer = None
    try:
        try:
            conn.connect()
        except OSError:
            return None, "The server could not be reached.", ""
        sock = conn.sock
        conn.auto_open = 0  # a closed connection is not opened again, unchecked
        extra = headers()
        key = extra.get("Authorization", "").removeprefix("Bearer ")

        def cut() -> None:
            expired.set()
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)

        timer = threading.Timer(timeout, cut)
        timer.daemon = True
        timer.start()
        try:
            conn.request(
                "POST",
                "/v1/chat/completions",
                json.dumps(body).encode("utf-8"),
                {"Content-Type": "application/json", **extra},
            )
            response = conn.getresponse()
            raw = response.read(TEST_REPLY_BYTES + 1)
        except TimeoutError:
            return None, late, key
        except (OSError, http.client.HTTPException):
            why = late if expired.is_set() else "The connection closed before the reply ended."
            return None, why, key
        if expired.is_set():
            return None, late, key
        return response.status, raw, key
    finally:
        if timer:
            timer.cancel()
        conn.close()


def _message(raw: bytes) -> dict | None:
    if len(raw) > TEST_REPLY_BYTES:
        return None
    try:
        message = json.loads(raw.decode("utf-8"))["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError, RecursionError):
        return None
    return message if isinstance(message, dict) else None


def _only_thinking(message: dict) -> bool:
    return bool(message.get("reasoning") or message.get("reasoning_content"))


def _judge_reply(message: dict) -> dict[str, str]:
    text = message.get("content")
    if isinstance(text, str) and text.strip():
        return _result("pass", "The model answered.")
    if _only_thinking(message):
        return _result(
            "warn",
            f"The server answered, but thinking used all {TEST_REPLY_TOKENS} tokens "
            "before any answer.",
        )
    return _result("fail", "The server answered with an empty reply.")


def _judge_tool(message: dict) -> dict[str, str]:
    calls = message.get("tool_calls")
    name = TEST_TOOL["function"]["name"]
    if not calls:
        text = message.get("content")
        text = text if isinstance(text, str) else ""
        if name in text or "<tool_call" in text:
            return _result(
                "fail",
                "The model wrote the tool call as text, so a coding tool cannot run it.",
            )
        if not text.strip() and _only_thinking(message):
            return _result("fail", "No tool call: thinking used all the tokens of the test.")
        return _result("fail", "The model answered in text and did not call the tool.")
    try:
        function = calls[0]["function"]
        called, written = function["name"], function["arguments"]
    except (KeyError, IndexError, TypeError):
        return _result("fail", "The tool call could not be read.")
    if called != name:
        return _result("fail", "The model called a tool that was not offered.")
    try:
        arguments = json.loads(written)
        if not isinstance(arguments, dict):
            raise ValueError("not an object")
    except (ValueError, TypeError, RecursionError):
        return _result("fail", "The tool call's arguments are not valid JSON.")
    city = arguments.get("city")
    if not (isinstance(city, str) and city.strip()):
        return _result("fail", "The tool call arrived without the required value (city).")
    if empty := [key for key, value in arguments.items() if value is None]:
        names = ", ".join(empty)[:80]
        return _result(
            "warn",
            f"The tool call arrived, but the model sent null for: {names}. Some tools "
            "reject this, and MLX Studio does not change replies.",
        )
    return _result(
        "pass",
        "The tool call arrived in the expected form. One pass does not prove it always will.",
    )


def check_connection(port: int, model: str, headers, timeout: float) -> dict[str, dict]:
    """A short reply and a tool call from the server on ``port``, judged in plain words.

    ``headers()`` runs before each request and raises ``Refused`` when nothing may
    be sent. No key is part of the answer.
    """
    checks = (
        ("reply", "Reply with one word: ok", {}, _judge_reply),
        (
            "tool",
            "What is the weather in Paris? Use the get_weather tool.",
            {"tools": [TEST_TOOL]},
            _judge_tool,
        ),
    )
    results = {}
    reachable = True
    for name, prompt, extra, judge in checks:
        if not reachable:
            results[name] = _result("skipped", "Not tried: the first request got no answer.")
            continue
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": TEST_REPLY_TOKENS,
            "temperature": 0,
            "stream": False,
            **extra,
        }
        status, raw, key = _ask(port, body, headers, timeout)
        if status is None:
            result, reachable = _result("fail", raw), False
        elif status != 200:
            said = raw.decode("utf-8", "replace")
            if key:  # hidden before it is cut short
                said = said.replace(key, "[key hidden]")
            said = redact(said)[:300]
            result = _result("fail", f"The server refused the request (HTTP {status}): {said}")
        elif (message := _message(raw)) is None:
            result = _result("fail", UNREADABLE)
        else:
            result = judge(message)
        if key:
            result["detail"] = result["detail"].replace(key, "[key hidden]")
        results[name] = result
    return results


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
