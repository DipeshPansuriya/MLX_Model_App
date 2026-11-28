"""Load Test side by side: the latest MLX and OptiQ runs of one model.

Pure: it is handed the stored run records and reads and writes nothing.
"""

from __future__ import annotations

import math

ENGINES = {"mlx": "MLX", "optiq": "OptiQ"}
# Row figure: whether a higher value is the better one.
FIGURES = {
    "read_tps": True,
    "write_tps": True,
    "first_token_s": False,
    "follow_up_first_token_s": False,
    "peak_percent": False,
    "server_peak_bytes": False,
}
# Fingerprint entries that must match. The engine and its version differ by design.
ENVIRONMENT = {
    "revision": "model revision",
    "mlx": "mlx version",
    "mlx_lm": "mlx-lm version",
    "ram_bytes": "memory of this Mac",
}


def engine_of(run: dict) -> str:
    return run.get("engine") or "mlx"  # MLX records carry no engine name


def trim_history(runs: list[dict], kept: int) -> list[dict]:
    """The last ``kept`` runs of each model and engine, in their stored order."""
    seen: dict[tuple, int] = {}
    out = []
    for run in reversed(runs):
        key = (run["repo"], engine_of(run))
        seen[key] = seen.get(key, 0) + 1
        if seen[key] <= kept:
            out.append(run)
    return out[::-1]


def _differences(mlx: dict, optiq: dict) -> list[str]:
    """Why the two runs may not be compared; empty when their settings match."""
    prints = [run.get("fingerprint") for run in (mlx, optiq)]
    values = [run.get("values") for run in (mlx, optiq)]
    if not all(isinstance(part, dict) for part in (*prints, *values)):
        return ["Settings were not recorded for both runs"]
    settings = [part.get("settings") for part in prints]
    if not all(isinstance(part, dict) for part in settings):
        return ["Settings were not recorded for both runs"]
    absent = object()

    def unequal(pair, key) -> bool:
        return pair[0].get(key, absent) != pair[1].get(key, absent)

    found = []
    if mlx.get("sizes") != optiq.get("sizes"):
        found.append("tested sizes")
    if mlx.get("thinking") != optiq.get("thinking"):
        found.append("thinking")
    found += [key for key in sorted({*values[0], *values[1]}) if unequal(values, key)]
    found += [label for key, label in ENVIRONMENT.items() if unequal(prints, key)]
    found += [key for key in sorted({*settings[0], *settings[1]}) if unequal(settings, key)]
    return [f"Different: {name}" for name in found]


def _rows(run: dict | None) -> dict[int, dict]:
    rows = (run or {}).get("rows")
    return {
        row["size"]: row
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict) and type(row.get("size")) is int
    }


def _better(mlx: dict | None, optiq: dict | None) -> dict[str, str]:
    """The winning engine of each figure, where both sizes passed and differ."""
    if not all(row and row.get("status") == "passed" for row in (mlx, optiq)):
        return {}
    winners = {}
    for key, higher in FIGURES.items():
        first, second = mlx.get(key), optiq.get(key)
        numbers = all(type(v) in (int, float) and math.isfinite(v) for v in (first, second))
        if numbers and first != second:
            winners[key] = "mlx" if (first > second) == higher else "optiq"
    return winners


def side_by_side(runs: list, repo: str) -> dict:
    """The last finished run of each engine for ``repo``, joined by size.

    ``runs`` are in stored order, the newest last. No winner is named unless
    ``comparable`` is true.
    """
    latest: dict[str, dict | None] = dict.fromkeys(ENGINES)
    for run in runs:
        if not isinstance(run, dict) or run.get("repo") != repo:
            continue
        if run.get("status") != "running" and engine_of(run) in ENGINES:
            latest[engine_of(run)] = run
    reasons = [
        f"No finished {name} run for this model"
        for key, name in ENGINES.items()
        if latest[key] is None
    ] or _differences(latest["mlx"], latest["optiq"])
    by_size = {key: _rows(run) for key, run in latest.items()}
    rows = []
    for size in sorted({size for found in by_size.values() for size in found}):
        pair = {key: by_size[key].get(size) for key in ENGINES}
        rows.append({"size": size, **pair, "better": {} if reasons else _better(**pair)})
    return {
        "repo": repo,
        "runs": latest,
        "comparable": not reasons,
        "reasons": reasons,
        "rows": rows,
    }
