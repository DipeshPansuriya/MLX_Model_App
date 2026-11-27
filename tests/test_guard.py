"""M6: the API server on 8080 starts through ``mlx_guard.py``.

Policy tests need nothing but the standard library. The real-handler and offline
load tests need mlx_lm (macOS) and skip without it. No model is loaded.
"""

from __future__ import annotations

import gc
import http.client
import importlib.util
import json
import os
import subprocess
import sys
import threading
import types
from email.message import Message
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "mlx_guard.py"
ORIGINS = ["http://127.0.0.1:8765", "http://localhost:8765"]
HARNESS_MODELS = (
    "mlx-community/Qwen2.5-7B-Instruct-1M-4bit",
    "mlx-community/DeepSeek-R1-Distill-Qwen-7B-4bit",
    "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit",
)
DEFAULT = "mlx-community/Started-With-4bit"
# Recorded from DeepSeek Harness 0.2.2-rc.5 (its pi-ai 1.3.0 and openai 7.19.0 on
# its own Node 24 runtime) against a capture port. No Origin is sent.
HARNESS_POST = {
    "accept": "application/json",
    "x-stainless-lang": "js",
    "x-stainless-runtime": "node",
    "authorization": "Bearer placeholder",
    "user-agent": "pi (darwin 27.2.0; arm64)",
    "content-type": "application/json",
    "accept-language": "*",
    "sec-fetch-mode": "cors",
    "accept-encoding": "gzip, deflate",
}
HARNESS_GET = {
    "accept": "application/json",
    "authorization": "Bearer placeholder",
    "accept-language": "*",
    "sec-fetch-mode": "cors",
    "user-agent": "node",
}


