"""Row 894 (2.0.14 point 7): live memory and speed hold measured values only."""

from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest

import live_stats
from mlx_loadtest import Sample

RAM = 32 * 1024**3
SERVER = Sample(percent=66.66, footprint=9_000, peak=12_000, pressure=2, swap=0)
MAC_ONLY = Sample(percent=50.0, footprint=0, peak=0, pressure=1, swap=0)
UNKNOWN_MEMORY = {
    "mac_total_bytes": None,
    "mac_used_percent": None,
    "pressure": None,
    "server_bytes": None,
    "server_peak_bytes": None,
}


class Probe:
    """Stands in for ``mlx_loadtest.mac_probe``; remembers what it was asked."""

    def __init__(self, server=SERVER, mac=MAC_ONLY):
        self.answers = {True: server, False: mac}
        self.asked: list = []

    def __call__(self, pid):
        self.asked.append(pid)
        answer = self.answers[pid is not None]
        if isinstance(answer, Exception):
            raise answer
        return answer


class TestMemory:
    def test_a_running_server_is_measured(self):
        probe = Probe()
        assert live_stats.memory(probe, 41, RAM) == {
            "mac_total_bytes": RAM,
            "mac_used_percent": 66.7,
            "pressure": "warning",
            "server_bytes": 9_000,
            "server_peak_bytes": 12_000,
        }
        assert probe.asked == [41]

    @pytest.mark.parametrize("pid", [None, 0, -1, True, "41"])
    def test_without_a_server_its_figures_are_unknown_not_zero(self, pid):
        probe = Probe()
        found = live_stats.memory(probe, pid, RAM)
        assert (found["server_bytes"], found["server_peak_bytes"]) == (None, None)
        assert (found["mac_used_percent"], found["pressure"]) == (50.0, "normal")
        assert probe.asked == [None]

    def test_an_unreadable_server_still_shows_the_mac(self):
        probe = Probe(server=OSError("gone"))
        found = live_stats.memory(probe, 41, RAM)
        assert found["mac_used_percent"] == 50.0
        assert (found["server_bytes"], found["server_peak_bytes"]) == (None, None)
        assert probe.asked == [41, None]

    @pytest.mark.parametrize("error", [OSError("no"), AttributeError("no symbol")])
    def test_an_unreadable_mac_is_all_unknown(self, error):
        found = live_stats.memory(Probe(server=error, mac=error), 41, RAM)
        assert found == {**UNKNOWN_MEMORY, "mac_total_bytes": RAM}

    @pytest.mark.parametrize("ram", [0, -5, None, True])
    def test_an_unknown_ram_size_is_unknown(self, ram):
        assert live_stats.memory(Probe(), None, ram)["mac_total_bytes"] is None

    @pytest.mark.parametrize("percent", [0, -1, 100.5, math.nan, math.inf])
    def test_impossible_readings_are_unknown(self, percent):
        odd = Sample(percent=percent, footprint=0, peak=0, pressure=3, swap=0)
        found = live_stats.memory(Probe(server=odd), 41, RAM)
        assert found == {**UNKNOWN_MEMORY, "mac_total_bytes": RAM}


def done(**stats):
    figures = {"tps": 21.5, "first_s": 0.8, "reply_tokens": 40, "prompt_tokens": 12, "mode": "x"}
    # The outer "tps" counts pieces when the server sends no usage: never to be used.
    return {"repo": "org/model", "tps": 999.0, "tokens": 7, "stats": {**figures, **stats}}


class TestChatSpeed:
    def test_the_measured_figures_are_kept_with_the_server_they_came_from(self):
        payload = done()
        before = copy.deepcopy(payload)
        assert live_stats.chat_speed(payload, "mlx", 41, "2026-11-28 10:00:00") == {
            "repo": "org/model",
            "engine": "mlx",
            "pid": 41,
            "at": "2026-11-28 10:00:00",
            "write_tps": 21.5,
            "first_token_s": 0.8,
            "reply_tokens": 40,
            "prompt_tokens": 12,
            "mode": "x",
        }
        assert payload == before

    @pytest.mark.parametrize("tps", [None, 0, 0.0, -3, math.nan, math.inf, True, "21"])
    def test_a_reply_without_a_measured_speed_gives_nothing(self, tps):
        assert live_stats.chat_speed(done(tps=tps), "mlx", 41, "now") is None

    @pytest.mark.parametrize("payload", [None, {}, {"stats": None}, {"tps": 5.0}])
    def test_a_payload_without_figures_gives_nothing(self, payload):
        assert live_stats.chat_speed(payload, "mlx", 41, "now") is None

    def test_other_unknown_figures_stay_unknown(self):
        found = live_stats.chat_speed(
            done(first_s=None, reply_tokens=-1, prompt_tokens=1.5, mode=None), "mlx", 41, "now"
        )
        for key in ("first_token_s", "reply_tokens", "prompt_tokens", "mode"):
            assert found[key] is None


