#!/usr/bin/env python3
"""The Load Test runner (ADR-012): its own API server, timed and measured.

Started by MLX Studio. stdin: one job line, then ``cancel``; its end means the app is
gone. stdout: one JSON event per line. No prompt or reply text is ever reported.

Memory is measured and reported, with no percentage limit. The server is still killed
when macOS reports critical memory pressure or swap grows by more than 0.5 GB.
"""

from __future__ import annotations

import contextlib
import ctypes
import functools
import http.client
import json
import os
import random
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import NamedTuple

# Safety figures, deliberately not settings. At the 80% limit: gate 75, kill 77.
SAMPLE_SECONDS = 0.1
MAX_GAP_SECONDS = 1.0  # a longer gap between samples: the Mac slept or stalled
SWAP_GROWTH_BYTES = 500_000_000
PRESSURE_CRITICAL = 4
STALL_SECONDS = 120  # no byte of a reply
LONG_SIZE = 32_768  # a larger size takes long: it gets the longest time limit
LONG_TIMEOUT_SECONDS = 3600  # the top of the "timeout_seconds" setting
STOP_GRACE_SECONDS = 5
GONE_SECONDS = 30
PORT_ATTEMPTS = 3
RESERVED_PORTS = (8765, 8080)
PROMPT_SPARE_TOKENS = 64
FOLLOW_UP_TEXT = "Name the last function above in one short line."
WRITE_MIN_TOKENS = 16
LINE_BYTES = 1024 * 1024


class Sample(NamedTuple):
    percent: float  # of RAM in use on the whole Mac
    footprint: int  # bytes of the server process
    peak: int  # the most it has held
    pressure: int  # macOS memory pressure level: 1, 2 or 4
    swap: int  # bytes of swap in use


# Memory -------------------------------------------------------------------- #
class _VMStats(ctypes.Structure):
    # <mach/vm_statistics.h> struct vm_statistics64, up to its first revision.
    _fields_ = (
        [(n, ctypes.c_uint32) for n in ("free", "active", "inactive", "wire")]
        + [(f"a{n}", ctypes.c_uint64) for n in range(9)]
        + [(n, ctypes.c_uint32) for n in ("purgeable", "speculative")]
        + [(f"b{n}", ctypes.c_uint64) for n in range(4)]
        + [(n, ctypes.c_uint32) for n in ("compressor", "throttled", "external", "internal")]
        + [("uncompressed", ctypes.c_uint64)]
    )


class _Swap(ctypes.Structure):  # <sys/sysctl.h> struct xsw_usage
    _fields_ = [(n, ctypes.c_uint64) for n in ("total", "avail", "used")] + [
        ("pagesize", ctypes.c_uint32),
        ("encrypted", ctypes.c_uint32),
    ]


class _Usage(ctypes.Structure):  # <sys/resource.h> struct rusage_info_v4
    _fields_ = [("uuid", ctypes.c_uint8 * 16), ("v", ctypes.c_uint64 * 35)]


FOOTPRINT, LIFETIME_PEAK = 7, 28  # ri_phys_footprint, ri_lifetime_max_phys_footprint


@functools.cache
def _libc():
    libc = ctypes.CDLL(None)
    libc.mach_host_self.restype = ctypes.c_uint32
    return libc, libc.mach_host_self()  # each call would hold one more reference


