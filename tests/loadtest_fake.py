"""Stand-ins for the load test: a small SSE server and a runner that uses it.

Run as a script: ``loadtest_fake.py serve <port> <mode>`` or
``loadtest_fake.py runner <mode>``. Nothing here imports mlx or loads a model.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socketserver
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPLY_WORD = "zebra"
LOG_LINE = "fake server log line"


class Server(ThreadingHTTPServer):
    """No host name lookup at bind (as ``app.LocalServer``): a CI Mac is slow at it."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def load_module(name):
    spec = importlib.util.spec_from_file_location(f"fake_{Path(name).stem}", ROOT / name)
    module = importlib.util.module_from_spec(spec)
    before, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = before
    return module


def load_runner():
    return load_module("mlx_loadtest.py")


def count_words(text):
    return len(text.split())


SERVERS: dict = {}  # port -> the fake server meant to listen on it


def listeners(port, run=None):
    """Stands in for ``engine_runtime.listeners``, which asks macOS's own lsof.

    A CI runner has none at that path, or one too slow for a test's deadline. A
    port that answers belongs to its fake server, or else to this other program.
    """
    import engine_runtime

    if not engine_runtime.answers(port):
        return set()
    proc = SERVERS.get(port)
    return {proc.pid} if proc and proc.poll() is None else {os.getpid()}


def spawner(mode, spawned):
    """``spawn(command, env, log)`` that starts the fake server on the asked port."""

    def spawn(command, env, log):
        port = command[command.index("--port") + 1]
        proc = subprocess.Popen(
            [sys.executable, __file__, "serve", port, mode],
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
        proc.args = list(command)
        if mode != "no-listen":
            SERVERS[int(port)] = proc
        else:
            SERVERS.pop(int(port), None)
        spawned.append(proc)
        return proc

    return spawn


def serve(port, mode):
    def follow_parent():
        while os.read(0, 4096):
            pass
        os._exit(0)

    threading.Thread(target=follow_parent, daemon=True).start()
    print(LOG_LINE, flush=True)
    full = mode.startswith("full-")  # every reply is as long as asked for
    mode = mode.removeprefix("full-")
    if mode == "die":
        sys.exit(1)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            # /health as mlx_lm answers it: 503 once the model has failed to load.
            self.send_response({"load-failed": 503, "slow-load": 404}.get(mode, 200))
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            warm_up = body.get("max_tokens") == 1 and not body.get("stream")
            if warm_up:
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if mode in ("too-long", "refused"):
                error = {"message": "maximum context length is 1"}
                if mode == "refused":
                    error["code"] = "context_length_exceeded"
                data = json.dumps({"error": error})
                self.send_response(400)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data.encode())
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            prompt = sum(count_words(m["content"]) for m in body["messages"])
            replies = 4 if mode == "short" else 20
            if full:
                replies = body["max_tokens"]

            def send(text):
                self.wfile.write(text.encode())
                self.wfile.flush()

            def chunk(**fields):
                send(f"data: {json.dumps(fields)}\n\n")

            send(f": keepalive {prompt // 2}/{prompt}\n\n")
            time.sleep(0.02)
            for _ in range(replies):
                chunk(choices=[{"delta": {"content": f"{REPLY_WORD} "}}])
                time.sleep(0.5 if mode == "slow-reply" else 0.002)
            if mode == "cut":
                return
            chunk(choices=[{"delta": {}, "finish_reason": "length"}])
            chunk(
                choices=[],
                usage={
                    "prompt_tokens": prompt,
                    "completion_tokens": replies,
                    "prompt_tokens_details": {"cached_tokens": 0},
                },
            )
            send("data: [DONE]\n\n")

    if mode == "no-listen":  # alive, but another program holds its port
        threading.Event().wait()
    httpd = Server(("127.0.0.1", port), Handler)
    httpd.serve_forever()


def run_runner(mode):
    # The real runner is run as a script in ROOT and imports its neighbours.
    sys.path.insert(0, str(ROOT))
    import engine_runtime

    engine_runtime.listeners = listeners
    lt = load_runner()
    if mode == "vanish":
        sys.stdin.readline()
        sys.exit(3)
    spawned: list = []

    def spawn(command, env, log):
        return spawner(mode, spawned)(command, env, log)

    def load_counter(job):
        return count_words

    lt.main(
        spawn=spawn,
        probe=lambda pid: lt.Sample(40.0, 0, 0, 1, 0),
        load_counter=load_counter,
    )


if __name__ == "__main__":
    if sys.argv[1] == "serve":
        serve(int(sys.argv[2]), sys.argv[3])
    else:
        run_runner(sys.argv[2])
