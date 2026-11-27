"""The engines the app ships. To add one: a class here, then one entry in REGISTRY.

``mlx`` is built in (``mlx_guard.py``; its command stays in ``app.py``). A ``direct``
engine is the publisher's own server behind a per-launch key; the app drives each of
them through the same methods. A planned engine has no command and no install.
"""

from __future__ import annotations

from pathlib import Path

import engine_runtime as runtime
import optiq_engine

NOT_AVAILABLE = "Not available"


class Engine:
    id = name = ""
    selectable = True
    direct = False  # a keyed server of its own port, started by its own command
    installable = False  # Setup offers Install / Update and Remove
    anthropic = False  # serves Anthropic's /v1/messages
    refusal: str | None = None  # why it can never be started
    version: str | None = None
    port: int | None = None
    # (what, why it is missing) for everything the built-in MLX server does and this not.
    missing: tuple[tuple[str, str], ...] = ()

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


class Optiq(Engine):
    id = "optiq"
    name = "OptiQ"
    direct = True
    installable = True
    anthropic = True
    version = optiq_engine.VERSION
    key_env = optiq_engine.KEY_ENV
    request_model = "default_model"
    missing = (
        ("80% memory limit and memory stop", "only the engine's own estimate"),
        ("Memory limit and MLX cache limit settings", "not passed to this engine"),
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

    def command(self, program, snapshot, port, **options) -> list[str]:
        return optiq_engine.command(program, snapshot, port, **options)

    def environment(self, root: Path, key: str) -> dict[str, str]:
        return optiq_engine.environment(root, key)

    def identity(self, snapshot) -> str:
        """The part of the command line that shows a process is this server."""
        return f" -m optiq.cli serve --model {snapshot} --host "

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