def mac_probe(pid: int | None) -> Sample:
    """The Mac's memory now, and the process ``pid``'s. OSError when it cannot be read."""
    if sys.platform != "darwin":
        raise OSError("memory can only be read on macOS")
    libc, host = _libc()

    def sysctl(name: str, value):
        size = ctypes.c_size_t(ctypes.sizeof(value))
        if libc.sysctlbyname(name.encode(), ctypes.byref(value), ctypes.byref(size), None, 0):
            raise OSError(f"{name} cannot be read")
        return value

    stats = _VMStats()
    count = ctypes.c_uint32(ctypes.sizeof(stats) // 4)
    if libc.host_statistics64(host, 4, ctypes.byref(stats), ctypes.byref(count)):
        raise OSError("the memory statistics cannot be read")
    pages = stats.internal - stats.purgeable + stats.wire + stats.compressor
    ram = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    level = sysctl("kern.memorystatus_level", ctypes.c_int()).value
    percent = max(100 * pages * os.sysconf("SC_PAGE_SIZE") / ram, 100 - level)
    footprint = peak = 0
    if pid:
        usage = _Usage()
        if libc.proc_pid_rusage(pid, 4, ctypes.byref(usage)):
            raise OSError("the server's memory cannot be read")
        footprint, peak = usage.v[FOOTPRINT], usage.v[LIFETIME_PEAK]
    return Sample(
        percent,
        footprint,
        max(peak, footprint),
        sysctl("kern.memorystatus_vm_pressure_level", ctypes.c_int()).value,
        sysctl("vm.swapusage", _Swap()).used,
    )


def trip(sample: Sample, base_swap: int) -> str | None:
    """Why this reading means the server must be killed now, else None."""
    if sample.pressure >= PRESSURE_CRITICAL:
        return "macOS reported critical memory pressure"
    if sample.swap - base_swap > SWAP_GROWTH_BYTES:
        return "swap use grew by more than 0.5 GB"
    return None


def kill_group(proc) -> None:
    """SIGKILL the server and everything in its own session."""
    with contextlib.suppress(OSError):
        if os.getpgid(proc.pid) == proc.pid:
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        proc.kill()


class Monitor:
    """Samples memory while one server lives, and ends it when a rule says so."""

    def __init__(
        self, proc, probe, clock, *, base_swap, deadline, cancel, abandon,
        killer=kill_group, report=None,
    ) -> None:  # fmt: skip
        self.proc, self.probe, self.clock, self.killer = proc, probe, clock, killer
        self.base_swap, self.deadline = base_swap, deadline
        self.cancel, self.abandon, self.report = cancel, abandon, report
        self.reason: tuple[str, str] | None = None
        self.peak_percent = 0.0
        self.lifetime_peak = self.step_peak = 0
        self.in_reply = False
        self.last_byte = self.last_tick = None
        self.reported = None
        self.hard_kill_at = None
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True)

    def begin_step(self) -> None:
        self.step_peak = 0
        self.last_byte, self.in_reply = self.clock(), True

    def note(self, sample: Sample, peak_before: int | None = None) -> None:
        """Count one reading; a lifetime peak that rose belongs to this step."""
        peak_before = self.lifetime_peak if peak_before is None else peak_before
        self.peak_percent = max(self.peak_percent, sample.percent)
        rose = sample.peak if sample.peak > peak_before else 0
        self.step_peak = max(self.step_peak, sample.footprint, rose)
        self.lifetime_peak = max(self.lifetime_peak, sample.peak, sample.footprint)

    def beat(self) -> None:
        self.last_byte = self.clock()

    def end(self, status: str, text: str) -> None:
        """Record the first reason, then kill: a reader must find it already set."""
        self.reason = self.reason or (status, text)
        self.killer(self.proc)

    def tick(self) -> None:
        now = self.clock()
        gap = 0 if self.last_tick is None else now - self.last_tick
        self.last_tick = now
        if self.proc.poll() is not None:
            return
        if self.abandon.is_set():
            return self.end("cancelled", "MLX Studio closed")
        if gap > MAX_GAP_SECONDS:
            return self.end("interrupted", "memory was not watched for over a second")
        try:
            sample = self.probe(self.proc.pid)
        except Exception:
            if self.proc.poll() is None:
                self.end("memory", "memory could not be read")
            return
        self.note(sample)
        if why := trip(sample, self.base_swap):
            return self.end("memory", why)
        if self.cancel.is_set():
            if self.hard_kill_at is None:
                self.reason = self.reason or ("cancelled", "cancelled by you")
                self.hard_kill_at = now + STOP_GRACE_SECONDS
                self.proc.terminate()
            elif now >= self.hard_kill_at:
                self.killer(self.proc)
            return
        if now > self.deadline or (self.in_reply and now - self.last_byte > STALL_SECONDS):
            return self.end("failed", "timeout")
        if self.report and (self.reported is None or now - self.reported >= 1):
            self.reported = now
            self.report(sample.percent)

    def _watch(self) -> None:
        while not self.done.is_set():
            try:
                self.tick()
            except Exception:  # a watcher that fails must not leave the server running
                self.end("failed", "the memory watcher failed")
            self.done.wait(SAMPLE_SECONDS)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.done.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)


