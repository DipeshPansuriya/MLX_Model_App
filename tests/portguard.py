"""The suite never touches the ports a real MLX Studio uses (8765 and 8080).

In this process, binding or connecting to them is refused, and so is starting the
real app or API server as a child. Each refusal is recorded, and a recorded one
fails the run even if the code under test swallowed the error. Children still
running when the run ends are killed and fail it too.
"""

from __future__ import annotations

import os
import re
import shlex
import socket
import subprocess
import weakref
from pathlib import Path

RESERVED_PORTS = frozenset({8765, 8080})
SHELLS = frozenset({"sh", "bash", "zsh", "dash"})
VIOLATIONS: list[str] = []
CHILDREN: weakref.WeakSet = weakref.WeakSet()


def _python_target(words):
    """``(is a module, name)`` an interpreter is told to run; None for ``-c`` or nothing."""
    items = iter(words)
    for word in items:
        if not word.startswith("-"):
            return False, word
        if word.startswith("--"):
            continue
        for at, letter in enumerate(word[1:], 2):  # flags may be joined: -um, -Xutf8
            if letter in "cmWX":  # these take the rest of the word, or the next one
                value = word[at:] or next(items, "")
                if letter == "c":
                    return None
                if letter == "m":
                    return True, value
                break
    return None


def _shell_launch(text, env) -> str | None:
    """``real_launch`` for each interpreter named in a shell command line."""
    for line in text.splitlines():
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        try:
            tokens = list(lexer)
        except ValueError:  # an unclosed quote: the shell reports it
            continue
        words: list[str] = []
        for token in [*tokens, ";"]:
            if token and set(token) <= set(lexer.punctuation_chars):
                for at in range(len(words)):
                    if why := real_launch(words[at:], env):
                        return why
                words = []
            else:
                words.append(token)
    return None


def real_launch(command, env=None, in_shell=False) -> str | None:
    """Why ``command`` would start the real app or server on a reserved port.

    A shell's ``-c`` text is read; what a script file starts cannot be seen.
    """
    if in_shell:
        text = command if isinstance(command, str) else next(iter(command), "")
        return _shell_launch(os.fsdecode(text), env)
    if not isinstance(command, (list, tuple)) or len(command) < 2:
        return None
    words = [os.fsdecode(word) for word in command]
    name = os.path.basename(words[0]).lower()
    if name in SHELLS:
        for at, word in enumerate(words[1:-1], 1):
            if re.fullmatch(r"-[a-z]*c[a-z]*", word):
                return _shell_launch(words[at + 1], env)
        return None
    # Only an interpreter runs them: xattr or cp on the same file starts nothing.
    if not name.startswith("python"):
        return None
    target = _python_target(words[1:])
    if not target:
        return None
    module, run = target
    server = run.startswith("mlx_lm") if module else run.endswith("mlx_guard.py")
    app = not module and os.path.basename(run) == "app.py"
    # The load test runner starts a real API server of its own.
    runner = not module and os.path.basename(run) == "mlx_loadtest.py"
    if not (server or app or runner):
        return None
    # A test's own stand-in for mlx_lm never listens. -I and -E ignore PYTHONPATH.
    stub = (env or {}).get("PYTHONPATH")
    flags = words[1 : words.index(run) if run in words else len(words)]
    ignored = any(re.fullmatch(r"-[A-Za-z]*[IE][A-Za-z]*", flag) for flag in flags)
    if stub and not ignored and (Path(stub) / "mlx_lm" / "server.py").is_file():
        return None
    port = words[words.index("--port") + 1] if "--port" in words[:-1] else None
    if server and port and port.isdigit() and int(port) not in RESERVED_PORTS:
        return None
    return f"a test tried to start {run} on port {port or 'default'}"


def _refuse(why: str):
    VIOLATIONS.append(why)
    raise OSError(why)


def install() -> None:
    real_init = subprocess.Popen.__init__

    def init(self, args, *rest, **kwargs):
        why = real_launch(args, kwargs.get("env"), kwargs.get("shell", False))
        if why:
            _refuse(why)
        real_init(self, args, *rest, **kwargs)
        CHILDREN.add(self)

    subprocess.Popen.__init__ = init

    for name in ("bind", "connect", "connect_ex"):
        real = getattr(socket.socket, name)

        def checked(self, address, *, _real=real, _name=name):
            if isinstance(address, tuple) and address[1] in RESERVED_PORTS:
                _refuse(f"a test tried to {_name} port {address[1]}")
            return _real(self, address)

        setattr(socket.socket, name, checked)


def kill_leftovers() -> list[str]:
    left = []
    for child in list(CHILDREN):
        if child.poll() is None:
            left.append(f"a child process outlived the run: pid {child.pid}")
            child.kill()
            child.wait(timeout=10)
    return left


def shut_down(app) -> None:
    """End an app module's server work, so no watcher thread starts one later."""
    if hasattr(app, "shutdown_loadtest"):
        app.shutdown_loadtest()
    app.STATE.cancel_pending_starts()  # a replaced server is not brought back
    app.STATE.server_stopping = True  # and a dead one is not acted on
