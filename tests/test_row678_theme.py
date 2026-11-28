"""Row 678: System / Light / Dark theme, and the ``ui`` block of the settings file.

Plan: row1045-2.0.14 WP1. AppKit and pywebview are replaced; no window opens.
"""

from __future__ import annotations

import json
import re
import stat
import sys
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_ui_behaviour import needs_node, run

UI = Path(__file__).resolve().parent.parent / "ui"
STYLE = (UI / "style.css").read_text("utf-8")
PAGE = (UI / "index.html").read_text("utf-8")
SCRIPT = (UI / "app.js").read_text("utf-8")

# ─────────────────────────── the settings file ───────────────────────────


def stored(app) -> dict:
    return json.loads((app.APP_DIR / "settings.json").read_text("utf-8"))


def write_raw(app, data) -> None:
    app.APP_DIR.mkdir(parents=True, exist_ok=True)
    (app.APP_DIR / "settings.json").write_text(json.dumps(data), encoding="utf-8")


class TestStore:
    def test_the_default_is_system_and_reading_writes_nothing(self, app_module):
        assert app_module.ui_settings() == {"theme": "system", "save_history": "on"}
        assert not (app_module.APP_DIR / "settings.json").exists()

    @pytest.mark.parametrize("theme", ["light", "dark", "system"])
    def test_a_saved_theme_is_read_back_from_a_private_file(self, app_module, theme):
        app_module.save_ui({"theme": theme})
        assert app_module.ui_settings() == {"theme": theme, "save_history": "on"}
        assert stored(app_module) == {"ui": {"theme": theme}}
        mode = (app_module.APP_DIR / "settings.json").stat().st_mode
        assert stat.S_IMODE(mode) == 0o600

    def test_model_and_load_test_writes_keep_the_ui_block(self, app_module):
        app_module.save_ui({"theme": "light"})
        app_module.save_settings({"max_context_tokens": 24000}, "org/model")
        app_module.save_settings({"max_context_tokens": 16000})
        app_module.save_load_test({"rounds": 1})
        assert app_module.ui_settings() == {"theme": "light", "save_history": "on"}
        app_module.reset_settings("org/model")
        app_module.reset_settings()
        app_module.save_load_test(None)
        # Nothing else is left: the file stays for the ui block.
        assert stored(app_module) == {"ui": {"theme": "light"}}

    def test_saving_the_theme_keeps_every_other_setting(self, app_module):
        app_module.save_settings({"max_context_tokens": 24000}, "org/model")
        app_module.save_load_test({"rounds": 1})
        before = stored(app_module)
        app_module.save_ui({"theme": "dark"})
        assert stored(app_module) == {**before, "ui": {"theme": "dark"}}

    def test_a_reset_with_nothing_at_all_left_still_removes_the_file(self, app_module):
        app_module.save_settings({"max_context_tokens": 16000})
        app_module.reset_settings()
        assert not (app_module.APP_DIR / "settings.json").exists()

    @pytest.mark.parametrize(
        "block",
        [{"theme": "pink"}, {"theme": 1}, {"theme": None}, {"theme": ["dark"]}, "dark", [], 7],
    )
    def test_a_bad_value_uses_the_default_and_spares_the_rest(self, app_module, block):
        write_raw(app_module, {"ui": block, "max_context_tokens": 16000})
        assert app_module.ui_settings() == {"theme": "system", "save_history": "on"}
        assert app_module.user_settings()["max_context_tokens"] == 16000
        assert "settings.json" in app_module.STATE.settings_complaint

    def test_an_unknown_ui_key_is_not_used(self, app_module):
        write_raw(app_module, {"ui": {"theme": "dark", "colour": "red"}})
        assert app_module.ui_settings() == {"theme": "dark", "save_history": "on"}

    def test_a_per_model_entry_in_the_ui_block_survives_every_writer(self, app_module):
        # Room for a later per-model alias: no save or reset may drop it.
        entries = {"org/model": {"alias": "Coder"}}
        write_raw(app_module, {"ui": {"theme": "dark", "models": entries}})
        for write in (
            lambda: app_module.save_settings({"max_context_tokens": 24000}, "org/model"),
            lambda: app_module.save_settings({"max_context_tokens": 16000}),
            lambda: app_module.save_load_test({"rounds": 1}),
            lambda: app_module.save_ui({"theme": "light"}),
            lambda: app_module.reset_settings("org/model"),
            app_module.reset_settings,
            lambda: app_module.save_load_test(None),
        ):
            write()
            assert stored(app_module)["ui"]["models"] == entries
        assert app_module.ui_settings() == {"theme": "light", "save_history": "on"}

    def test_a_broken_file_gives_the_default(self, app_module):
        app_module.APP_DIR.mkdir(parents=True, exist_ok=True)
        (app_module.APP_DIR / "settings.json").write_text("{oops", encoding="utf-8")
        assert app_module.ui_settings() == {"theme": "system", "save_history": "on"}

    def test_the_snapshot_carries_it(self, app_module):
        app_module.save_ui({"theme": "dark"})
        assert app_module.STATE.snapshot()["ui"] == {"theme": "dark", "save_history": "on"}


