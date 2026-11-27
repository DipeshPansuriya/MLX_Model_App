"""Rows 884 and 885: context sizes up to 1M, long time limit, the Thinking option (fakes)."""

import json

import pytest
from test_row848_loadtest import REPO, api, job, lt, run_job  # noqa: F401 - fixtures

BIG = (65_536, 131_072, 262_144, 524_288, 1_048_576)


def test_sizes_up_to_1m_are_offered_and_all_fit_in_one_run(api):  # noqa: F811
    app = api.module
    offered = app.load_test_view()["sizes"]["value"]
    assert offered == [4096, 8192, 16384, 32768, *BIG]
    assert app._valid_sizes([*offered, 1_000_000], 2_000_000)
    shown = api.get(f"/api/loadtest?repo={REPO}")[1]["sizes"]["offered"]
    # This model takes 16384: the larger ones are offered but not allowed.
    assert {"size": 1_048_576, "allowed": False} in shown


def test_a_saved_list_from_before_stays_the_users_own(api):  # noqa: F811
    app = api.module
    old = [4096, 8192, 16384, 32768, 5000, 6000, 7000, 9000]
    status, _ = api.post("/api/server/settings", {"load_test": {"sizes": old}})
    assert status == 200 and app.load_test_view()["sizes"]["value"] == old


@pytest.mark.parametrize(("size", "limit"), [(32_768, 60), (65_536, 3600), (1_048_576, 3600)])
def test_a_large_size_gets_the_longest_time_limit(lt, tmp_path, size, limit):  # noqa: F811
    runner = lt.Runner(job(tmp_path), print, spawn=None, probe=None, clock=None, count_tokens=None)
    assert runner.time_limit(size) == limit


def test_a_size_at_the_models_limit_leaves_room_for_the_replies(lt, tmp_path):  # noqa: F811
    status, _, rows, _, _ = run_job(
        lt,
        tmp_path,
        sizes=(4096,),
        mode="full-limit-4096",
        reply_tokens=256,
        context_limit=4096,
    )
    assert (status, rows[4096]["status"]) == ("done", "passed"), rows[4096]["reason"]
    assert rows[4096]["prompt_tokens"] <= 4096 - 256


def test_many_short_lines_do_not_recount_once_per_line(lt):  # noqa: F811
    calls = []

    def count(text):  # a joined text counts more than its parts, line by line
        calls.append(text.count("def block_") > 1)
        return len(text.split()) + text.count("\n\ndef")

    prompt = lt.make_prompt(20_000, "run", "label", count)
    assert sum(calls) < 10 and 19_000 < count(prompt) <= 20_000


@pytest.mark.parametrize("thinking", [True, False, None])
def test_the_request_names_thinking_only_when_chosen(lt, tmp_path, monkeypatch, thinking):  # noqa: F811
    bodies, real = [], lt.ask

    def ask(port, body, *args):
        bodies.append(body)
        return real(port, body, *args)

    monkeypatch.setattr(lt, "ask", ask)
    extra = {} if thinking is None else {"thinking": thinking}
    run_job(lt, tmp_path, sizes=(4096,), **extra)
    wanted = None if thinking is None else {"enable_thinking": thinking}
    assert bodies and all(b.get("chat_template_kwargs") == wanted for b in bodies)


def _template(app, text):
    folder = app._snapshot_dir(REPO)
    (folder / "tokenizer_config.json").write_text(json.dumps({"chat_template": text}), "utf-8")


class TestThinking:
    def start(self, api, **extra):  # noqa: F811
        return api.post("/api/loadtest/start", {"repo": REPO, "sizes": [4096], **extra})

    def test_a_template_without_the_switch_disables_it_with_the_reason(self, api):  # noqa: F811
        _template(api.module, "{{ messages }}")
        shown = api.get(f"/api/loadtest?repo={REPO}")[1]["thinking"]
        assert shown["supported"] is False and "enable_thinking" in shown["reason"]
        status, body = self.start(api, thinking=True)
        assert status == 400 and "enable_thinking" in body["error"]
        assert self.start(api)[0] == 200
        api.finished()
        assert "thinking" not in api.get(f"/api/loadtest?repo={REPO}")[1]["results"][-1]

    @pytest.mark.parametrize("name", ["chat_template.jinja", "chat_template.json", "list"])
    def test_every_place_a_template_is_kept_is_read(self, api, name):  # noqa: F811
        app, text = api.module, "{% if enable_thinking %}x{% endif %}"
        folder = app._snapshot_dir(REPO)
        if name == "list":
            _template(app, [{"name": "default", "template": text}])
        elif name.endswith("json"):
            (folder / name).write_text(json.dumps({"chat_template": text}), "utf-8")
        else:
            (folder / name).write_text(text, "utf-8")
        assert api.get(f"/api/loadtest?repo={REPO}")[1]["thinking"]["supported"] is True

    def test_the_result_records_it_and_other_runs_are_not_mixed_in(self, api):  # noqa: F811
        app = api.module
        _template(app, "{% if enable_thinking %}x{% endif %}")
        assert self.start(api, thinking="yes")[0] == 400
        assert self.start(api, thinking=False)[0] == 200
        api.finished()
        result = api.get(f"/api/loadtest?repo={REPO}")[1]["results"][-1]
        assert result["thinking"] is False and result["stale"] is False
        for wanted, found in ((False, True), (True, False)):
            built, _ = app._loadtest_job(REPO, [4096], "r2", app.APP_DIR, "mlx", wanted)
            assert built["thinking"] is wanted and bool(built["priors"]) is found
