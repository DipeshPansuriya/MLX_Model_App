"""Live server memory and writing speed (rows 894, 685): measured values only.

Standard library only; no file, process, network or log. The app hands in what it
has already read. Anything unreadable or unmeasured is None, never zero or a guess.
Speed comes only from the app's own chat replies and load tests.
"""

from __future__ import annotations

import math

# macOS kern.memorystatus_vm_pressure_level
PRESSURE_NAMES = {1: "normal", 2: "warning", 4: "critical"}


def _count(value, least: int = 0) -> int | None:
    return value if type(value) is int and value >= least else None


def _number(value) -> float | None:
    """A finite number of zero or more; for times."""
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    return value


def _rate(value) -> float | None:
    """A speed: nothing was measured if it is not above zero."""
    return _number(value) or None


def memory(probe, pid, ram_bytes) -> dict:
    """The Mac's memory and the server's, now.

    ``probe`` is ``mlx_loadtest.mac_probe``; ``pid`` the running server's, or None.
    ``mac_used_percent`` is the load test's own figure: the higher of the page count
    and macOS's own level. The server figures are the footprint of that one process.
    """
    pid = _count(pid, 1)
    sample = None
    for asked in (pid, None) if pid else (None,):
        try:
            sample = probe(asked)
            break
        except Exception:  # an unreadable server must not hide the Mac's figures
            pid = None
    percent = _rate(getattr(sample, "percent", None))
    return {
        "mac_total_bytes": _count(ram_bytes, 1),
        "mac_used_percent": round(percent, 1) if percent and percent <= 100 else None,
        "pressure": PRESSURE_NAMES.get(getattr(sample, "pressure", None)),
        "server_bytes": _count(sample.footprint, 1) if sample and pid else None,
        "server_peak_bytes": _count(sample.peak, 1) if sample and pid else None,
    }


def chat_speed(done, engine: str, pid: int, at: str) -> dict | None:
    """What to remember from a chat reply's ``done`` event; None if no speed was measured.

    Only ``done["stats"]`` is read: the event's outer ``tps`` counts pieces of text
    when the server sends no token count.
    """
    stats = done.get("stats") if isinstance(done, dict) else None
    if not isinstance(stats, dict) or (tps := _rate(stats.get("tps"))) is None:
        return None
    mode = stats.get("mode")
    return {
        "repo": done.get("repo"),
        "engine": engine,
        "pid": pid,
        "at": at,
        "write_tps": tps,
        "first_token_s": _number(stats.get("first_s")),
        "reply_tokens": _count(stats.get("reply_tokens")),
        "prompt_tokens": _count(stats.get("prompt_tokens")),
        "mode": mode if isinstance(mode, str) else None,
    }


def _passed_rows(run: dict) -> list[dict]:
    """The sizes of one load test that passed and have a measured speed."""
    rows = run.get("rows")
    found = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("status") != "passed":
            continue
        figures = {
            "read_tps": _rate(row.get("read_tps")),
            "write_tps": _rate(row.get("write_tps")),
            "first_token_s": _number(row.get("first_token_s")),
        }
        size = _count(row.get("size"), 1)
        if size and (figures["read_tps"] or figures["write_tps"]):
            found.append({"size": size, **figures})
    return found


def load_test_speed(runs, repo: str, engine: str) -> dict | None:
    """The newest measured load test of this model on this engine.

    ``runs`` are the saved runs, oldest first; one without ``engine`` is MLX. Each
    size is given as measured: nothing is averaged or carried between sizes.
    """
    for run in reversed(runs or []):
        if (
            isinstance(run, dict)
            and run.get("repo") == repo
            and run.get("engine", "mlx") == engine
            and (rows := _passed_rows(run))
        ):
            return {"time": run.get("time"), "stale": run.get("stale"), "rows": rows}
    return None


def speed(last_chat, runs, repo, engine, pid) -> dict:
    """``{"chat": ..., "test": ...}`` for the running server; the two are never mixed.

    The chat figure counts only for the very server process that produced it.
    """
    same = (
        isinstance(last_chat, dict)
        and pid is not None
        and (last_chat.get("repo"), last_chat.get("engine"), last_chat.get("pid"))
        == (repo, engine, pid)
    )
    return {
        "chat": dict(last_chat) if same else None,
        "test": load_test_speed(runs, repo, engine) if repo else None,
    }
