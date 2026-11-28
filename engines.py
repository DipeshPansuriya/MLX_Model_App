"""The engines the app ships. To add one: a class here, then one entry in REGISTRY.

``mlx`` is mlx_lm's own server (its command stays in ``app.py``). A ``direct``
engine is the publisher's own server behind a per-launch key; the app drives each of
them through the same methods. A planned engine has no command and no install.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import engine_runtime as runtime
import model_info
import optiq_engine

NOT_AVAILABLE = "Not available"


# --------------------------------------------------------------------------- #
# Served names, by file layout only: no wrapper and no proxy
# --------------------------------------------------------------------------- #
def _check_name(name: str) -> None:
    if model_info.alias_problem(name, longest=model_info.SERVED_NAME_MAX):
        raise runtime.Refused(f"Not served: {name!r} cannot be used as a model name.")


def link_model(folder: Path, name: str, snapshot: Path) -> None:
    """Make ``folder/name`` a link to ``snapshot``.

    A server started in ``folder`` with ``--model name`` lists and answers to ``name``.
    The links of earlier starts are removed; nothing else ever is.
    """
    _check_name(name)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    for entry in folder.iterdir():
        if entry.is_symlink() and entry.name.lower() not in model_info.RESERVED_NAMES:
            entry.unlink()
    target = folder / name
    if os.path.lexists(target):
        raise runtime.Refused(
            f"Not served: {target} is in the way. Remove it or choose another alias."
        )
    target.symlink_to(os.path.abspath(snapshot), target_is_directory=True)


def alias_cache(root: Path, name: str, snapshot: Path) -> tuple[Path, Path]:
    """``(Hub cache holding only name, empty folder to run in)``, both under ``root``.

    mlx_lm lists and loads a cached model by its cache folder's name, so this is the
    whole alias. Each entry is a link to a file of ``snapshot``, which is only read.
    The folder is built anew at each start.
    """
    _check_name(name)
    if snapshot.parent.name != "snapshots":
        raise runtime.Refused("Not served: this model is not in the model cache.")
    hub, run = root / "hub", root / "run"
    for folder in (root, run):
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder.chmod(0o700)
    if hub.is_symlink() or run.is_symlink():
        raise runtime.Refused(f"Not served: remove the links in {root}.")
    # mlx_lm takes a file or folder here, with the model's name, for the model.
    if any(run.iterdir()):
        raise runtime.Refused(f"Not served: {run} must be empty. Remove what is in it.")
    if hub.exists():
        shutil.rmtree(hub)  # removes links, never what they point to
    entry = hub / f"models--{name}"
    view = entry / "snapshots" / snapshot.name
    view.mkdir(mode=0o700, parents=True)
    (entry / "refs").mkdir()
    (entry / "refs" / "main").write_text(snapshot.name, encoding="utf-8")
    # One link per file: the cache's own links are relative and would break if the
    # snapshot folder as a whole were linked.
    for folder, folders, files in os.walk(snapshot):
        here = view / os.path.relpath(folder, snapshot)
        here.mkdir(exist_ok=True)
        linked = [n for n in folders if os.path.islink(os.path.join(folder, n))]
        for item in [*files, *linked]:
            (here / item).symlink_to(os.path.realpath(os.path.join(folder, item)))
    return hub, run


class Engine:
    id = name = ""
    selectable = True
    direct = False  # a keyed server of its own port, started by its own command
    installable = False  # Setup offers Install / Update and Remove
    anthropic = False  # serves Anthropic's /v1/messages
    refusal: str | None = None  # why it can never be started
    version: str | None = None
    port: int | None = None
    # (what, why it is missing): protections and features this engine's server lacks.
    missing: tuple[tuple[str, str], ...] = ()
    setting_keys: tuple[str, ...] = ()  # the server settings its command takes

    def state(self, app_dir: Path) -> str:
        return "installed"

    def view(self, app_dir: Path) -> dict:
        """What the screens need to know; plain data."""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "port": self.port,
            "selectable": self.selectable,
            "direct": self.direct,
            "installable": self.installable,
            "anthropic": self.anthropic,
            "state": self.state(app_dir),
            "reason": self.refusal,
            "missing": [list(entry) for entry in self.missing],
        }


class Mlx(Engine):
    id = "mlx"
    name = "MLX (built in)"
    request_model = "default_model"  # mlx_lm's name for the model it was started with
    setting_keys = (
        "server_max_reply_tokens",
        "prompt_cache_size",
        "prompt_cache_bytes",
        "server_reasoning",
    )
    missing = (
        ("API key", "any program on this Mac can use the server"),
        ("Max context setting and long prompt refusal", NOT_AVAILABLE),
        ("Memory limit", NOT_AVAILABLE),
        ("MLX cache limit setting", NOT_AVAILABLE),
        ("Reply token cap", "the setting is a default only"),
        ("Two models in one server", "one model"),
        ("Null tool argument fix", NOT_AVAILABLE),
        ("Web page checks (Host, JSON only)", "only the allowed origins list"),
        (
            "Refusal of other models, draft models, adapters and file paths",
            "a request can load another model or folder on this Mac",
        ),
        ("Request size limits, loaded model and memory readout", NOT_AVAILABLE),
    )


class Optiq(Engine):
    id = "optiq"
    name = "OptiQ"
    direct = True
    installable = True
    anthropic = True
    version = optiq_engine.VERSION
    key_env = optiq_engine.KEY_ENV
    request_model = "default_model"
    setting_keys = (*(key for key, _ in optiq_engine.SETTING_FLAGS), "server_reasoning")
    missing = (
        ("Memory limit", NOT_AVAILABLE),
        ("MLX cache limit setting", "not passed to this engine"),
        ("Long prompt refusal", "older text is dropped beyond the max context"),
        ("Reply token cap", "the setting is a default only"),
        ("Two models in one server", "one model"),
        ("Null tool argument fix", NOT_AVAILABLE),
        ("Web page checks (Host, Origin, JSON only)", "the API key is the only gate"),
        ("Refusal of draft models, adapters and file paths", "a key holder can send them"),
        ("Image models", "refused"),
        ("Open without the key", "/health, /v1/optiq/context and OPTIONS"),
        ("Request size limits, loaded model and memory readout", NOT_AVAILABLE),
    )

    @property
    def port(self) -> int:
        return optiq_engine.PORT

    def state(self, app_dir: Path) -> str:
        return optiq_engine.state(app_dir)

    def view(self, app_dir: Path) -> dict:
        problem = optiq_engine.python_problem()
        return {**super().view(app_dir), "unsupported": problem}

    def program(self, app_dir: Path) -> Path:
        return optiq_engine.python_path(app_dir)

    def check_model(self, repo: str, snapshot: Path) -> None:
        optiq_engine.check_model(repo, snapshot)

    def command(self, program, model, port, **options) -> list[str]:
        return optiq_engine.command(program, model, port, **options)

    def environment(self, root: Path, key: str) -> dict[str, str]:
        return optiq_engine.environment(root, key)

    def identity(self, model) -> str:
        """The part of the command line that shows a process is this server.

        ``model`` is what ``--model`` was given: a served name, or a snapshot folder.
        """
        return f" -m optiq.cli serve --model {model} --host "

    def new_key(self) -> str:
        return runtime.new_key()

    def read_key(self, path: Path) -> str | None:
        return optiq_engine.read_key(path)

    def install(self, app_dir: Path, emit, **options) -> None:
        optiq_engine.install(app_dir, emit, **options)

    def remove(self, app_dir: Path) -> None:
        optiq_engine.remove(app_dir)


class Planned(Engine):
    """Shown, never started: it has not passed the security review."""

    selectable = False
    refusal = "Not available yet: security review pending."

    def __init__(self, ident: str, name: str) -> None:
        self.id, self.name = ident, name

    def state(self, app_dir: Path) -> str:
        return "unavailable"


MLX = Mlx()
OPTIQ = Optiq()
REGISTRY: tuple[Engine, ...] = (
    MLX,
    OPTIQ,
    Planned("splash", "Splash"),
    Planned("rapid-mlx", "Rapid-MLX"),
)


def get(ident) -> Engine | None:
    """The engine called ``ident``; no name means MLX."""
    if ident is None:
        return MLX
    return next((engine for engine in REGISTRY if engine.id == ident), None)
