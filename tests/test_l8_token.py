"""L8: the GitHub token must never appear on a command line."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "set_approval_gate.sh"
VALUE = "stub-credential-value"

GIT_STUB = f"""#!/bin/sh
cat >/dev/null
echo "password={VALUE}"
"""

# Records argv and whatever arrives on stdin, then answers like the API would.
CURL_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$STUB_DIR/argv.log"
for a in "$@"; do
    if [ "$a" = "@-" ]; then cat >> "$STUB_DIR/stdin.log"; fi
done
case "$*" in
    *rulesets*) echo '{"rules":[{"type":"pull_request","parameters":{"required_approving_review_count":0,"require_code_owner_review":false,"require_last_push_approval":false}}]}' ;;
    *) echo '[]' ;;
esac
"""


def _run(tmp_path: Path, *args: str):
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name, body in (("git", GIT_STUB), ("curl", CURL_STUB)):
        (stubs / name).write_text(body, encoding="utf-8")
        (stubs / name).chmod(0o755)
    env = {
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "STUB_DIR": str(tmp_path),
    }
    return subprocess.run(
        ["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, check=False
    )


def test_token_travels_on_stdin_only(tmp_path):
    done = _run(tmp_path, "--check")
    assert done.returncode == 0, done.stderr
    argv = (tmp_path / "argv.log").read_text(encoding="utf-8")
    stdin = (tmp_path / "stdin.log").read_text(encoding="utf-8")
    assert VALUE not in argv
    assert f"Authorization: Bearer {VALUE}" in stdin
    assert VALUE not in done.stdout + done.stderr


def test_requests_with_a_body_also_hide_the_token(tmp_path):
    _run(tmp_path, "--disable")
    argv = (tmp_path / "argv.log").read_text(encoding="utf-8")
    assert "-X PUT" in argv
    assert VALUE not in argv