# ─────────────────────────────── the route ───────────────────────────────

ROUTE = "/api/ui/settings"


def post(base: str, body, headers=None, raw: bytes | None = None):
    request = urllib.request.Request(
        base + ROUTE,
        data=json.dumps(body).encode() if raw is None else raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    for name, value in (headers or {}).items():
        request.add_header(name, value)  # after the fixture's token: this one wins
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.fixture
def main_thread(monkeypatch):
    """Records what is handed to the AppKit main thread."""
    calls = []
    package, helper = types.ModuleType("PyObjCTools"), types.ModuleType("PyObjCTools.AppHelper")
    helper.callAfter = lambda work, *args: calls.append((work, args))
    package.AppHelper = helper
    monkeypatch.setitem(sys.modules, "PyObjCTools", package)
    monkeypatch.setitem(sys.modules, "PyObjCTools.AppHelper", helper)
    return calls


class TestRoute:
    def test_it_saves_the_choice(self, app_module, base_url, main_thread):
        assert post(base_url, {"theme": "light"}) == (200, {"ok": True})
        assert app_module.ui_settings() == {"theme": "light", "save_history": "on"}

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"theme": "pink"},
            {"theme": None},
            {"theme": True},
            {"theme": "dark", "other": 1},
            {"save_history": False},
        ],
    )
    def test_a_bad_change_is_refused_and_nothing_is_written(self, app_module, base_url, body):
        status, answer = post(base_url, body)
        assert status == 400 and answer["error"]
        assert not (app_module.APP_DIR / "settings.json").exists()

    def test_a_body_that_is_not_an_object_is_refused(self, base_url):
        assert post(base_url, None, raw=b'["theme"]')[0] == 400

    @pytest.mark.parametrize(
        "headers",
        [
            {"X-MLX-Token": "wrong"},
            {"Origin": "http://evil.example"},
            {"Host": "evil.example"},
        ],
    )
    def test_only_this_app_may_change_it(self, app_module, base_url, headers):
        assert post(base_url, {"theme": "light"}, headers)[0] == 403
        assert app_module.ui_settings() == {"theme": "system", "save_history": "on"}

    def test_a_window_is_repainted_on_the_main_thread(self, app_module, base_url, main_thread):
        app_module.STATE.window = object()
        post(base_url, {"theme": "dark"})
        assert main_thread == [(app_module.apply_native_theme, ("dark",))]

    def test_browser_mode_touches_no_appkit(self, app_module, base_url, main_thread):
        app_module.STATE.window = None
        assert post(base_url, {"theme": "dark"})[0] == 200
        assert main_thread == []

    def test_a_main_thread_failure_does_not_lose_the_save(self, app_module, base_url, monkeypatch):
        monkeypatch.setitem(sys.modules, "PyObjCTools", None)  # the import fails
        app_module.STATE.window = object()
        assert post(base_url, {"theme": "dark"}) == (200, {"ok": True})
        assert app_module.ui_settings() == {"theme": "dark", "save_history": "on"}


# ────────────────────────────── the native app ──────────────────────────────