@pytest.fixture
def guard():
    spec = importlib.util.spec_from_file_location("mlx_guard_under_test", GUARD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def headers(*pairs) -> Message:
    message = Message()
    for name, value in pairs:
        message[name] = value
    return message


def cache_repo(cache: Path, repo: str, files=("config.json", "model.safetensors")) -> Path:
    folder = cache / f"models--{repo.replace('/', '--')}"
    snapshot = folder / "snapshots" / ("a" * 40)
    snapshot.mkdir(parents=True)
    (folder / "refs").mkdir()
    (folder / "refs" / "main").write_text("a" * 40, encoding="utf-8")
    for name in files:
        (snapshot / name).write_text("{}", encoding="utf-8")
    return snapshot


# Header policy --------------------------------------------------------------- #
class TestHeaderPolicy:
    @pytest.mark.parametrize(
        "host",
        ["127.0.0.1:8080", "localhost:9999", "LOCALHOST:8080", "[::1]:8080", "127.0.0.1"],
    )
    def test_loopback_host_on_any_port_passes(self, guard, host):
        assert guard.check_headers("GET", headers(("Host", host)), ORIGINS) is None

    @pytest.mark.parametrize(
        "host",
        [
            "evil.example:8080",
            "127.0.0.1.evil.example",
            "localhost.evil.example:8080",
            "evil.example@127.0.0.1",
            "127.0.0.1:8080:1",
            "host.docker.internal:8080",
            "0.0.0.0:8080",
            "",
        ],
    )
    def test_other_host_is_refused(self, guard, host):
        assert guard.check_headers("GET", headers(("Host", host)), ORIGINS)[0] == 403

    def test_missing_or_repeated_host_is_refused(self, guard):
        twice = headers(("Host", "127.0.0.1:8080"), ("Host", "evil.example"))
        assert guard.check_headers("GET", headers(), ORIGINS)[0] == 403
        assert guard.check_headers("GET", twice, ORIGINS)[0] == 403

    @pytest.mark.parametrize("method", ["GET", "POST", "OPTIONS"])
    @pytest.mark.parametrize(
        "origin", ["https://evil.example", "null", "http://127.0.0.1:8766", "", "*"]
    )
    def test_other_origin_is_refused_even_with_a_wildcard(self, guard, method, origin):
        sent = headers(
            ("Host", "127.0.0.1:8080"), ("Origin", origin), ("Content-Type", "application/json")
        )
        for allowed in (ORIGINS, [*ORIGINS, "*", "null", ""]):
            assert guard.check_headers(method, sent, allowed)[0] == 403

    def test_app_origin_passes_but_a_second_foreign_one_does_not(self, guard):
        good = headers(("Host", "127.0.0.1:8080"), ("Origin", ORIGINS[1]))
        assert guard.check_headers("OPTIONS", good, ORIGINS) is None
        good["Origin"] = "https://evil.example"
        assert guard.check_headers("OPTIONS", good, ORIGINS)[0] == 403

    @pytest.mark.parametrize(
        "ctype", ["application/json", "application/json; charset=utf-8", "Application/JSON"]
    )
    def test_json_post_passes(self, guard, ctype):
        sent = headers(("Host", "127.0.0.1:8080"), ("Content-Type", ctype))
        assert guard.check_headers("POST", sent, ORIGINS) is None

    @pytest.mark.parametrize(
        "ctypes",
        [
            (),
            ("text/plain",),
            ("application/x-www-form-urlencoded",),
            ("multipart/form-data; boundary=x",),
            ("text/plain; application/json",),
            ("application/json", "text/plain"),
        ],
    )
    def test_other_post_content_type_is_refused(self, guard, ctypes):
        sent = headers(("Host", "127.0.0.1:8080"), *[("Content-Type", c) for c in ctypes])
        assert guard.check_headers("POST", sent, ORIGINS)[0] == 415


# Body policy ----------------------------------------------------------------- #
class TestBodyPolicy:
    def check(self, guard, model, draft="default_model", adapters=None, cached=()):
        return guard.check_body(model, draft, adapters, DEFAULT, lambda repo: repo in cached)

    @pytest.mark.parametrize("model", ["default_model", DEFAULT])
    def test_the_started_model_passes(self, guard, model):
        assert self.check(guard, model) is None

    @pytest.mark.parametrize("model", [*HARNESS_MODELS, "gpt2"])
    def test_any_cached_model_passes(self, guard, model):
        assert self.check(guard, model, cached=(model,)) is None

    def test_uncached_model_is_refused_with_advice(self, guard):
        assert "download it in MLX Studio first" in self.check(guard, "org/not-downloaded")

    @pytest.mark.parametrize(
        "model",
        ["/etc", "./org/model", "../model", "org/..", "~/model", "a/b/c", "", "org/model\n", None],
    )
    def test_paths_are_refused_without_asking_the_cache(self, guard, model):
        asked = []
        assert guard.check_body(model, None, None, DEFAULT, asked.append)
        assert asked == []

    def test_existing_local_path_is_refused_even_when_cached(self, guard, tmp_path, monkeypatch):
        (tmp_path / "org" / "model").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        assert self.check(guard, "org/model", cached=("org/model",))

    @pytest.mark.parametrize("draft", [None, "default_model"])
    def test_default_draft_passes(self, guard, draft):
        assert self.check(guard, "default_model", draft=draft) is None

    @pytest.mark.parametrize("draft", ["org/model", "/tmp", "", ["x"], {}])
    def test_other_draft_is_refused(self, guard, draft):
        assert self.check(guard, "default_model", draft=draft)

    @pytest.mark.parametrize("adapters", ["org/model", "/tmp", ""])
    def test_adapters_are_refused(self, guard, adapters):
        assert self.check(guard, "default_model", adapters=adapters)


class TestCachedCheck:
    def checker(self, guard, snapshot):
        return guard.cached_checker(lambda repo: snapshot)

    def test_needs_weights_and_every_indexed_shard(self, guard, tmp_path):
        snapshot = cache_repo(tmp_path, "org/model", files=("config.json",))
        is_cached = self.checker(guard, snapshot)
        assert not is_cached("org/model")
        (snapshot / "model-00001-of-00002.safetensors").write_text("x", encoding="utf-8")
        index = {"weight_map": {f"w{i}": f"model-0000{i}-of-00002.safetensors" for i in (1, 2)}}
        (snapshot / "model.safetensors.index.json").write_text(json.dumps(index), "utf-8")
        assert not is_cached("org/model")
        (snapshot / "model-00002-of-00002.safetensors").write_text("x", encoding="utf-8")
        assert is_cached("org/model")
        (snapshot / "model.safetensors.index.json").write_text("not json", encoding="utf-8")
        assert not is_cached("org/model")

    def test_a_lookup_error_means_not_cached(self, guard):
        def missing(repo):
            raise OSError("not in the cache")

        assert not guard.cached_checker(missing)("org/model")


# Real mlx_lm handler --------------------------------------------------------- #
@pytest.fixture
def live(guard, tmp_path, monkeypatch):
    """The installed APIHandler, guarded, on a free port, with a stub generator."""
    server = pytest.importorskip("mlx_lm.server")
    utils = pytest.importorskip("mlx_lm.utils")
    from huggingface_hub import constants

    cache = tmp_path / "hub"
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(cache))
    for repo in HARNESS_MODELS:
        cache_repo(cache, repo)

    loads, downloads = [], []
    real_download = utils.snapshot_download

    def spy_download(*args, **kwargs):
        if not kwargs.get("local_files_only"):
            downloads.append(args)
            raise AssertionError("a request reached the network download")
        return real_download(*args, **kwargs)

    monkeypatch.setattr(utils, "snapshot_download", spy_download)
    monkeypatch.setattr(server.ModelProvider, "load", lambda self, *args: loads.append(args))
    provider = object.__new__(server.ModelProvider)

    def generate(request, args, progress_callback=None):
        provider.load(args.model.model, args.model.adapter, args.model.draft)
        raise RuntimeError("stub generator reached")

    cli = types.SimpleNamespace(
        allowed_origins=ORIGINS,
        model=DEFAULT,
        num_draft_tokens=3,
        max_tokens=8,
        temp=0.0,
        top_p=1.0,
        top_k=0,
        min_p=0.0,
        chat_template_args={},
    )
    generator = types.SimpleNamespace(cli_args=cli, generate=generate, is_healthy=True)

    for name in ("parse_request", "validate_model_parameters"):
        monkeypatch.setattr(server.APIHandler, name, getattr(server.APIHandler, name))
    formatter = server.ToolCallFormatter
    monkeypatch.setattr(formatter, "_format", formatter._format)
    assert guard.install(server, guard.cached_checker(utils.hf_repo_to_path)) is None

    handler = partial(server.APIHandler, generator, system_fingerprint="test")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def send(method, path, sent, body=None, host="127.0.0.1:8080"):
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            if host is not None:
                conn.putheader("Host", host)
            for name, value in sent.items():
                conn.putheader(name, value)
            data = None if body is None else json.dumps(body).encode()
            if data is not None:
                conn.putheader("Content-Length", str(len(data)))
            conn.endheaders(data)
            response = conn.getresponse()
            return response.status, response.headers, response.read().decode()
        finally:
            conn.close()

    try:
        yield types.SimpleNamespace(send=send, loads=loads, downloads=downloads, cache=cache)
    finally:
        httpd.shutdown()
        httpd.server_close()


