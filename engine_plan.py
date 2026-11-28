"""One-button install or update: what has to change, and the order to do it in.

Pure: the pins, the installed state and both actions are handed in by the caller.
Nothing here installs, downloads or starts anything.
"""

from __future__ import annotations

from collections.abc import Callable

STEPS = ("check", "install", "update", "verify")
# Engine state (``Engine.view``) to action; any other state is left alone.
ENGINE_ACTIONS = {
    "not_installed": "install",
    "update": "update",
    "broken": "update",  # the engine's install removes the folder and builds it again
    "installed": "keep",
}


def build_plan(
    pins: dict[str, str],
    installed_version: Callable[[str], str | None],
    is_newer: Callable[[str, str], bool],
    engines: list[dict],
    *,
    busy: str | None = None,
) -> dict:
    """The four steps for the pinned packages and the installable engines.

    ``engines`` holds ``Engine.view`` dicts. A newer installed package is kept,
    never downgraded. ``busy`` is why nothing may change now.
    """
    items = []
    for name, pin in pins.items():
        current = installed_version(name)
        action = "install" if not current else "update" if is_newer(pin, current) else "keep"
        item = {"id": name, "kind": "package", "current": current, "target": pin}
        items.append({**item, "action": action})
    for view in engines:
        if view.get("installable") is False:  # MLX: it comes with the pinned packages
            continue
        action = ENGINE_ACTIONS.get(view["state"], "unsupported")
        item = {"id": view["id"], "kind": "engine", "target": view["version"], "action": action}
        if action == "unsupported":
            item["reason"] = view.get("unsupported") or "This engine cannot be installed here."
        items.append(item)
    targets = {
        step: [item["id"] for item in items if item["action"] == step]
        for step in ("install", "update")
    }
    targets["check"] = [item["id"] for item in items]
    targets["verify"] = targets["install"] + targets["update"]
    return {
        "items": items,
        "steps": [
            {"id": step, "needed": step == "check" or bool(targets[step]), "targets": targets[step]}
            for step in STEPS
        ],
        "needed": bool(targets["verify"]),
        "blocked": busy,
    }


def run_plan(
    check: Callable[[], dict],
    install_packages: Callable[[list[str]], bool],
    install_engine: Callable[[str], bool],
    emit: Callable[[str], None] = lambda line: None,
) -> tuple[bool, str]:
    """Check, install, update, verify; stops at the first failure.

    ``check`` returns a fresh ``build_plan``. Both actions return True on success;
    ``install_packages`` serves a first install and an update alike.
    """
    step = "check"
    try:
        plan = check()
        if plan["blocked"]:
            return False, plan["blocked"]
        if not plan["needed"]:
            return True, "Everything is already up to date."
        emit(f"check: {len(plan['steps'][-1]['targets'])} to change")
        kinds = {item["id"]: item["kind"] for item in plan["items"]}
        changed = []
        for entry in plan["steps"][1:3]:
            step = entry["id"]
            if not entry["needed"]:
                continue
            emit(f"{step}: {', '.join(entry['targets'])}")
            packages = [name for name in entry["targets"] if kinds[name] == "package"]
            done = not packages or install_packages(packages)
            for name in entry["targets"]:
                if done and kinds[name] == "engine":
                    done = install_engine(name)
            if not done:
                return False, f"The {step} step failed. See the lines above."
            changed += entry["targets"]
        step = "verify"
        emit(f"verify: {', '.join(changed)}")
        left = [
            item["id"]
            for item in check()["items"]
            if item["id"] in changed and item["action"] != "keep"
        ]
    except Exception as exc:  # an action's failure ends the run, reported by type only
        return False, f"The {step} step failed ({type(exc).__name__})."
    if left:
        return False, f"Verify failed: {', '.join(left)} still needs a change."
    return True, "Everything is installed and verified."
