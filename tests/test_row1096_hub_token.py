"""Row 1096: the saved Hugging Face token goes out only where a model needs it."""

from __future__ import annotations

import sys
import types

import pytest

REPO = "org/model"


class Refused(Exception):
    """Like huggingface_hub's HTTP errors: the status is on the response."""

    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.response = types.SimpleNamespace(status_code=status)


@pytest.fixture
def hub(monkeypatch):
    """A fake Hub. ``token=None`` is the library's default: the saved token is sent."""
    seen = types.SimpleNamespace(
        asked=[], searched=[], fetched=[], anonymous=None, gated=False, private=False
    )

    class Api:
        def __init__(self, token=None):
            self.token = token

        def list_models(self, **kwargs):
            seen.searched.append(self.token)
            return []

        def repo_info(self, repo_id, **kwargs):
            seen.asked.append(self.token)
            if self.token is False and seen.anonymous:
                raise seen.anonymous
            return types.SimpleNamespace(
                siblings=[types.SimpleNamespace(rfilename="model.safetensors", size=1)],
                sha="abc",
                gated=seen.gated,
                private=seen.private,
            )

    def download(token=None, **kwargs):
        seen.fetched.append(token)

    module = types.ModuleType("huggingface_hub")
    module.HfApi = Api
    module.hf_hub_download = download
    monkeypatch.setitem(sys.modules, "huggingface_hub", module)
    return seen


def status(app_module):
    return app_module.STATE.downloads[REPO]["status"]


def test_search_never_sends_the_token(app_module, hub):
    app_module.search_hf("qwen")
    assert hub.searched == [False]


def test_a_public_model_is_downloaded_without_the_token(app_module, hub):
    app_module.worker_download(REPO)
    assert (hub.asked, hub.fetched, status(app_module)) == ([False], [False], "success")


@pytest.mark.parametrize("gated", ["auto", "manual"])
def test_a_gated_model_is_downloaded_with_the_token(app_module, hub, gated):
    hub.gated = gated
    app_module.worker_download(REPO)
    assert (hub.asked, hub.fetched, status(app_module)) == ([False, None], [None], "success")


@pytest.mark.parametrize("code", [401, 403, 404])
def test_a_model_hidden_from_strangers_is_asked_for_with_the_token(app_module, hub, code):
    hub.anonymous, hub.private = Refused(code), True
    app_module.worker_download(REPO)
    assert (hub.asked, hub.fetched, status(app_module)) == ([False, None], [None], "success")


@pytest.mark.parametrize("error", [Refused(500), Refused(429), OSError("offline")])
def test_another_failure_does_not_bring_out_the_token(app_module, hub, error):
    hub.anonymous = error
    app_module.worker_download(REPO)
    assert (hub.asked, hub.fetched, status(app_module)) == ([False], [], "failed")