def chat(**extra):
    return {"messages": [{"role": "user", "content": "hi"}], **extra}


CHAT = "/v1/chat/completions"
JSON = {"Content-Type": "application/json"}


class TestRealHandler:
    def assert_nothing_happened(self, live):
        assert live.loads == []
        assert live.downloads == []

    @pytest.mark.parametrize(
        "sent",
        [{"Content-Type": "text/plain"}, {"Content-Type": "application/x-www-form-urlencoded"}, {}],
    )
    def test_simple_request_post_gets_415(self, live, sent):
        status, _, _ = live.send("POST", CHAT, sent, chat(model=HARNESS_MODELS[0]))
        assert status == 415
        self.assert_nothing_happened(live)

    @pytest.mark.parametrize("origin", ["https://evil.example", "null"])
    @pytest.mark.parametrize(
        ("method", "path"), [("POST", CHAT), ("GET", "/v1/models"), ("OPTIONS", CHAT)]
    )
    def test_foreign_origin_gets_403_and_no_cors_header(self, live, origin, method, path):
        body = chat(model=HARNESS_MODELS[0]) if method == "POST" else None
        status, got, text = live.send(method, path, {**JSON, "Origin": origin}, body)
        assert status == 403
        assert got["Access-Control-Allow-Origin"] is None
        assert HARNESS_MODELS[0] not in text
        self.assert_nothing_happened(live)

    @pytest.mark.parametrize("host", ["evil.example:8080", None])
    @pytest.mark.parametrize(("method", "path"), [("POST", CHAT), ("GET", "/v1/models")])
    def test_rebound_or_missing_host_gets_403(self, live, host, method, path):
        sent = {**JSON, "Origin": "http://evil.example:8080"} if host else JSON
        body = chat(model=HARNESS_MODELS[0]) if method == "POST" else None
        status, _, text = live.send(method, path, sent, body, host=host)
        assert status == 403
        assert HARNESS_MODELS[0] not in text
        self.assert_nothing_happened(live)

    def test_a_large_refused_body_still_gets_its_answer(self, live):
        body = chat(model=HARNESS_MODELS[0], pad="x" * 400_000)
        assert live.send("POST", CHAT, {"Content-Type": "text/plain"}, body)[0] == 415

    @pytest.mark.parametrize("stream", [True, False])
    @pytest.mark.parametrize("host", ["127.0.0.1:8080", "localhost:9999"])
    @pytest.mark.parametrize("model", [*HARNESS_MODELS, "default_model", DEFAULT])
    def test_harness_requests_reach_the_generator(self, live, model, host, stream):
        status, _, text = live.send(
            "POST", CHAT, HARNESS_POST, chat(model=model, stream=stream), host=host
        )
        assert (status, "stub generator reached" in text) == (404, True), text
        assert [call[0] for call in live.loads] == [model]
        assert live.downloads == []

    def test_a_request_without_a_model_uses_the_default(self, live):
        _, _, text = live.send("POST", CHAT, JSON, chat())
        assert "stub generator reached" in text

    def test_harness_model_listing_and_health_pass(self, live):
        assert live.send("GET", "/v1/models", HARNESS_GET)[0] == 200
        assert live.send("GET", "/health", {})[0] == 200

    def test_app_origin_preflight_and_post(self, live):
        sent = {**JSON, "Origin": ORIGINS[0]}
        status, got, _ = live.send("OPTIONS", CHAT, sent)
        assert (status, got["Access-Control-Allow-Origin"]) == (204, ORIGINS[0])
        _, got, text = live.send("POST", CHAT, sent, chat(model=HARNESS_MODELS[0]))
        assert "stub generator reached" in text
        assert got["Access-Control-Allow-Origin"] == ORIGINS[0]

    @pytest.mark.parametrize(
        "extra",
        [
            {"model": "org/not-downloaded"},
            {"model": "/tmp"},
            {"model": "local/model"},
            {"draft_model": HARNESS_MODELS[0]},
            {"adapters": "org/adapter"},
        ],
    )
    @pytest.mark.parametrize("path", [CHAT, "/chat/completions", "/v1/completions"])
    def test_refused_body_gets_400(self, live, tmp_path, monkeypatch, extra, path):
        (tmp_path / "local" / "model").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        body = {**chat(prompt="hi", model=HARNESS_MODELS[0]), **extra}
        status, _, text = live.send("POST", path, JSON, body)
        assert status == 400, text
        assert "error" in json.loads(text)
        self.assert_nothing_happened(live)

    def test_a_new_download_works_without_a_restart(self, live):
        body = chat(model="org/later")
        assert live.send("POST", CHAT, JSON, body)[0] == 400
        cache_repo(live.cache, "org/later")
        assert "stub generator reached" in live.send("POST", CHAT, JSON, body)[2]


