"""Every module at the root ships: in the bundle, the release record and each source check."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULES = sorted(path.name for path in ROOT.glob("*.py"))


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def names(text: str) -> set[str]:
    return set(re.findall(r"[a-z_]+\.py", text))


@pytest.mark.parametrize("module", MODULES)
def test_the_build_copies_the_module(module):
    line = f'cp "$ROOT/{module}" "$APP/Contents/Resources/{module}"'
    assert line in read("packaging/build_app.sh")


@pytest.mark.parametrize("block", ["SHIPPED", "BUNDLED"])
def test_the_release_record_lists_every_module(block):
    found = re.search(rf"^{block} = \((.*?)^\)", read("packaging/release_manifest.py"), re.M | re.S)
    assert set(MODULES) <= names(found[1])


def test_the_audit_checks_every_module():
    found = re.search(r"^SOURCES=\((.*?)\)", read("scripts/security_audit.sh"), re.M | re.S)
    assert set(MODULES) <= names(found[1])


@pytest.mark.parametrize("workflow", ["quality.yml", "pr-review.yml"])
def test_ci_compiles_every_module(workflow):
    text = read(f".github/workflows/{workflow}").replace("\\\n", " ")
    lines = [line for line in text.splitlines() if "py_compile app.py" in line]
    assert lines and all(set(MODULES) <= names(line) for line in lines)


def test_the_clean_clone_build_copies_every_module():
    assert set(MODULES) <= names(read("tests/test_c1_h4_build.py"))