def row(size, status="passed", read=100.0, write=20.0, first=1.5):
    return {
        "size": size,
        "status": status,
        "read_tps": read,
        "write_tps": write,
        "first_token_s": first,
    }


def run(when, rows, repo="org/model", **extra):
    return {"id": when, "repo": repo, "time": when, "rows": rows, **extra}


class TestLoadTestSpeed:
    def test_each_passed_size_is_given_as_measured(self):
        runs = [run("t1", [row(4096), row(8192, write=None), row(16384, status="failed")])]
        assert live_stats.load_test_speed(runs, "org/model", "mlx") == {
            "time": "t1",
            "stale": None,
            "rows": [
                {"size": 4096, "read_tps": 100.0, "write_tps": 20.0, "first_token_s": 1.5},
                {"size": 8192, "read_tps": 100.0, "write_tps": None, "first_token_s": 1.5},
            ],
        }

    def test_the_newest_run_with_a_measured_speed_wins(self):
        runs = [
            run("t1", [row(4096, write=1.0)]),
            run("t2", [row(4096, write=2.0)], stale=True),
            run("t3", [row(4096, status="failed")]),
            run("t4", [row(4096, read=None, write=None)]),
        ]
        found = live_stats.load_test_speed(runs, "org/model", "mlx")
        assert (found["time"], found["stale"], found["rows"][0]["write_tps"]) == ("t2", True, 2.0)

    def test_another_model_or_engine_is_not_borrowed(self):
        runs = [run("t1", [row(4096)], engine="optiq"), run("t2", [row(4096)], repo="org/other")]
        assert live_stats.load_test_speed(runs, "org/model", "mlx") is None
        assert live_stats.load_test_speed(runs, "org/model", "optiq")["time"] == "t1"

    @pytest.mark.parametrize("runs", [[], None, ["junk"], [{"repo": "org/model", "rows": 3}]])
    def test_nothing_usable_is_unknown(self, runs):
        assert live_stats.load_test_speed(runs, "org/model", "mlx") is None

    def test_a_bad_size_or_figure_is_not_shown(self):
        runs = [run("t1", [row("big"), row(4096, read=math.nan, first=-1)])]
        assert live_stats.load_test_speed(runs, "org/model", "mlx")["rows"] == [
            {"size": 4096, "read_tps": None, "write_tps": 20.0, "first_token_s": None}
        ]


class TestSpeed:
    CHAT = live_stats.chat_speed(done(), "mlx", 41, "now")
    RUNS = (run("t1", [row(4096)]),)

    def test_both_sources_stay_apart(self):
        found = live_stats.speed(self.CHAT, list(self.RUNS), "org/model", "mlx", 41)
        assert found["chat"] == self.CHAT
        assert found["test"]["time"] == "t1"

    @pytest.mark.parametrize(
        ("repo", "engine", "pid"),
        [
            ("org/other", "mlx", 41),
            ("org/model", "optiq", 41),
            ("org/model", "mlx", 42),
            ("org/model", "mlx", None),
        ],
    )
    def test_a_chat_figure_of_another_server_is_not_shown(self, repo, engine, pid):
        assert live_stats.speed(self.CHAT, [], repo, engine, pid)["chat"] is None

    def test_no_model_means_nothing_known(self):
        found = live_stats.speed(None, list(self.RUNS), None, None, None)
        assert found == {"chat": None, "test": None}


def test_a_real_saved_run_of_the_app_is_read():
    """The load test writes these keys (mlx_loadtest.py): a rename must fail here."""
    source = (Path(__file__).resolve().parent.parent / "mlx_loadtest.py").read_text()
    for key in ("read_tps=", "write_tps=", "first_token_s=", '"status"] = "passed"'):
        assert key in source
