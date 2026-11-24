"""Logging: rotation, permissions, migration and the JSON-lines reader."""

from __future__ import annotations

import json
import os
import stat


class TestAppendAndRead:
    def test_activity_round_trip(self, app_module):
        app_module.log_activity("chat", "org/model", "success", "3 tokens")
        entries = app_module.read_activity()
        assert len(entries) == 1
        assert entries[0]["action"] == "chat"
        assert entries[0]["model"] == "org/model"
        assert entries[0]["status"] == "success"
        assert entries[0]["detail"] == "3 tokens"
        assert entries[0]["timestamp"]

    def test_app_log_round_trip(self, app_module):
        app_module.log_app("error", "boom", repo="org/model", exit_code=-9)
        entries = app_module.read_app_log()
        assert entries[0]["level"] == "error"
        assert entries[0]["message"] == "boom"
        assert entries[0]["repo"] == "org/model"
        assert entries[0]["exit_code"] == -9

    def test_appends_do_not_overwrite(self, app_module):
        for index in range(3):
            app_module.log_activity("chat", "m", "success", f"turn {index}")
        assert [e["detail"] for e in app_module.read_activity()] == [
            "turn 0",
            "turn 1",
            "turn 2",
        ]

    def test_limit_returns_the_tail(self, app_module):
        for index in range(10):
            app_module.log_activity("chat", "m", "success", str(index))
        entries = app_module.read_activity(limit=3)
        assert [e["detail"] for e in entries] == ["7", "8", "9"]

    def test_missing_file_reads_empty(self, app_module):
        assert app_module.read_activity() == []
        assert app_module.read_app_log() == []

    def test_malformed_line_is_wrapped_as_raw(self, app_module):
        app_module.ACTIVITY_LOG.write_text('{"ok": 1}\nnot json\n', encoding="utf-8")
        entries = app_module.read_activity()
        assert len(entries) == 2
        # Valid JSON that is not an activity entry passes through untouched.
        assert entries[0] == {"ok": 1}
        # Unparseable text is wrapped so the UI can still render it.
        assert entries[1]["action"] == "raw"
        assert entries[1]["detail"] == "not json"
        assert entries[1]["status"] == "info"

    def test_unicode_is_preserved(self, app_module):
        app_module.log_activity("chat", "m", "success", "héllo — 世界")
        assert app_module.read_activity()[0]["detail"] == "héllo — 世界"


class TestPermissions:
    def test_log_dir_is_0700(self, app_module):
        mode = stat.S_IMODE(os.stat(app_module.LOG_DIR).st_mode)
        assert mode == 0o700

    def test_app_dir_is_0700(self, app_module):
        mode = stat.S_IMODE(os.stat(app_module.APP_DIR).st_mode)
        assert mode == 0o700

    def test_log_files_are_0600(self, app_module):
        app_module.log_activity("chat", "m", "success", "x")
        app_module.log_app("info", "y")
        for path in (app_module.ACTIVITY_LOG, app_module.APP_LOG):
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


class TestRotation:
    def test_rotates_past_the_cap(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module, "LOG_MAX_BYTES", 200)
        monkeypatch.setattr(app_module, "LOG_BACKUPS", 3)
        for index in range(12):
            app_module.log_app("info", f"line {index} " + "x" * 40)

        names = {p.name for p in app_module.LOG_DIR.iterdir()}
        assert "app.log" in names
        assert "app.log.1" in names
        # Never more than LOG_BACKUPS numbered files.
        numbered = [n for n in names if n.startswith("app.log.")]
        assert len(numbered) <= 3

    def test_current_file_stays_small(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module, "LOG_MAX_BYTES", 200)
        for index in range(12):
            app_module.log_app("info", f"line {index} " + "x" * 40)
        assert app_module.APP_LOG.stat().st_size < 400

    def test_rotation_keeps_newest_lines(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module, "LOG_MAX_BYTES", 200)
        for index in range(12):
            app_module.log_app("info", f"line {index}")
        entries = app_module.read_app_log(limit=100)
        assert entries, "current log should not be empty"
        assert entries[-1]["message"].startswith("line 11")


class TestLogFiles:
    def test_lists_files_with_metadata(self, app_module):
        app_module.log_activity("chat", "m", "success", "x")
        app_module.log_app("info", "y")
        files = app_module.log_files()
        names = {f["name"] for f in files}
        assert {"activity.log", "app.log"} <= names
        for entry in files:
            assert entry["size"] > 0
            assert entry["size_h"]
            assert entry["modified"]
            assert entry["path"].endswith(entry["name"])

    def test_empty_dir_returns_empty_list(self, app_module):
        for path in app_module.LOG_DIR.iterdir():
            path.unlink()
        assert app_module.log_files() == []


class TestMigration:
    def test_moves_legacy_logs_into_the_folder(self, app_module):
        legacy_activity = app_module.APP_DIR / "activity.log"
        legacy_launch = app_module.APP_DIR / "launch.log"
        legacy_activity.write_text('{"action": "chat", "detail": "old"}\n', encoding="utf-8")
        legacy_launch.write_text("old launch\n", encoding="utf-8")

        app_module._migrate_legacy_logs()

        assert not legacy_activity.exists()
        assert not legacy_launch.exists()
        assert (app_module.LOG_DIR / "activity.log").exists()
        assert (app_module.LOG_DIR / "launch.log").exists()

    def test_keeps_existing_target_and_drops_the_duplicate(self, app_module):
        app_module.log_activity("chat", "m", "success", "new")
        legacy = app_module.APP_DIR / "activity.log"
        legacy.write_text('{"action": "chat", "detail": "old"}\n', encoding="utf-8")

        app_module._migrate_legacy_logs()

        assert not legacy.exists()
        details = [e["detail"] for e in app_module.read_activity()]
        assert details == ["new"]

    def test_no_legacy_files_is_a_noop(self, app_module):
        app_module._migrate_legacy_logs()
        assert app_module.read_activity() == []


class TestDiagnosticBundle:
    def test_zip_contains_environment_and_logs(self, app_module):
        import io
        import zipfile

        app_module.log_activity("chat", "m", "success", "x")
        app_module.log_app("info", "y")
        payload, name = app_module.build_diagnostic_bundle()

        assert name.startswith("mlx-studio-diagnostics-")
        assert name.endswith(".zip")
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = set(archive.namelist())
            assert "environment.json" in names
            assert "logs/activity.log" in names
            assert "logs/app.log" in names
            env = json.loads(archive.read("environment.json"))
        assert env["app"]["name"] == "MLX Studio"
        assert env["app"]["version"] == app_module.APP_VERSION

    def test_environment_never_contains_secrets(self, app_module, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "hf_supersecret")
        report = app_module.environment_report()
        assert report["env"]["HF_TOKEN"] == "set"
        assert "hf_supersecret" not in json.dumps(report)

    def test_environment_reports_paths_and_ports(self, app_module):
        report = app_module.environment_report()
        assert report["paths"]["log_dir"] == str(app_module.LOG_DIR)
        assert report["ports"]["app"] == app_module.PORT
        assert report["ports"]["server"] == app_module.SERVER_PORT
        assert report["machine"]
        assert report["python"]
