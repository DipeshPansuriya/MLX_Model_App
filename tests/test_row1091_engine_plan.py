"""Row 1091, point 21: the plan behind one-button install or update. Nothing is installed."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import engine_plan
import engines

ROOT = Path(__file__).resolve().parent.parent
PINS = {"mlx": "0.32.4", "mlx-lm": "0.32.2"}


def _newer(candidate, current):
    return tuple(map(int, candidate.split("."))) > tuple(map(int, current.split(".")))


def _engine(state, ident="optiq", **extra):
    return {"id": ident, "name": "OptiQ", "version": "0.8.6", "state": state, **extra}


def _plan(installed=None, found=(), busy=None):
    have = dict(PINS if installed is None else installed)
    return engine_plan.build_plan(PINS, have.get, _newer, list(found), busy=busy)


def _steps(plan):
    return {step["id"]: step["targets"] for step in plan["steps"]}


def test_the_plan_always_has_the_four_steps_in_order():
    plan = _plan()
    assert [step["id"] for step in plan["steps"]] == ["check", "install", "update", "verify"]
    assert [step["needed"] for step in plan["steps"]] == [True, False, False, False]
    assert plan["needed"] is False


def test_missing_old_current_and_newer_packages():
    plan = _plan({"mlx": "0.31.0"})
    assert _steps(plan) == {
        "check": ["mlx", "mlx-lm"],
        "install": ["mlx-lm"],
        "update": ["mlx"],
        "verify": ["mlx-lm", "mlx"],
    }
    assert plan["needed"] is True
    # A newer installed release is never downgraded.
    assert _plan({"mlx": "0.40.0", "mlx-lm": "0.32.2"})["needed"] is False


@pytest.mark.parametrize(
    ("state", "action"),
    [
        ("not_installed", "install"),
        ("update", "update"),
        ("broken", "update"),
        ("installed", "keep"),
        ("unsupported", "unsupported"),
        ("something new", "unsupported"),
    ],
)
def test_each_engine_state_has_one_action(state, action):
    item = _plan(found=[_engine(state)])["items"][-1]
    assert (item["id"], item["kind"], item["action"]) == ("optiq", "engine", action)
    assert item["target"] == "0.8.6"


def test_an_unsupported_engine_says_why_and_changes_nothing():
    plan = _plan(found=[_engine("unsupported", unsupported="needs Python 3.15")])
    assert plan["items"][-1]["reason"] == "needs Python 3.15"
    assert plan["needed"] is False


def test_the_real_pins_and_the_real_engine_view_fit(tmp_path):
    tree = ast.parse((ROOT / "app.py").read_text("utf-8"))
    pins = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and getattr(node.targets[0], "id", None) == "PINNED_PACKAGES"
    )
    view = engines.get("optiq").view(tmp_path / "none")
    plan = engine_plan.build_plan(pins, lambda name: None, _newer, [view])
    assert _steps(plan)["install"][: len(pins)] == list(pins)
    assert plan["items"][-1]["action"] in ("install", "unsupported")
    assert plan["items"][-1]["target"] == engines.get("optiq").version
    assert list(tmp_path.iterdir()) == []


class Mac:
    """Fake installed state; the two actions change it as the app's workers would."""

    def __init__(self, installed, engine_state, *, fail=None, lie=False):
        self.installed, self.engine_state = dict(installed), engine_state
        self.fail, self.lie, self.calls, self.lines = fail, lie, [], []
        self.busy = None

    def check(self):
        return _plan(self.installed, [_engine(self.engine_state)], self.busy)

    def packages(self, names):
        self.calls.append(("packages", names))
        if self.fail == "packages":
            return False
        if not self.lie:
            self.installed.update({name: PINS[name] for name in names})
        return True

    def engine(self, ident):
        self.calls.append(("engine", ident))
        if self.fail == "engine":
            raise RuntimeError("no network")
        self.engine_state = "installed"
        return True

    def run(self):
        return engine_plan.run_plan(self.check, self.packages, self.engine, self.lines.append)


def test_run_installs_then_updates_then_verifies():
    mac = Mac({"mlx": "0.31.0"}, "not_installed")
    assert mac.run() == (True, "Everything is installed and verified.")
    assert mac.calls == [("packages", ["mlx-lm"]), ("engine", "optiq"), ("packages", ["mlx"])]
    assert [line.split(":")[0] for line in mac.lines] == ["check", "install", "update", "verify"]


def test_nothing_to_do_calls_nothing():
    mac = Mac(PINS, "installed")
    assert mac.run() == (True, "Everything is already up to date.")
    assert mac.calls == []


@pytest.mark.parametrize("fail", ["packages", "engine"])
def test_the_first_failure_stops_the_run(fail):
    mac = Mac({"mlx": "0.31.0"}, "not_installed", fail=fail)
    done, message = mac.run()
    assert done is False
    assert message.startswith("The install step failed")
    assert mac.calls[-1][0] == fail
    assert ("packages", ["mlx"]) not in mac.calls


def test_verify_fails_when_an_action_reports_success_but_nothing_changed():
    mac = Mac({"mlx": "0.31.0", "mlx-lm": "0.32.2"}, "installed", lie=True)
    assert mac.run() == (False, "Verify failed: mlx still needs a change.")


def test_a_busy_app_blocks_the_run():
    mac = Mac({}, "not_installed")
    mac.busy = "Stop the API server first."
    assert mac.check()["blocked"] == "Stop the API server first."
    assert mac.run() == (False, "Stop the API server first.")
    assert mac.calls == []


def test_a_failed_check_is_reported():
    def check():
        raise OSError("disk")

    done, message = engine_plan.run_plan(check, None, None)
    assert (done, message) == (False, "The check step failed (OSError).")


def test_an_engine_that_cannot_be_installed_is_left_to_its_packages(tmp_path):
    views = [engines.get("mlx").view(tmp_path / "none")]
    plan = _plan(found=views)
    assert [item["id"] for item in plan["items"]] == list(PINS)
