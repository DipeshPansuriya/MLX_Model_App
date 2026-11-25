"""M15: CI builds the app on macOS and gates releases; SA-12 is hard at release."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUALITY = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
AUDIT = (ROOT / "scripts" / "security_audit.sh").read_text(encoding="utf-8")


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


def test_audit_has_a_release_mode_that_fails_hard():
    assert "--release" in AUDIT
    assert "release_manifest.py --verify --release --dist dist" in AUDIT
    assert 'bad SA-12 "release gate' in AUDIT