@pytest.fixture
def appkit(monkeypatch):
    """A stand-in AppKit that records what the app asks for."""
    seen = types.SimpleNamespace(appearance=[], system="NSAppearanceNameAqua", colours=[])

    class Appearance:
        def __init__(self, name):
            self.name = name

        def bestMatchFromAppearancesWithNames_(self, names):
            return self.name

    application = types.SimpleNamespace(
        setAppearance_=lambda value: seen.appearance.append(value and value.name),
        effectiveAppearance=lambda: Appearance(
            seen.appearance[-1] if seen.appearance and seen.appearance[-1] else seen.system
        ),
    )
    module = types.ModuleType("AppKit")
    module.NSAppearanceNameAqua = "NSAppearanceNameAqua"
    module.NSAppearanceNameDarkAqua = "NSAppearanceNameDarkAqua"
    module.NSAppearance = types.SimpleNamespace(appearanceNamed_=Appearance)
    module.NSApplication = types.SimpleNamespace(sharedApplication=lambda: application)
    module.NSColor = types.SimpleNamespace(
        colorWithName_dynamicProvider_=lambda name, provider: ("dynamic", provider),
        colorWithSRGBRed_green_blue_alpha_=lambda *parts: parts,
    )
    monkeypatch.setitem(sys.modules, "AppKit", module)
    monkeypatch.setattr(sys, "platform", "darwin")
    seen.Appearance = Appearance
    return seen


class TestNative:
    @pytest.mark.parametrize(
        ("theme", "name"),
        [("light", "NSAppearanceNameAqua"), ("dark", "NSAppearanceNameDarkAqua"), ("system", None)],
    )
    def test_the_app_appearance_follows_the_choice(self, app_module, appkit, theme, name):
        app_module.apply_native_theme(theme)
        assert appkit.appearance == [name]

    def test_a_failure_is_logged_not_raised(self, app_module, monkeypatch):
        monkeypatch.setitem(sys.modules, "AppKit", None)
        monkeypatch.setattr(sys, "platform", "darwin")
        app_module.apply_native_theme("dark")
        assert "appearance" in app_module.APP_LOG.read_text()

    @pytest.mark.parametrize(
        ("theme", "system", "expected"),
        [
            ("light", "NSAppearanceNameDarkAqua", "light"),
            ("dark", "NSAppearanceNameAqua", "dark"),
            ("system", "NSAppearanceNameAqua", "light"),
            ("system", "NSAppearanceNameDarkAqua", "dark"),
        ],
    )
    def test_the_launch_colour_matches_the_theme(self, app_module, appkit, theme, system, expected):
        appkit.system = system
        app_module.apply_native_theme(theme)
        assert app_module.window_background(theme) == app_module.WINDOW_BACKGROUNDS[expected]

    def test_without_appkit_the_launch_colour_is_dark(self, app_module, monkeypatch):
        monkeypatch.setitem(sys.modules, "AppKit", None)
        dark = app_module.WINDOW_BACKGROUNDS["dark"]
        assert app_module.window_background("system") == dark

    def test_the_window_background_follows_the_appearance(self, app_module, appkit):
        painted, handlers = [], []

        class Hook:
            def __iadd__(self, handler):
                handlers.append(handler)
                return self

        native = types.SimpleNamespace(setBackgroundColor_=painted.append)
        window = types.SimpleNamespace(
            native=native, events=types.SimpleNamespace(before_show=Hook())
        )
        app_module.follow_window_background(window)
        [handler] = handlers
        handler()
        [(kind, provider)] = painted
        assert kind == "dynamic"
        for name, theme in (
            ("NSAppearanceNameAqua", "light"),
            ("NSAppearanceNameDarkAqua", "dark"),
        ):
            value = app_module.WINDOW_BACKGROUNDS[theme]
            parts = tuple(int(value[at : at + 2], 16) / 255 for at in (1, 3, 5))
            assert provider(appkit.Appearance(name)) == (*parts, 1.0)

    def test_a_window_without_hooks_is_left_alone(self, app_module):
        app_module.follow_window_background(object())  # no exception, no browser mode

    def test_main_themes_the_app_before_the_window_opens(self, app_module, appkit, monkeypatch):
        order = []
        app_module.save_ui({"theme": "light"})
        webview = types.ModuleType("webview")
        webview.create_window = lambda *a, **k: order.append(
            ("window", list(appkit.appearance), k["background_color"])
        )
        webview.start = lambda: None
        monkeypatch.setitem(sys.modules, "webview", webview)
        monkeypatch.setattr(app_module, "instance_is_running", lambda: False)
        monkeypatch.setattr(
            app_module, "serve", lambda: types.SimpleNamespace(shutdown=lambda: None)
        )
        for name in (
            "worker_check_versions",
            "restore_server_state",
            "stop_server",
            "adopt_bundle_identity",
        ):
            monkeypatch.setattr(app_module, name, lambda: None)
        app_module.main()
        assert order == [
            ("window", ["NSAppearanceNameAqua"], app_module.WINDOW_BACKGROUNDS["light"])
        ]
        assert app_module.STATE.browser_mode is False


