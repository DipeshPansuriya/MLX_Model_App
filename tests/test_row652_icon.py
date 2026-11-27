"""Row 652: the bundle shows its own name and icon, not Python's.

AppKit and Foundation are replaced, so no test touches the real Dock or opens a
window.
"""

from __future__ import annotations

import sys
import types

import pytest


@pytest.fixture
def bundle(app_module, monkeypatch, tmp_path):
    """app.py as installed: the icon sits in Contents/Resources."""
    icon = tmp_path / "MLX Studio.app" / "Contents" / "Resources" / "AppIcon.icns"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"icns")
    monkeypatch.setattr(app_module, "APP_ICON", icon)
    monkeypatch.setattr(app_module.sys, "platform", "darwin")
    return icon


@pytest.fixture
def cocoa(monkeypatch):
    """Stand-ins for the two PyObjC modules, recording what the app asks of them."""
    seen = types.SimpleNamespace(
        calls=[], info={"CFBundleName": "Python"}, localized=None, readable=True, named={}
    )

    class Image:
        def __init__(self, source=None):
            self.source = source

        @classmethod
        def alloc(cls):
            return cls()

        @classmethod
        def imageNamed_(cls, name):
            return seen.named.get(name)

        def initWithContentsOfFile_(self, path):
            self.source = path
            return self if seen.readable else None

        def setName_(self, name):
            # AppKit: a name belongs to one image, and only a free name can be taken.
            if name is not None and name in seen.named:
                return False
            for taken in [key for key, image in seen.named.items() if image is self]:
                del seen.named[taken]
            if name is not None:
                seen.named[name] = self
            return True

    seen.named["NSApplicationIcon"] = Image("the Python icon")

    shared = types.SimpleNamespace(
        setActivationPolicy_=lambda policy: seen.calls.append(("policy", policy)),
        setApplicationIconImage_=lambda image: seen.calls.append(("icon", image.source)),
    )

    def shared_application():
        seen.calls.append(("app", seen.info["CFBundleName"]))
        return shared

    appkit = types.ModuleType("AppKit")
    appkit.NSImage = Image
    appkit.NSApplication = types.SimpleNamespace(sharedApplication=shared_application)
    foundation = types.ModuleType("Foundation")
    main_bundle = types.SimpleNamespace(
        infoDictionary=lambda: seen.info, localizedInfoDictionary=lambda: seen.localized
    )
    foundation.NSBundle = types.SimpleNamespace(mainBundle=lambda: main_bundle)
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "Foundation", foundation)
    return seen


def test_the_icon_is_looked_for_beside_app_py(app_module):
    assert app_module.APP_ICON.name == "AppIcon.icns"
    assert app_module.APP_ICON.parent == app_module.UI_DIR.parent


def test_the_bundle_sets_its_name_before_the_application_and_then_its_icon(
    app_module, bundle, cocoa
):
    app_module.adopt_bundle_identity()
    assert cocoa.calls == [("app", "MLX Studio"), ("policy", 0), ("icon", str(bundle))]


def test_alerts_such_as_confirm_look_the_icon_up_by_name_and_find_ours(app_module, bundle, cocoa):
    app_module.adopt_bundle_identity()
    assert cocoa.named["NSApplicationIcon"].source == str(bundle)


def test_a_localized_name_is_replaced_too(app_module, bundle, cocoa):
    cocoa.localized = {"CFBundleName": "Python"}
    app_module.adopt_bundle_identity()
    assert cocoa.localized["CFBundleName"] == cocoa.info["CFBundleName"] == "MLX Studio"


@pytest.mark.parametrize("case", ["source checkout", "no icon file", "not macOS"])
def test_outside_the_macos_bundle_nothing_happens(
    app_module, bundle, cocoa, monkeypatch, tmp_path, case
):
    if case == "source checkout":
        loose = tmp_path / "AppIcon.icns"
        loose.write_bytes(b"icns")
        monkeypatch.setattr(app_module, "APP_ICON", loose)
    elif case == "no icon file":
        bundle.unlink()
    else:
        monkeypatch.setattr(app_module.sys, "platform", "linux")
    app_module.adopt_bundle_identity()
    assert cocoa.calls == [] and cocoa.info == {"CFBundleName": "Python"}
    assert not app_module.APP_LOG.exists()


def test_an_unreadable_icon_is_not_set_and_is_logged(app_module, bundle, cocoa):
    cocoa.readable = False
    app_module.adopt_bundle_identity()
    assert [name for name, _ in cocoa.calls if name == "icon"] == []
    assert "could not set the app icon" in app_module.APP_LOG.read_text()


def test_a_name_that_cannot_be_changed_still_gets_the_icon(app_module, bundle, cocoa):
    cocoa.localized = types.MappingProxyType({"CFBundleName": "Python"})  # read only
    app_module.adopt_bundle_identity()
    assert ("icon", str(bundle)) in cocoa.calls
    assert "could not set the app name" in app_module.APP_LOG.read_text()


def test_without_pyobjc_it_only_logs(app_module, bundle, monkeypatch):
    monkeypatch.setitem(sys.modules, "AppKit", None)
    monkeypatch.setitem(sys.modules, "Foundation", None)
    app_module.adopt_bundle_identity()
    assert "could not set the app icon" in app_module.APP_LOG.read_text()


def test_main_sets_the_identity_before_the_window_starts(app_module, monkeypatch):
    order = []
    webview = types.ModuleType("webview")
    webview.create_window = lambda *a, **k: order.append("window") or object()
    webview.start = lambda *a, **k: order.append("start")
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setattr(app_module, "instance_is_running", lambda: False)
    monkeypatch.setattr(app_module, "serve", lambda: types.SimpleNamespace(shutdown=lambda: None))
    for name in ("worker_check_versions", "restore_server_state", "stop_server"):
        monkeypatch.setattr(app_module, name, lambda: None)
    monkeypatch.setattr(app_module, "adopt_bundle_identity", lambda: order.append("identity"))
    app_module.main()
    assert order == ["identity", "window", "start"]