# Prompts and replies ------------------------------------------------------- #
def make_prompt(tokens: int, run_id: str, label: str, count) -> str:
    """Seeded filler code of at most ``tokens``; no file of the user is read.

    The run id and ``label`` come first, so no earlier prompt cache is reused.
    """
    seed = random.Random(f"{run_id}/{label}")  # noqa: S311 - filler, not a secret
    lines = [f"# load test {run_id} {label}"]

    def block(number: int) -> str:
        a, b = seed.randrange(10**6), seed.randrange(10**6)
        return (
            f"def block_{number}(value):\n    total = value * {a} + {b}\n"
            f"    return total % {a + 7}\n"
        )

    total, number = count(lines[0]), 0
    while True:
        text = block(number)
        size = count(text)
        if total + size > tokens:
            break
        lines.append(text)
        total, number = total + max(size, 1), number + 1
    # Counted whole: joined text can differ from the sum of its parts.
    # Dropped in one go: a recount for every line would take hours near 1M tokens.
    while len(lines) > 1 and (over := count("\n".join(lines)) - tokens) > 0:
        del lines[max(len(lines) - max(over // max(size, 1), 1), 1) :]
    return "\n".join(lines)


class RequestError(Exception):
    pass


def ask(port: int, body: dict, clock, beat, progress, headers: dict | None = None) -> dict:
    """One streamed chat request: its text, timings and usage."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=STALL_SECONDS + 30)
    try:
        sent = clock()
        headers = {"Content-Type": "application/json", **(headers or {})}
        conn.request("POST", "/v1/chat/completions", json.dumps(body), headers)
        reply = conn.getresponse()
        if reply.status != 200:
            raw = reply.read(64 * 1024).decode("utf-8", "replace")
            with contextlib.suppress(ValueError, TypeError, KeyError):
                raw = str(json.loads(raw)["error"])
            raise RequestError(f"the server answered {reply.status}: {raw[:300]}")
        first = last = usage = None
        pieces, done = [], False
        while line := reply.readline(LINE_BYTES):
            beat()
            text = line.decode("utf-8", "replace").strip()
            if text.startswith(": keepalive"):
                at, _, total = text.rpartition(" ")[2].partition("/")
                if at.isdigit() and total.isdigit():
                    progress(int(at), int(total))
            if not text.startswith("data:"):
                continue
            if text[5:].strip() == "[DONE]":
                done = True
                break
            data = json.loads(text[5:])
            usage = data.get("usage") or usage
            for choice in data.get("choices") or []:
                delta = choice.get("delta") or {}
                piece = delta.get("content") or delta.get("reasoning") or ""
                piece = piece or delta.get("reasoning_content") or ""
                if piece or delta.get("tool_calls"):
                    last = clock()
                    first = last if first is None else first
                    pieces.append(piece)
        if not done or not usage or first is None:
            raise RequestError("the reply was cut short")
        return {
            "text": "".join(pieces),
            "first": first - sent,
            "writing": last - first,
            "usage": usage,
        }
    except (OSError, http.client.HTTPException, ValueError, AttributeError) as exc:
        raise RequestError(f"the request failed ({type(exc).__name__})") from None
    finally:
        conn.close()


def mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


# The run ------------------------------------------------------------------- #
def free_port(reserved=RESERVED_PORTS) -> int:
    while True:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port not in reserved:
            return port


def spawn_server(command, env, log):
    return subprocess.Popen(
        command,
        stdin=subprocess.PIPE,  # the server exits when this pipe closes
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=env,
    )


class Stop(Exception):
    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status, self.reason = status, reason


class Runner:
    def __init__(
        self, job, emit, *, spawn, probe, clock, count_tokens, cancel=None, abandon=None
    ) -> None:
        self.job, self.emit = job, emit
        self.spawn, self.probe, self.clock, self.count = spawn, probe, clock, count_tokens
        self.cancel = cancel or threading.Event()
        self.abandon = abandon or threading.Event()
        self.base_swap = 0
        self.taken = False  # another program holds the server's port
        self.seconds = job.get("timeout_seconds")  # of the size being run

    def time_limit(self, size: int) -> int:
        return LONG_TIMEOUT_SECONDS if size > LONG_SIZE else self.job["timeout_seconds"]

    def run(self) -> tuple[str, str]:
        try:
            self.base_swap = self.probe(None).swap
        except Exception:
            return "failed", "memory could not be read"
        sizes = sorted(self.job["sizes"])
        ended = None
        for index, size in enumerate(sizes, 1):
            row = {"size": size, "status": "skipped", "reason": "", "steps_done": 0}
            if ended and ended[0] == "cancelled":
                row["status"], row["reason"] = ended
            elif ended:
                row["reason"] = "a smaller size did not pass"
            else:
                self.where = {"size": size, "index": index, "of": len(sizes)}
                try:
                    self.run_size(size, row)
                    row["status"] = "passed"
                except Stop as stop:
                    row["status"], row["reason"] = stop.status, stop.reason
                    status = "memory" if stop.status in ("skipped", "stopped") else stop.status
                    ended = (status, stop.reason)
            self.emit({"event": "row", "row": row})
        return ended or ("done", "")

    def check(self, monitor=None) -> None:
        if monitor and monitor.reason:
            status, reason = monitor.reason
            raise Stop("stopped" if status == "memory" else status, reason)
        if self.cancel.is_set() or self.abandon.is_set():
            raise Stop("cancelled", "cancelled by you")

    def read(self, pid) -> Sample:
        try:
            return self.probe(pid)
        except Exception:
            raise Stop("failed", "memory could not be read") from None

    def progress(self, **fields) -> None:
        self.emit({"event": "progress", **self.where, **fields})

    def run_size(self, size: int, row: dict) -> None:
        self.check()
        log_path = Path(self.job["workdir"]) / "server.log"
        monitor = proc = None
        self.seconds = self.time_limit(size)
        deadline = self.clock() + self.seconds
        try:
            for attempt in range(1, PORT_ATTEMPTS + 1):
                self.progress(state="loading", step="loading the model")
                port = free_port()
                command = list(self.job["command"])
                command[command.index("--port") + 1] = str(port)
                with open(log_path, "ab") as log:
                    proc = self.spawn(command, self.server_environment(), log)
                self.emit({"event": "server", "pid": proc.pid, "port": port})
                monitor = Monitor(
                    proc, self.probe, self.clock, base_swap=self.base_swap,
                    deadline=deadline, cancel=self.cancel, abandon=self.abandon,
                    report=lambda percent: self.progress(memory_percent=round(percent, 1)),
                )  # fmt: skip
                monitor.start()
                self.taken = False
                if self.wait_ready(proc, port, monitor):
                    break
                monitor.stop()
                self.end_server(proc)
                if attempt == PORT_ATTEMPTS:
                    raise Stop(
                        "failed",
                        "another program answered on the test server's port"
                        if self.taken
                        else "the server stopped while starting",
                    )
            self.steps(size, row, proc, port, monitor)
        finally:
            if monitor:
                monitor.stop()
            if proc:
                self.end_server(proc)
            if monitor:
                row["peak_percent"] = round(monitor.peak_percent, 1)
                row["server_peak_bytes"] = monitor.lifetime_peak
        # Passed only if the peak also stayed under the kill line.

    # A job with "key_env" is a keyed server of another engine; the key is only ever
    # in the environment.
    def key(self) -> str | None:
        name = self.job.get("key_env")
        return os.environ[name] if name else None

    def headers(self) -> dict | None:
        key = self.key()
        return {"Authorization": f"Bearer {key}"} if key else None

    def server_environment(self) -> dict:
        if not self.job.get("key_env"):
            return dict(os.environ)
        return {**self.job["server_env"], self.job["key_env"]: self.key()}

    def wait_ready(self, proc, port: int, monitor) -> bool:
        """True once this server, alone on its port, has loaded its model and answered."""
        import engine_runtime as runtime

        while proc.poll() is None:
            self.check(monitor)
            holders = runtime.listeners(port)
            if holders and holders != {proc.pid}:
                self.taken = True
                return False
            state = None
            if holders and self.job.get("key_env"):
                state = runtime.auth_state(port, self.key())
                if state == "open":
                    raise Stop("failed", "the server answered without the key")
            elif holders:
                health = runtime.health(port)
                if health == 503:
                    raise Stop("failed", "the model could not be loaded")
                state = "ready" if health == 200 else None
            if state == "ready" and runtime.listener_is(port, proc.pid):
                model, wait = self.job["request_model"], self.seconds
                if not runtime.warm_up(port, self.key(), model, wait):
                    self.check(monitor)
                    raise Stop("failed", "the model could not be loaded")
                return True
            time.sleep(SAMPLE_SECONDS)
        self.check(monitor)
        return False

    def steps(self, size, row, proc, port, monitor) -> None:
        reply_tokens, rounds = self.job["reply_tokens"], self.job["rounds"]
        reading, writing, cold_first, follow_first, prompts = [], [], [], [], []

        def figures():
            row.update(
                prompt_tokens=max(prompts, default=None),
                read_tps=mean(reading),
                write_tps=mean(writing),
                first_token_s=mean(cold_first),
                follow_up_first_token_s=round(max(follow_first), 3) if follow_first else None,
            )

        # At the model's own limit the prompts stop short, so the last reply still fits.
        limit = self.job.get("context_limit")
        top = min(size, limit - reply_tokens) if limit else size
        thinking = self.job.get("thinking")
        for number in range(1, rounds + 1):
            first = max(top - reply_tokens - PROMPT_SPARE_TOKENS, 1)
            label = f"{size}-{number}"
            prompt = make_prompt(first, self.job["run_id"], label, self.count)
            messages = [{"role": "user", "content": prompt}]
            for step in ("cold", "follow-up"):
                self.check(monitor)
                name = f"{step} {number} of {rounds}"
                self.progress(state="running", step=name)
                before = self.read(proc.pid)
                monitor.note(before)
                monitor.begin_step()
                body = {
                    "model": self.job["request_model"],
                    "messages": messages,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "max_tokens": reply_tokens,
                    "temperature": 0,
                }
                if thinking is not None:
                    body["chat_template_kwargs"] = {"enable_thinking": thinking}
                keyed = self.headers()
                if keyed:
                    import engine_runtime as runtime

                    if not runtime.listener_is(port, proc.pid):
                        raise Stop("failed", "another program listens on the test port")
                try:
                    got = ask(
                        port, body, self.clock, monitor.beat,
                        lambda done, total, name=name: self.progress(
                            state="running", step=name, done=done, total=total
                        ),
                        *((keyed,) if keyed else ()),
                    )  # fmt: skip
                except RequestError as exc:
                    self.check(monitor)
                    raise Stop("failed", str(exc)) from None
                finally:
                    monitor.in_reply = False
                self.check(monitor)
                # Read once more: a short step can end between two samples.
                monitor.note(self.read(proc.pid), before.peak)
                self.check(monitor)
                usage = got["usage"]
                count = usage.get("prompt_tokens") or 0
                cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
                written = usage.get("completion_tokens") or 0
                prompts.append(count)
                # Another engine may drop older text instead of refusing a long prompt.
                if keyed and step == "cold" and count < first * 0.9:
                    raise Stop("skipped", "the engine dropped part of the prompt")
                if step == "cold":
                    cold_first.append(got["first"])
                    if got["first"] > 0:
                        reading.append((count - cached) / got["first"])
                    messages = [
                        *messages,
                        {"role": "assistant", "content": got["text"] or "ok"},
                        {"role": "user", "content": FOLLOW_UP_TEXT},
                    ]
                else:
                    follow_first.append(got["first"])
                if written >= WRITE_MIN_TOKENS and got["writing"] > 0:
                    writing.append((written - 1) / got["writing"])
                row["steps_done"] += 1
                figures()

    def end_server(self, proc) -> None:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=0 if self.abandon.is_set() else STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                kill_group(proc)
        try:
            proc.wait(timeout=GONE_SECONDS)
        except subprocess.TimeoutExpired:
            raise Stop("failed", "the old server did not stop") from None
        if proc.stdin:
            with contextlib.suppress(OSError):
                proc.stdin.close()


def load_counter(job: dict):
    """The model's own cached tokenizer, offline; no weights are read."""
    from mlx_lm.tokenizer_utils import load

    tokenizer = load(Path(job["snapshot"]))
    return lambda text: len(tokenizer.encode(text))


def main(*, spawn=spawn_server, probe=mac_probe, load_counter=load_counter) -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    cancel, abandon = threading.Event(), threading.Event()
    lock = threading.Lock()

    def emit(event: dict) -> None:
        with lock:
            try:
                sys.stdout.write(json.dumps(event) + "\n")
                sys.stdout.flush()
            except (OSError, ValueError):
                abandon.set()  # nobody is listening

    # Read with os.read: a thread blocked in sys.stdin cannot be shut down cleanly.
    held = bytearray()

    def next_line() -> str | None:
        while b"\n" not in held:
            chunk = os.read(0, 65536)
            if not chunk:
                return None
            held.extend(chunk)
        line, _, rest = bytes(held).partition(b"\n")
        held[:] = rest
        return line.decode("utf-8", "replace")

    first = next_line()
    if first is None:
        return
    job = json.loads(first)

    def listen() -> None:
        while (line := next_line()) is not None:
            if line.strip() == "cancel":
                cancel.set()
        abandon.set()

    threading.Thread(target=listen, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: abandon.set())
    try:
        count = load_counter(job)
    except Exception as exc:
        reason = f"the tokenizer cannot be loaded ({type(exc).__name__})"
        emit({"event": "done", "status": "failed", "reason": reason})
        return
    runner = Runner(
        job, emit, spawn=spawn, probe=probe, clock=time.monotonic, count_tokens=count,
        cancel=cancel, abandon=abandon,
    )  # fmt: skip
    try:
        status, reason = runner.run()
    except Exception as exc:
        status, reason = "failed", f"the test failed ({type(exc).__name__}: {exc})"[:300]
    emit({"event": "done", "status": status, "reason": reason})


if __name__ == "__main__":
    main()