def test_offline_load_of_cached_models(tmp_path):
    """The file lookup mlx_lm runs on load must succeed with the network off."""
    pytest.importorskip("mlx_lm.utils")
    script = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from test_guard import cache_repo
from huggingface_hub import constants
from huggingface_hub._tree_cache import TreeCacheEntry, write_tree_cache
from mlx_lm.utils import _download

cache = Path(constants.HF_HUB_CACHE)
# As the app downloads: every file of the repo, one by one, no cached listing.
app = cache_repo(cache, "org/app", ("config.json", "model.safetensors", "README.md"))
# As mlx_lm downloads: only its patterns, with the full listing cached.
lm = cache_repo(cache, "org/lm", ("config.json", "model.safetensors"))
listing = {n: TreeCacheEntry(size=2, blob_id="b") for n in
           ("config.json", "model.safetensors", ".gitattributes", "weights.gguf")}
write_tree_cache(str(lm.parent.parent), "a" * 40, listing)
assert _download("org/app") == app
assert _download("org/lm") == lm
print("offline ok", constants.HF_HUB_OFFLINE)
"""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HF_", "HUGGING"))}
    env.update(HF_HOME=str(tmp_path / "hf"), HF_HUB_OFFLINE="1", PYTHONDONTWRITEBYTECODE="1")
    done = subprocess.run(
        [sys.executable, "-c", script, str(ROOT / "tests")],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "offline ok True" in done.stdout


# F1: image URLs -------------------------------------------------------------- #
IMAGE_SHAPES = {
    "image_url-dict": lambda url: {"type": "image_url", "image_url": {"url": url}},
    "image_url-string": lambda url: {"type": "image_url", "image_url": url},
    "input_image": lambda url: {"type": "input_image", "image_url": url},
    "input_image-dict": lambda url: {"type": "input_image", "image_url": {"url": url}},
    "image": lambda url: {"type": "image", "image": url},
    "image-dict": lambda url: {"type": "image", "image": {"url": url}},
}
DATA_URL = "data:image/png;base64,iVBORw0KGgo="


def image_chat(*parts, **extra):
    content = [{"type": "text", "text": "look"}, *parts]
    return {"messages": [{"role": "user", "content": content}], **extra}


@pytest.fixture
def fetches(live, monkeypatch):
    """Every URL mlx_lm tries to open. Nothing leaves the machine."""
    server = pytest.importorskip("mlx_lm.server")
    seen = []

    def spy(url, *args, **kwargs):
        seen.append(url)
        raise OSError("blocked by the test")

    monkeypatch.setattr(server, "urlopen", spy)
    return seen


class TestImageUrls:
    @pytest.mark.parametrize("path", [CHAT, "/chat/completions"])
    @pytest.mark.parametrize("shape", IMAGE_SHAPES)
    def test_a_remote_image_is_refused_before_any_fetch(self, live, fetches, shape, path):
        part = IMAGE_SHAPES[shape]("http://127.0.0.1:9/track.png?user=probe")
        status, _, text = live.send("POST", path, JSON, image_chat(part))
        assert (status, "data: URL" in text) == (400, True), text
        assert fetches == [] and live.loads == []

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.example/x.png",
            "HTTP://evil.example/x.png",
            " http://evil.example/x.png",
            "ftp://evil.example/x.png",
            "file:///etc/passwd",
            "/etc/passwd",
            "DATA:image/png;base64,AAAA",
            "",
            7,
            ["http://evil.example/x.png"],
        ],
    )
    def test_only_data_urls_pass(self, live, fetches, url):
        part = IMAGE_SHAPES["image_url-dict"](url)
        status, _, text = live.send("POST", CHAT, JSON, image_chat(part))
        assert (status, "data: URL" in text) == (400, True), text
        assert fetches == [] and live.loads == []

    def test_a_remote_image_after_a_data_image_is_refused(self, live, fetches):
        shape = IMAGE_SHAPES["image_url-dict"]
        body = image_chat(shape(DATA_URL), shape("https://evil.example/x.png"))
        body["messages"].insert(0, {"role": "system", "content": "be brief"})
        assert live.send("POST", CHAT, JSON, body)[0] == 400
        assert fetches == []

    @pytest.mark.parametrize("shape", IMAGE_SHAPES)
    def test_a_data_image_reaches_the_generator(self, live, fetches, shape):
        _, _, text = live.send("POST", CHAT, JSON, image_chat(IMAGE_SHAPES[shape](DATA_URL)))
        assert "stub generator reached" in text, text
        assert fetches == []

    @pytest.mark.parametrize(
        "messages", [7, "hi", {"role": "user"}, ["hi"], [{"role": "user", "content": ["hi"]}]]
    )
    def test_malformed_messages_get_a_400_not_a_crash(self, live, fetches, messages):
        status, _, text = live.send("POST", CHAT, JSON, {"messages": messages})
        assert (status, "messages" in text) == (400, True), text
        assert fetches == [] and live.loads == []

    def test_a_text_completion_without_messages_still_passes(self, live):
        _, _, text = live.send("POST", "/v1/completions", JSON, {"prompt": "hi"})
        assert "stub generator reached" in text, text


# Fail closed ----------------------------------------------------------------- #
STUB_SERVER = """
import json, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
OFFLINE_AT_IMPORT = os.environ.get("HF_HUB_OFFLINE")

