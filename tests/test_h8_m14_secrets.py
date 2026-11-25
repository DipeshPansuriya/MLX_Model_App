"""H8: findings never echo the matched line. M14: every outgoing blob is scanned."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCANNER = ROOT / "scripts" / "check_secrets.py"
PRE_PUSH = ROOT / ".githooks" / "pre-push"
ZERO = "0" * 40
# Assembled at runtime so this file never matches the rule it exercises.
TOKEN = "ghp_" + "Ab1" * 12


def _env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        HOME=str(home),
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@invalid",
        PYTHONDONTWRITEBYTECODE="1",
    )
    return env


class Repo:
    def __init__(self, base: Path):
        self.path = base / "repo"
        self.path.mkdir()
        self.env = _env(base)
        self.git("init", "-q", "-b", "main")
        (self.path / "scripts").mkdir()
        shutil.copy(SCANNER, self.path / "scripts" / "check_secrets.py")

    def git(self, *args: str) -> str:
        done = subprocess.run(
            ["git", *args],
            cwd=self.path,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout.strip()

    def commit(self, name: str, content: str) -> str:
        (self.path / name).write_text(content, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", name)
        return self.git("rev-parse", "HEAD")

    def scan(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.path / "scripts" / "check_secrets.py"), *args],
            cwd=self.path,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def push_hook(self, local: str, remote: str, remote_name: str = "origin"):
        return subprocess.run(
            ["bash", str(PRE_PUSH), remote_name, "unused-url"],
            cwd=self.path,
            env=self.env,
            input=f"refs/heads/main {local} refs/heads/main {remote}\n",
            capture_output=True,
            text=True,
            check=False,
        )

    def leak_then_remove(self) -> tuple[str, str]:
        base = self.commit("readme.txt", "hello\n")
        self.commit("config.txt", f"token = {TOKEN}\n")
        tip = self.commit("config.txt", "clean\n")
        return base, tip


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


class TestRedaction:
    @pytest.mark.parametrize("mode", [[], ["--staged"], ["--all-history"]])
    def test_output_has_rule_and_location_but_no_secret(self, repo, mode):
        if mode == ["--staged"]:
            (repo.path / "config.txt").write_text(f"x\ntoken = {TOKEN}\n", encoding="utf-8")
            repo.git("add", "-A")
        else:
            repo.commit("config.txt", f"x\ntoken = {TOKEN}\n")
        done = repo.scan(*mode)
        output = done.stdout + done.stderr
        assert done.returncode == 1, output
        assert "SEC-001" in output
        assert "config.txt:2" in output
        assert TOKEN not in output
        assert TOKEN[4:20] not in output
        assert "token = " not in output

    def test_warning_rules_are_redacted_too(self, repo):
        value = "Zz9" * 10
        repo.commit("env.txt", f"Authorization: Bearer {value}\n")
        done = repo.scan()
        assert "SEC-022" in done.stdout
        assert value not in done.stdout + done.stderr


class TestOutgoingCommits:
    def test_range_catches_a_secret_removed_by_a_later_commit(self, repo):
        base, tip = repo.leak_then_remove()
        done = repo.scan("--range", f"{base}..{tip}")
        assert done.returncode == 1, done.stdout + done.stderr
        assert "config.txt:1" in done.stdout

    def test_new_branch_scans_history_not_the_work_tree(self, repo):
        _, tip = repo.leak_then_remove()
        done = repo.scan("--range", tip)
        assert done.returncode == 1, done.stdout + done.stderr

    def test_commits_already_on_the_remote_are_not_rescanned(self, repo):
        _, tip = repo.leak_then_remove()
        repo.git("update-ref", "refs/remotes/origin/main", tip)
        newer = repo.commit("more.txt", "fine\n")
        done = repo.scan("--range", newer, "--remote", "origin")
        assert done.returncode == 0, done.stdout + done.stderr

    def test_another_remote_does_not_hide_commits(self, repo):
        _, tip = repo.leak_then_remove()
        repo.git("update-ref", "refs/remotes/private/main", tip)
        done = repo.scan("--range", tip, "--remote", "origin")
        assert done.returncode == 1, done.stdout + done.stderr

    def test_clean_range_passes(self, repo):
        base = repo.commit("a.txt", "one\n")
        tip = repo.commit("b.txt", "two\n")
        assert repo.scan("--range", f"{base}..{tip}").returncode == 0

    def test_bad_range_fails_closed(self, repo):
        repo.commit("a.txt", "one\n")
        assert repo.scan("--range", "nope..HEAD").returncode == 2


class TestPrePushHook:
    def test_blocks_existing_branch(self, repo):
        base, tip = repo.leak_then_remove()
        done = repo.push_hook(tip, base)
        assert done.returncode == 1, done.stdout + done.stderr
        assert TOKEN not in done.stdout + done.stderr

    def test_blocks_new_branch(self, repo):
        _, tip = repo.leak_then_remove()
        assert repo.push_hook(tip, ZERO).returncode == 1

    def test_unknown_remote_sha_still_scans(self, repo):
        _, tip = repo.leak_then_remove()
        done = repo.push_hook(tip, "1" * 40)
        assert done.returncode == 1
        assert "config.txt:1" in done.stdout

    def test_allows_a_clean_push(self, repo):
        base = repo.commit("a.txt", "one\n")
        tip = repo.commit("b.txt", "two\n")
        done = repo.push_hook(tip, base)
        assert done.returncode == 0, done.stdout + done.stderr
