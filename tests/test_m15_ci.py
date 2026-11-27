"""M15: CI builds the app on macOS and gates releases; SA-12 is hard at release."""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
QUALITY = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
AUDIT = (ROOT / "scripts" / "security_audit.sh").read_text(encoding="utf-8")
CHECKER = ROOT / ".github" / "scripts" / "require_no_skips.py"
SKIP_CHECK = ["python3", ".github/scripts/require_no_skips.py"]
REPORT = "$RUNNER_TEMP/macos-tests.xml"
SUITE_STEP = "Run the suite on macOS"
CHECK_STEP = "Guard and packaging tests ran, none skipped"


def test_ci_builds_the_app_on_macos():
    assert "runs-on: macos-" in QUALITY
    assert "./packaging/build_app.sh" in QUALITY
    assert "release_manifest.py --inspect" in QUALITY


def test_ci_gates_versions_tags_and_published_assets():
    assert 'tags: ["v*"]' in QUALITY
    assert 'release_manifest.py --check --tag "$GITHUB_REF_NAME"' in QUALITY
    assert "release_manifest.py --verify --dist" in QUALITY
    assert "fetch-depth: 0" in QUALITY


def test_ci_no_longer_references_the_removed_launcher():
    assert "launcher.py" not in QUALITY


def test_actions_stay_pinned_by_commit():
    for line in QUALITY.splitlines():
        if "uses:" in line:
            ref = line.split("@", 1)[1].split()[0]
            assert len(ref) == 40, line


def macos_job() -> str:
    return QUALITY.split("\n  macos-build:\n", 1)[1].split("\n  release:\n", 1)[0]


def macos_steps() -> list[dict[str, str]]:
    """The steps of the macOS job as {key: value}; comments between keys are dropped.

    A value keeps its further lines, including any "#" line inside a block.
    """
    steps: list[dict[str, str]] = []
    key = ""
    for line in macos_job().partition("\n    steps:\n")[2].splitlines():
        if line.startswith(" " * 10) and steps:
            steps[-1][key] += "\n" + line.strip()
        elif line.strip() and not line.lstrip().startswith("#"):
            match = re.fullmatch(r" {6}(- |  )([\w-]+):(?: +(.*))?", line)
            assert match, f"unexpected line in the macOS job: {line!r}"
            if match[1] == "- ":
                steps.append({})
            key = match[2]
            steps[-1][key] = match[3] or ""
    return steps


def step(name: str) -> dict[str, str]:
    found = [s for s in macos_steps() if s.get("name") == name]
    assert len(found) == 1, f"expected one step named {name!r}"
    return found[0]


def command(name: str) -> list[str]:
    """The words bash would see for a one-command step (plain or ">-" folded)."""
    first, *more = step(name)["run"].split("\n")
    if first == ">-":
        return shlex.split(" ".join(more), comments=True)
    assert not more, f"{name!r} must be a single command"
    return shlex.split(first, comments=True)


def guarded_files() -> list[str]:
    """The test files the workflow passes to the no-skip check."""
    return command(CHECK_STEP)[len(SKIP_CHECK) + 1 :]


def test_macos_job_installs_the_pinned_mlx_packages():
    assert "pip install -c requirements.txt mlx mlx-lm huggingface-hub" in macos_job()
    pins = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    for name in ("mlx", "mlx-lm", "huggingface-hub"):
        assert re.search(rf"^{name}==\d+\.\d+\.\d+$", pins, re.MULTILINE), name


def test_macos_job_checks_the_report_of_its_own_test_run():
    suite = ["python3", "-m", "pytest", "-q", "-rs", f"--junitxml={REPORT}"]
    assert command(SUITE_STEP) == suite  # nothing that deselects or hides a failure
    check = command(CHECK_STEP)
    assert check[: len(SKIP_CHECK) + 1] == [*SKIP_CHECK, REPORT]
    assert guarded_files()
    for word in guarded_files():
        assert re.fullmatch(r"tests/test_\w+\.py", word), word  # no "|| true"
    names = [s.get("name") for s in macos_steps()]
    assert names.index(SUITE_STEP) < names.index(CHECK_STEP)


def test_macos_test_steps_cannot_be_switched_off_or_softened():
    # No "if", "continue-on-error", "shell" or "env" on the steps, the job or the file.
    assert set(step(SUITE_STEP)) == {"name", "run"}
    assert set(step(CHECK_STEP)) == {"name", "run"}
    job_keys = re.findall(r"^ {4}([\w-]+):", macos_job(), re.MULTILINE)
    assert job_keys == ["name", "runs-on", "steps"]
    file_keys = re.findall(r"^([\w-]+):", QUALITY, re.MULTILINE)
    assert file_keys == ["name", "on", "permissions", "concurrency", "jobs"]
    assert "PYTEST_ADDOPTS" not in QUALITY