def _image_url(part):
    return None

def _image_parts(messages):
    return iter(())

def _inline_image_urls(messages):
    for part in _image_parts(messages):
        _image_url(part)

class ToolCallFormatter:
    def __init__(self, tools):
        self._tools = tools

    def _format(self, tc):
        return tc["arguments"]

    def __call__(self, calls):
        return list(self._format(tc) for tc in calls)

class APIHandler(BaseHTTPRequestHandler):
    def handle_completion(self):
        ToolCallFormatter([])

    def handle_chat_completions(self):
        _inline_image_urls(self.body["messages"])

    def do_POST(self):
        self.requested_model = self.requested_draft_model = self.adapter = None
        self.validate_model_parameters()

    def validate_model_parameters(self):
        pass

def _run_http_server(host, handler_class=APIHandler):
    pass

def run(host):
    _run_http_server(host)

def main():
    with open(os.environ["STUB_MARK"], "w") as mark:
        json.dump({"argv": sys.argv[1:], "offline": OFFLINE_AT_IMPORT}, mark)
    run(None)
"""


def run_guard_on_stub(tmp_path, source, *args):
    package = tmp_path / "stub" / "mlx_lm"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "server.py").write_text(source, encoding="utf-8")
    (package / "utils.py").write_text("def hf_repo_to_path(repo):\n    return repo\n", "utf-8")
    mark = tmp_path / "main-was-called"
    env = {**os.environ, "PYTHONPATH": str(package.parent), "STUB_MARK": str(mark)}
    env.pop("HF_HUB_OFFLINE", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    done = subprocess.run(
        [sys.executable, str(GUARD), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return done, mark


class TestStartup:
    def test_starts_offline_and_passes_flags_through(self, tmp_path):
        flags = ["--model", "org/model", "--host", "127.0.0.1", "--port", "8080", "--kv-bits", "4"]
        done, mark = run_guard_on_stub(tmp_path, STUB_SERVER, *flags)
        assert done.returncode == 0, done.stderr
        assert json.loads(mark.read_text()) == {"argv": flags, "offline": "1"}

    @pytest.mark.parametrize(
        ("old", "new"),
        [
            ("class APIHandler(", "class Renamed("),
            ("def validate_model_parameters", "def renamed"),
            ("        self.validate_model_parameters()", "        pass"),
            ("self.requested_draft_model = ", ""),
            ("self.adapter = ", ""),
            ("handler_class=APIHandler", "handler_class=BaseHTTPRequestHandler"),
            ("    _run_http_server(host)", "    pass"),
            ("    run(None)", "    pass"),
            (
                "    def do_POST",
                "    def handle_one_request(self):\n        pass\n\n    def do_POST",
            ),
            ("def main", "def renamed_main"),
            ("def _image_url(", "def renamed_url("),
            ("def _image_parts(", "def renamed_parts("),
            ("    for part in _image_parts(messages):", "    for part in other(messages):"),
            ("        _image_url(part)", "        pass"),
            ('        _inline_image_urls(self.body["messages"])', "        pass"),
            ("def handle_chat_completions", "def renamed_chat"),
            ("class ToolCallFormatter:", "class RenamedFormatter:"),
            ("def _format(self, tc):", "def _renamed(self, tc):"),
            ("def _format(self, tc):", "def _format(self, tc, tools):"),
            ('return tc["arguments"]', 'return tc["input"]'),
            ("self._tools = tools", "self.tools = tools"),
            ("self._format(tc) for tc in calls", "tc for tc in calls"),
            ("        ToolCallFormatter([])", "        pass"),
            ("def handle_completion(", "def renamed_completion("),
        ],
    )
    def test_refuses_to_start_when_a_hook_point_is_gone(self, tmp_path, old, new):
        source = STUB_SERVER.replace(old, new)
        if old == "class APIHandler(":
            source = source.replace("handler_class=APIHandler", "handler_class=Renamed")
        assert source != STUB_SERVER
        done, mark = run_guard_on_stub(tmp_path, source)
        assert done.returncode != 0
        assert "refusing to start" in done.stderr
        assert not mark.exists()

    def test_refuses_to_start_without_mlx_lm(self, tmp_path):
        done, mark = run_guard_on_stub(tmp_path, "raise ImportError('broken install')\n")
        assert done.returncode != 0
        assert "refusing to start" in done.stderr
        assert not mark.exists()


# The app --------------------------------------------------------------------- #
@pytest.mark.parametrize("background", [False, True])
def test_app_launches_the_guard_offline(app_module, monkeypatch, background):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    seen = {}

    def fake_popen(cmd, **kwargs):
        seen.update(cmd=cmd, env=kwargs.get("env"))
        raise OSError("not started in tests")

    monkeypatch.setattr(app_module, "stop_server", lambda *a, **k: None)
    monkeypatch.setattr(app_module.subprocess, "Popen", fake_popen)
    app_module.LOG_DIR.mkdir(parents=True, exist_ok=True)
    app_module.worker_server_start("org/model", background)
    assert seen["cmd"][:4] == [sys.executable, str(GUARD), "--model", "org/model"]
    assert seen["cmd"][4:8] == ["--host", "127.0.0.1", "--port", "8080"]
    assert seen["env"]["HF_HUB_OFFLINE"] == "1"
    assert seen["env"]["HOME"] == os.environ["HOME"]
    assert "HF_HUB_OFFLINE" not in os.environ


PY = "/usr/bin/python3"
BUNDLED_GUARD = "/Applications/MLX Studio.app/Contents/Resources/mlx_guard.py"


@pytest.mark.parametrize(
    ("command", "ours"),
    [
        (f"{PY} {BUNDLED_GUARD} --model org/model --host 127.0.0.1 --port 8080 --x", True),
        (f"{PY} -m mlx_lm server --model org/model --host 127.0.0.1 --port 8080", True),
        (f"{PY} {BUNDLED_GUARD} --model org/other --host 127.0.0.1 --port 8080", False),
        (f"{PY} {BUNDLED_GUARD} --model org/model --host 127.0.0.1 --port 9090", False),
        (f"{PY} /x/not_mlx_guard.py --model org/model --host 127.0.0.1 --port 8080", False),
        (f"{PY} /x/other.py --model org/model --host 127.0.0.1 --port 8080", False),
    ],
)
def test_identity_check_knows_old_and_new_servers(app_module, monkeypatch, command, ours):
    monkeypatch.setattr(app_module, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(app_module, "_ps_field", lambda pid, field: command)
    assert app_module._is_our_server(4242, "org/model", None) is ours


# Row 656: two models, checked against the installed mlx_lm ------------------ #
class Weights(dict):
    """mlx's nn.Module is a dict of its weights."""

    def __init__(self, name):
        super().__init__(weights=name)
        self.name = name


