#!/usr/bin/env python3
"""Fail CI when test files that must run were skipped, or did not run at all.

pytest exits 0 for a skipped or deselected test, so a missing package or a
filter would hide the checks. Every test function a file defines must be in the
report, and none may be skipped. Run it from the repository root.

Usage: require_no_skips.py JUNIT_XML TEST_FILE [TEST_FILE ...]
"""

from __future__ import annotations

import ast
import sys
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath


def defined_tests(body: list[ast.stmt], prefix: str = "") -> list[str]:
    """Names like test_x or TestY.test_x, by pytest's default naming rules."""
    names = []
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                names.append(prefix + node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            names += defined_tests(node.body, f"{prefix}{node.name}.")
    return names


def problems(report: str, files: list[str]) -> list[str]:
    """One line per skip, per test that did not run and per file with no pass."""
    # The report is written by pytest in the same job, so it is trusted input.
    cases = list(ET.parse(report).getroot().iter("testcase"))  # noqa: S314
    found = []
    for file in files:
        module = ".".join(PurePosixPath(file).with_suffix("").parts)
        passed = 0
        ran = set()
        for case in cases:
            owner = case.get("classname", "")
            name = case.get("name", "")
            # A file skipped as a whole has no classname and is named after itself.
            if not (
                owner == module or owner.startswith(f"{module}.") or (not owner and name == module)
            ):
                continue
            # A parametrized case is named "test_x[...]"; a class follows the module.
            inside = owner.removeprefix(module).lstrip(".")
            ran.add(".".join(filter(None, (inside, name.partition("[")[0]))))
            skip = case.find("skipped")
            if skip is not None:
                found.append(f"{file}: {name} was skipped: {skip.get('message', '')}")
            elif case.find("failure") is None and case.find("error") is None:
                passed += 1
        if not passed:
            found.append(f"{file}: no test passed")
            continue
        try:
            tree = ast.parse(Path(file).read_bytes(), filename=file)
        except (OSError, SyntaxError, ValueError) as exc:
            found.append(f"{file}: cannot read the test file: {exc}")
            continue
        for test in defined_tests(tree.body):
            if test not in ran:
                found.append(f"{file}: {test} did not run")
    return found


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    report, files = argv[0], argv[1:]
    try:
        found = problems(report, files)
    except (OSError, ET.ParseError) as exc:
        found = [f"cannot read the test report {report}: {exc}"]
    for line in found:
        print(f"::error::{line}")
    if found:
        return 1
    print(f"No skips in: {', '.join(files)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
