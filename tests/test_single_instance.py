"""Single-instance behaviour: the bug that produced a dead second launch.

app.py owns this: a second launch asks the running instance to raise its window.
The native launcher is covered by test_m13_l1_launcher.py. Nothing here may
touch the real desktop.
"""

from __future__ import annotations


class TestAppSideGuards:
    def test_instance_is_running_true_against_live_server(self, app_module, server, monkeypatch):
        host, port = server.server_address[:2]
        monkeypatch.setattr(app_module, "HOST", host)
        monkeypatch.setattr(app_module, "PORT", port)
        assert app_module.instance_is_running() is True

    def test_instance_is_running_false_when_port_is_dead(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module, "PORT", 1)
        assert app_module.instance_is_running() is False

    def test_request_focus_true_against_live_server(self, app_module, server, monkeypatch):
        host, port = server.server_address[:2]
        monkeypatch.setattr(app_module, "HOST", host)
        monkeypatch.setattr(app_module, "PORT", port)

        class StubWindow:
            def restore(self):
                pass

            def show(self):
                pass

        app_module.STATE.window = StubWindow()
        assert app_module.request_focus_from_running_instance() is True

    def test_request_focus_false_without_a_window(self, app_module, server, monkeypatch):
        host, port = server.server_address[:2]
        monkeypatch.setattr(app_module, "HOST", host)
        monkeypatch.setattr(app_module, "PORT", port)
        app_module.STATE.window = None
        assert app_module.request_focus_from_running_instance() is False

    def test_request_focus_false_when_port_is_dead(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module, "PORT", 1)
        assert app_module.request_focus_from_running_instance() is False

    def test_focus_window_false_without_a_window(self, app_module):
        app_module.STATE.window = None
        assert app_module.focus_window() is False

    def test_focus_window_survives_a_broken_window(self, app_module):
        class Broken:
            def restore(self):
                raise RuntimeError("no window")

            def show(self):
                raise RuntimeError("no window")

        app_module.STATE.window = Broken()
        assert app_module.focus_window() is False