# ──────────────────────────────── the colours ────────────────────────────────

PALETTES = {
    "dark": ":root",
    "light media": ':root:not([data-theme="dark"])',
    "light forced": ':root[data-theme="light"]',
}
LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|rgba|hsl|hsla)\(|\b(?:white|black)\b(?!-)")


def blocks():
    """``(selector, body)`` of every rule, including those inside @media."""
    bare = re.sub(r"/\*.*?\*/", "", STYLE, flags=re.S)
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", bare):
        yield selector.strip(), body


def variables(selector: str) -> dict[str, str]:
    found = {}
    for name, body in blocks():
        if name == selector:
            found.update(re.findall(r"(--[a-z0-9-]+|color-scheme)\s*:\s*([^;]+);", body))
    return found


def is_colour(value: str) -> bool:
    return bool(LITERAL.search(value))


def palette(theme: str) -> dict[str, str]:
    dark = variables(PALETTES["dark"])
    return dark if theme == "dark" else {**dark, **variables(PALETTES["light forced"])}


class TestColours:
    def test_no_colour_is_written_outside_the_palettes(self):
        loose = [
            (selector, declaration.strip())
            for selector, body in blocks()
            if selector not in PALETTES.values()
            for declaration in body.split(";")
            if ":" in declaration and is_colour(declaration.split(":", 1)[1])
        ]
        assert loose == []

    def test_the_page_and_the_script_write_no_colour(self):
        for text in (PAGE, SCRIPT):
            assert re.findall(r"#[0-9a-fA-F]{6}\b|\brgba?\(|\bhsla?\(", text) == []

    def test_light_redefines_every_colour_of_dark(self):
        dark = {name for name, value in variables(":root").items() if is_colour(value)}
        light = set(variables(PALETTES["light forced"])) - {"color-scheme"}
        assert light == dark

    def test_both_light_blocks_are_the_same(self):
        assert variables(PALETTES["light media"]) == variables(PALETTES["light forced"])
        assert re.search(
            r"@media \(prefers-color-scheme: light\) \{\s*" + re.escape(PALETTES["light media"]),
            STYLE,
        )

    def test_native_controls_are_told_the_scheme(self):
        assert variables(":root")["color-scheme"] == "dark"
        assert variables(PALETTES["light forced"])["color-scheme"] == "light"

    def test_every_used_variable_exists(self):
        used = set(re.findall(r"var\((--[a-z0-9-]+)", STYLE))
        assert used <= set(variables(":root"))

    @pytest.mark.parametrize("theme", ["dark", "light"])
    def test_the_window_colour_is_the_page_colour(self, app_module, theme):
        assert app_module.WINDOW_BACKGROUNDS[theme] == palette(theme)["--bg"]


SURFACES = ("--bg", "--bg-2", "--panel", "--panel-2")
# text, the surfaces under it, and a see-through tint between the two (or None)
PAIRS = [
    *[(text, surface, None) for text in ("--text", "--text-2", "--text-3") for surface in SURFACES],
    *[
        (f"--{hue}", surface, tint)
        for hue in ("accent", "green", "amber", "red")
        for surface in ("--bg", "--panel")
        for tint in (None, f"--{hue}-soft")
    ],
    ("--amber", "--bg-2", None),
    ("--red-text", "--bg", None),
    ("--red-text", "--panel", None),
    ("--red-text", "--panel-2", None),
    ("--code-text", "--bg", None),
    ("--text-2", "--line", None),
    ("--on-accent", "--button", None),
    ("--on-accent", "--button-hover", None),
    ("--on-amber", "--amber", None),
]


def rgba(value: str):
    value = value.strip()
    if value.startswith("#"):
        digits = value[1:]
        if len(digits) == 3:
            digits = "".join(c * 2 for c in digits)
        return (*[int(digits[at : at + 2], 16) for at in (0, 2, 4)], 1.0)
    parts = re.findall(r"[\d.]+", value)
    return (*[float(p) for p in parts[:3]], float(parts[3]) if len(parts) > 3 else 1.0)


def over(top, bottom):
    alpha = top[3]
    return (*[top[i] * alpha + bottom[i] * (1 - alpha) for i in range(3)], 1.0)


def luminance(colour) -> float:
    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(c) for c in colour[:3])
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize(("text", "surface", "tint"), PAIRS)
def test_text_has_a_contrast_of_at_least_4_5(theme, text, surface, tint):
    colours = palette(theme)
    ground = rgba(colours[surface])
    if tint:
        ground = over(rgba(colours[tint]), ground)
    high, low = sorted((luminance(rgba(colours[text])), luminance(ground)), reverse=True)
    assert (high + 0.05) / (low + 0.05) >= 4.5


