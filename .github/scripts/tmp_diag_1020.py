"""TEMPORARY (row 1020): time what a local server start costs on this runner."""

import faulthandler
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

faulthandler.dump_traceback_later(10, repeat=True)


def timed(label, call):
    start = time.monotonic()
    call()
    print(f"DIAG {label}: {time.monotonic() - start:.2f}s", flush=True)


def bind():
    ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler).server_close()


for i in range(3):
    timed(f"getfqdn(127.0.0.1) #{i}", lambda: socket.getfqdn("127.0.0.1"))
    timed(f"HTTPServer bind #{i}", bind)
timed(
    "python start", lambda: subprocess.run([sys.executable, "-c", "import http.server"], check=True)
)
timed(
    "ps", lambda: subprocess.run(["ps", "-p", "1", "-o", "stat="], capture_output=True, check=True)
)