def studio_args():
    return types.SimpleNamespace(
        model="org/a", adapter_path=None, draft_model=None, trust_remote_code=False,
        chat_template=None, use_default_chat_template=False, pipeline=False,
    )  # fmt: skip


@pytest.fixture
def real(monkeypatch):
    """The installed mlx_lm.server, put back as it was afterwards."""
    pytest.importorskip("mlx_lm")
    from mlx_lm import server

    for cls, name in (
        (server.ModelProvider, "_load"),
        (server.ModelProvider, "load_default"),
        (server.ResponseGenerator, "__init__"),
        (server.ResponseGenerator, "_next_request"),
    ):
        monkeypatch.setattr(cls, name, getattr(cls, name))
    return server


class TestInstalledMlxLmTwoModels:
    def test_hooks_are_where_the_guard_expects(self, guard, real):
        assert guard.hook_problem(real) is None
        assert guard.multi_problem(real) is None

    def test_renamed_load_argument_is_reported(self, guard, real, monkeypatch):
        def _load(self, path, adapter_path=None, draft_model_path=None):
            pass

        monkeypatch.setattr(real.ModelProvider, "_load", _load)
        assert "_load" in guard.multi_problem(real)

    def test_generation_loop_without_batch_generator_is_reported(self, guard, real, monkeypatch):
        def _generate(self):
            self.model_provider.load_default()
            self.model_provider.load("x")

        monkeypatch.setattr(real.ResponseGenerator, "_generate", _generate)
        assert "_generate" in guard.multi_problem(real)

    def test_a_missing_request_loop_hook_is_reported(self, guard, real, monkeypatch):
        monkeypatch.delattr(real.ResponseGenerator, "_next_request")
        assert "_next_request" in guard.multi_problem(real)

    def test_missing_cache_trim_is_reported(self, guard, real, monkeypatch):
        monkeypatch.setattr(real, "LRUPromptCache", type("Cache", (), {}))
        assert "trim_to" in guard.multi_problem(real)

    def test_real_classes_keep_two_models(self, guard, real, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(
            real,
            "load",
            lambda path, **kw: calls.append(path) or (Weights(path), path, {}),
        )
        monkeypatch.setattr(real, "load_processor", lambda model, config: None)
        monkeypatch.setattr(real, "make_prompt_cache", lambda model: [])
        monkeypatch.setattr(real.Thread, "start", lambda self: None)
        opts = guard.split_studio_args(
            ["--studio-max-models", "2", "--studio-also-model", "org/b"]
        )[1]
        path = tmp_path / "r.json"
        guard.install_multi(
            real,
            opts,
            guard.status_writer(str(path)),
            figures=lambda repo, tokens: (1000, 1000),
            is_cached=lambda repo: True,
        )
        provider = real.ModelProvider(studio_args())
        cache = real.LRUPromptCache(2)
        real.ResponseGenerator(provider, cache)
        assert provider._studio_prompt_cache is cache
        provider.load_default()
        provider.load("org/b")
        provider.load("default_model")
        assert calls == ["org/a", "org/b"]
        assert provider.model.name == "org/a"
        assert provider.model_key == ("org/a", None, None)

    def test_a_cache_without_a_byte_limit_is_reported(self, guard, real, monkeypatch):
        def insert_cache(self, model, tokens, prompt_cache, *, cache_type="assistant"):
            pass

        monkeypatch.setattr(real.LRUPromptCache, "insert_cache", insert_cache)
        assert "bytes" in guard.multi_problem(real)

    def test_the_real_cache_holds_the_byte_cap_on_a_serial_insert(
        self, guard, real, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(real.Thread, "start", lambda self: None)
        opts = guard.split_studio_args(["--studio-max-models", "2"])[1]
        guard.install_multi(real, opts, guard.status_writer(None), None, None)
        args = studio_args()
        args.prompt_cache_bytes = 250
        provider = real.ModelProvider(args)
        cache = real.LRUPromptCache(10)
        real.ResponseGenerator(provider, cache)

        class Layer:
            nbytes = 100

            def is_trimmable(self):
                return False

        for token in range(5):  # insert_cache is what _serve_single calls
            cache.insert_cache(("org/a", None, None), [token], [Layer()])
            assert cache.nbytes <= 250
        assert len(cache) == 2

    def test_real_provider_reports_one_model(self, guard, real, monkeypatch, tmp_path):
        monkeypatch.setattr(real, "load", lambda path, **kw: (Weights(path), path, {}))
        monkeypatch.setattr(real, "load_processor", lambda model, config: None)
        monkeypatch.setattr(real, "make_prompt_cache", lambda model: [])
        path = tmp_path / "r.json"
        guard.install_report(real, guard.status_writer(str(path)), 10**9)
        provider = real.ModelProvider(studio_args())
        provider.load_default()
        data = json.loads(path.read_text("utf-8"))
        assert data["resident"] == ["org/a"] and data["active_bytes"] >= 0

    def test_releasing_a_real_module_frees_weights_still_referenced(self, guard, real):
        import mlx.core as mx
        from mlx import nn

        gc.collect()
        mx.clear_cache()
        before = mx.get_active_memory()
        model = nn.Sequential(nn.Linear(1024, 1024))
        mx.eval(model.parameters())
        assert mx.get_active_memory() - before >= 4 * 1024 * 1024
        guard.release([model])
        gc.collect()
        mx.clear_cache()
        assert mx.get_active_memory() - before < 1024 * 1024