# ───────────────────────────────── the screen ─────────────────────────────────


def test_setup_offers_the_three_choices():
    setup = PAGE[PAGE.index('id="view-settings"') :]
    assert "Appearance" in setup
    choices = re.findall(r'<input type="radio" name="theme" id="theme-(\w+)" value="(\w+)"', setup)
    assert choices == [("system", "system"), ("light", "light"), ("dark", "dark")]
    assert 'role="radiogroup"' in setup


SHOWN = """
const shown = () => [document.documentElement.dataset.theme ?? null,
  ...["system", "light", "dark"].filter((t) => document.querySelector(`#theme-${t}`).checked)];
const choose = async (theme) => {
  const box = document.querySelector(`#theme-${theme}`);
  box.checked = true;
  await box.listeners.change({ target: box });
};
const until = () => new Promise((done) => setTimeout(done, 20));
"""


@needs_node
class TestScreen:
    def test_the_saved_theme_is_applied_to_the_page(self, tmp_path):
        out = run(
            tmp_path,
            SHOWN
            + """
            const seen = [];
            for (const theme of ["light", "dark", "system"]) {
              render(snap({ ui: { theme } }));
              seen.push(shown());
            }
            return seen;
            """,
        )
        assert out == [["light", "light"], ["dark", "dark"], [None, "system"]]

    def test_a_choice_applies_at_once_and_is_saved(self, tmp_path):
        out = run(
            tmp_path,
            SHOWN
            + """
            render(snap({ ui: { theme: "system" } }));
            let done;
            respond = (path) => path === "/api/ui/settings"
              ? new Promise((ok) => { done = ok; }) : new Promise(() => {});
            const saving = choose("light");
            const atOnce = shown();
            render(snap({ ui: { theme: "system" } }));  // a poll from before the save
            const duringSave = shown();
            done({ ok: true, json: async () => ({ ok: true }) });
            await saving; await until();
            const sent = calls.filter((c) => c.path === "/api/ui/settings")
              .map((c) => [c.opts.method, c.opts.body]);
            return [atOnce, duringSave, shown(), sent];
            """,
        )
        assert out == [
            ["light", "light"],
            ["light", "light"],
            ["light", "light"],
            [["POST", '{"theme":"light"}']],
        ]

    def test_a_failed_save_goes_back_and_says_why(self, tmp_path):
        out = run(
            tmp_path,
            SHOWN
            + """
            render(snap({ ui: { theme: "dark" } }));
            respond = (path) => path === "/api/ui/settings"
              ? reply({ error: "disk full" }, false) : new Promise(() => {});
            await choose("light"); await until();
            return [shown(), document.querySelector("#toast").textContent];
            """,
        )
        assert out[0] == ["dark", "dark"]
        assert "disk full" in out[1]
