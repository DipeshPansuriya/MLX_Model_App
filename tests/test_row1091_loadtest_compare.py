"""Row 1091, point 8: the latest MLX and OptiQ load tests of one model, side by side."""

from __future__ import annotations

import copy

import pytest

import loadtest_compare as compare

REPO = "org/model"


def _row(size, status="passed", **figures):
    base = {
        "size": size,
        "status": status,
        "reason": "",
        "read_tps": 100.0,
        "write_tps": 20.0,
        "first_token_s": 2.0,
        "follow_up_first_token_s": 0.5,
        "peak_percent": 40.0,
        "server_peak_bytes": 1000,
    }
    return {**base, **figures}


def _run(ident, engine="mlx", *, repo=REPO, status="done", rows=None, sizes=(4096,), **extra):
    fingerprint = {
        "revision": "abc",
        "mlx": "0.32.4",
        "mlx_lm": "0.32.2",
        "ram_bytes": 64,
        "settings": {"server_reasoning": "auto", "prompt_cache_gb": 4},
    }
    record = {
        "id": ident,
        "repo": repo,
        "time": "2026-11-28 10:00:00",
        "status": status,
        "reason": "",
        "sizes": list(sizes),
        "rows": [_row(size) for size in sizes] if rows is None else rows,
        "values": {"reply_tokens": 256, "rounds": 2, "timeout_seconds": 900},
        "fingerprint": fingerprint,
    }
    if engine != "mlx":
        record["engine"] = engine
        fingerprint.update(engine=engine, engine_version="0.8.6")
    return {**record, **extra}


def test_picks_the_last_finished_run_of_each_engine_for_this_model_only():
    runs = [
        _run("m1"),
        _run("o1", "optiq"),
        _run("m2"),
        _run("x", repo="org/other"),
        _run("o2", "optiq", status="running"),
    ]
    view = compare.side_by_side(runs, REPO)
    assert view["runs"]["mlx"]["id"] == "m2"
    assert view["runs"]["optiq"]["id"] == "o1"


def test_same_settings_are_comparable_although_the_engine_differs():
    view = compare.side_by_side([_run("m"), _run("o", "optiq", flags=["--other"])], REPO)
    assert view["comparable"] is True
    assert view["reasons"] == []


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda r: r.update(sizes=[8192]), "tested sizes"),
        (lambda r: r.update(thinking=False), "thinking"),
        (lambda r: r["values"].update(reply_tokens=512), "reply_tokens"),
        (lambda r: r["values"].update(rounds=3), "rounds"),
        (lambda r: r["fingerprint"].update(revision="def"), "model revision"),
        (lambda r: r["fingerprint"].update(mlx="0.33.0"), "mlx version"),
        (lambda r: r["fingerprint"].update(mlx_lm="0.33.0"), "mlx-lm version"),
        (lambda r: r["fingerprint"].update(ram_bytes=32), "memory of this Mac"),
        (lambda r: r["fingerprint"]["settings"].update(prompt_cache_gb=8), "prompt_cache_gb"),
        (lambda r: r["fingerprint"]["settings"].pop("server_reasoning"), "server_reasoning"),
    ],
)
def test_any_different_setting_flags_the_pair_as_not_comparable(change, reason):
    other = copy.deepcopy(_run("o", "optiq"))
    change(other)
    view = compare.side_by_side([_run("m"), other], REPO)
    assert view["comparable"] is False
    assert view["reasons"] == [f"Different: {reason}"]


@pytest.mark.parametrize("missing", ["fingerprint", "values"])
def test_a_run_without_recorded_settings_is_not_comparable(missing):
    other = _run("o", "optiq")
    del other[missing]
    view = compare.side_by_side([_run("m"), other], REPO)
    assert view["comparable"] is False
    assert view["reasons"] == ["Settings were not recorded for both runs"]


def test_one_engine_only_says_which_run_is_missing():
    view = compare.side_by_side([_run("m")], REPO)
    assert view["runs"]["optiq"] is None
    assert view["comparable"] is False
    assert view["reasons"] == ["No finished OptiQ run for this model"]
    assert view["rows"][0]["optiq"] is None


def test_rows_are_joined_by_size_and_name_the_better_engine():
    mlx = _run("m", sizes=(4096, 8192), rows=[_row(8192), _row(4096)])
    optiq = _run(
        "o",
        "optiq",
        sizes=(4096, 8192),
        rows=[_row(4096, write_tps=30.0, first_token_s=3.0), _row(8192, "failed")],
    )
    view = compare.side_by_side([mlx, optiq], REPO)
    assert [row["size"] for row in view["rows"]] == [4096, 8192]
    first, second = view["rows"]
    # Equal figures have no winner; more tokens per second and less waiting win.
    assert first["better"] == {"write_tps": "optiq", "first_token_s": "mlx"}
    assert second["optiq"]["status"] == "failed"
    assert second["better"] == {}


def test_no_winner_is_named_when_the_runs_are_not_comparable():
    optiq = _run("o", "optiq", rows=[_row(4096, write_tps=30.0)], thinking=True)
    view = compare.side_by_side([_run("m"), optiq], REPO)
    assert view["rows"][0]["better"] == {}


def test_damaged_stored_values_are_ignored():
    rows = [None, {"size": "big"}, _row(4096, write_tps="fast", read_tps=float("nan"))]
    optiq = _run("o", "optiq", rows=rows)
    view = compare.side_by_side(["junk", {}, _run("m"), optiq], REPO)
    assert [row["size"] for row in view["rows"]] == [4096]
    assert view["rows"][0]["better"] == {}


def test_history_is_trimmed_per_model_and_engine_in_order():
    runs = [_run("o1", "optiq")] + [_run(f"m{n}") for n in range(4)]
    runs.append(_run("x", repo="org/other"))
    kept = compare.trim_history(runs, 2)
    assert [run["id"] for run in kept] == ["o1", "m2", "m3", "x"]


def test_it_reads_what_the_app_really_stores(app_module):
    app_module.APP_DIR.mkdir(parents=True, exist_ok=True)
    for record in (_run("m"), _run("o", "optiq")):
        app_module._save_loadtest(record)
    view = compare.side_by_side(app_module._stored_runs(REPO), REPO)
    assert view["comparable"] is True
    assert view["runs"]["optiq"]["id"] == "o"
