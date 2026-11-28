"""The Download Model screen's GET routes, answered from ``hub_catalog``.

app.py calls ``get`` only after its Host, Origin and token checks, so none of these
paths may be added to ``OPEN_GETS``.
"""

from __future__ import annotations

import hub_catalog


def _first(query: dict, key: str) -> str:
    return (query.get(key) or [""])[0]


class Routes:
    """``app`` is the running app module; its names are looked up on each request."""

    def __init__(self, app, list_models=None, repo_info=None):
        self._app = app
        self._list_models = list_models
        self._sizes = hub_catalog.SizeChecker(self._fit, repo_info)

    def _fit(self, size_gb: float, ram_bytes: int) -> str:
        return self._app.fit_rating(size_gb, ram_bytes)

    def _installed(self) -> set[str]:
        return {m["repo"] for m in self._app.installed_models() if m["complete"]}

    def get(self, path: str, query: dict) -> tuple[dict, int] | None:
        """``(body, status)``, or None for a path that is not one of these."""
        if path == "/api/catalog":
            hardware = self._app.hardware()
            cards = hub_catalog.curated_cards(
                self._app.MODEL_CATALOG, hardware["ram_bytes"], self._fit, self._installed()
            )
            return {"catalog": cards, "hardware": hardware}, 200
        if path == "/api/search":
            found = hub_catalog.search(
                _first(query, "q"),
                catalog=self._app.MODEL_CATALOG,
                ram_bytes=self._app.hardware()["ram_bytes"],
                fit=self._fit,
                installed=self._installed(),
                list_models=self._list_models,
            )
            return found, 200
        if path == "/api/model-size":
            found = self._sizes.check(_first(query, "repo"), self._app.hardware()["ram_bytes"])
            return (found, 200) if found else ({"error": "unknown model"}, 400)
        return None