def test_the_packaging_behaviour_tests_must_run_on_macos():
    # They skip on the Ubuntu job, so this job is the only place they run.
    assert "tests/test_packaging.py" in guarded_files()


def test_every_file_needing_mlx_is_in_the_no_skip_check():
    needing = {
        f"tests/{path.name}"
        for path in (ROOT / "tests").glob("test_*.py")
        if re.search(
            r"importorskip\(\s*[\"'](mlx|huggingface_hub)", path.read_text(encoding="utf-8")
        )
    }
    assert {"tests/test_guard.py", "tests/test_guard_null_args.py"} <= needing
    assert needing <= set(guarded_files())


def checked_run(tmp_path: Path, body: str, files=("tests/test_sample.py",), options=()):
    """Run real pytest on a throwaway file, then the checker on its report."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text(body, encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    report = tmp_path / "report.xml"
    run_tests = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    subprocess.run(
        [*run_tests, f"--junitxml={report}", *options],
        cwd=tmp_path,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert report.is_file()
    return subprocess.run(
        [sys.executable, str(CHECKER), str(report), *files],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


PASSING = "def test_ok():\n    pass\n\nclass TestGroup:\n    def test_ok(self):\n        pass\n"
MISSING = 'import pytest\npytest.importorskip("no_such_module_for_ci")\n'


PARAMETRIZED = (
    'import pytest\n@pytest.mark.parametrize("n", [1, 2])\ndef test_each(n):\n    pass\n'
    "def helper():\n    pass\n"
    "class Fake:\n    def test_not_collected(self):\n        pass\n"
)


@pytest.mark.parametrize("body", [PASSING, PASSING + PARAMETRIZED], ids=["plain", "parametrized"])
def test_skip_check_passes_when_everything_ran(tmp_path, body):
    done = checked_run(tmp_path, body)
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize(
    "body",
    [
        PASSING + "\ndef test_needs():\n    " + MISSING.replace("\n", "\n    "),
        MISSING + PASSING,
        PASSING + "\nimport pytest\n@pytest.mark.skip\ndef test_off():\n    pass\n",
        PASSING
        + "\nclass TestMore:\n    def test_needs(self):\n        "
        + MISSING.replace("\n", "\n        "),
        PASSING + "\nimport pytest\n@pytest.mark.skip\nclass TestOff:\n"
        "    def test_off(self):\n        pass\n",
    ],
    ids=["one-test", "whole-file", "marker", "in-a-class", "whole-class"],
)
def test_skip_check_fails_on_a_skip(tmp_path, body):
    done = checked_run(tmp_path, body)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "skipped" in done.stdout


@pytest.mark.parametrize(
    ("body", "missing"),
    [
        (PASSING + "\ndef test_live():\n    pass\n", "test_live"),
        (PASSING + "\nclass TestLive:\n    def test_x(self):\n        pass\n", "TestLive.test_x"),
        (
            PASSING + "\nclass TestOuter:\n    class TestLive:\n"
            "        def test_x(self):\n            pass\n",
            "TestOuter.TestLive.test_x",
        ),
        (PASSING + "\nasync def test_live():\n    pass\n", "test_live"),
    ],
    ids=["function", "method", "nested-class", "async"],
)
def test_skip_check_fails_when_a_test_was_deselected(tmp_path, body, missing):
    done = checked_run(tmp_path, body, options=("-k", "not live and not Live"))
    assert done.returncode == 1, done.stdout + done.stderr
    assert f"tests/test_sample.py: {missing} did not run" in done.stdout


def test_skip_check_fails_when_a_listed_file_did_not_run(tmp_path):
    done = checked_run(tmp_path, PASSING, ("tests/test_sample.py", "tests/test_absent.py"))
    assert done.returncode == 1
    assert "tests/test_absent.py: no test passed" in done.stdout


def test_skip_check_fails_without_a_report(tmp_path):
    done = subprocess.run(
        [sys.executable, str(CHECKER), str(tmp_path / "none.xml"), "tests/test_guard.py"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 1
    assert "cannot read the test report" in done.stdout


def test_audit_has_a_release_mode_that_fails_hard():
    assert "--release" in AUDIT
    assert "release_manifest.py --verify --release --dist dist" in AUDIT
    assert 'bad SA-12 "release gate' in AUDIT
