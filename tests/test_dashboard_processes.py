"""Windows launch/cleanup behavior with mocked OS processes, no game access."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

from dbv.mcp_server import server
import pytest
from dbv.mcp_server.dashboard_security import SESSION_TOKEN


def test_only_matching_build_is_activated_on_next_start(tmp_path, monkeypatch):
    current, stage = tmp_path / ".next", tmp_path / ".next-ready-123abcd"
    for folder in [current, stage]:
        folder.mkdir()
        (folder / "BUILD_ID").write_text(folder.name)
        (folder / "routes-manifest.json").write_text("{}")
    monkeypatch.setattr(server, "ENGINE_REVISION", "0" * 40)
    server._activate_staged_dashboard(tmp_path)
    assert (current / "BUILD_ID").read_text() == ".next"
    monkeypatch.setattr(server, "ENGINE_REVISION", "123abcd" + "0" * 33)
    original = type(stage).rename
    def simulate_locked(path, destination):
        if path == stage:
            raise PermissionError("locked")
        return original(path, destination)
    with monkeypatch.context() as patch:
        patch.setattr(type(stage), "rename", simulate_locked)
        with pytest.raises(PermissionError):
            server._activate_staged_dashboard(tmp_path)
    assert (current / "BUILD_ID").read_text() == ".next"
    server._activate_staged_dashboard(tmp_path)
    assert (current / "BUILD_ID").read_text() == stage.name and not stage.exists()


def test_engine_label_includes_revision_and_timezone_and_survives_missing_git(monkeypatch, tmp_path):
    revision = "abc2345" + "0" * 33
    monkeypatch.setattr(server, "version_for", lambda root, sha: "v1.1.0" if sha else None)
    label, timestamp = server._engine_label(tmp_path, revision).split(" · start ")
    assert label == "Silnik v1.1.0 (abc2345)" and datetime.fromisoformat(timestamp).utcoffset() is not None
    assert "wersja nieznana · start " in server._engine_label(tmp_path, None)


def test_cleanup_targets_only_live_owned_process_trees(monkeypatch):
    live = Mock(pid=1234)
    live.poll.return_value = None
    exited = Mock(pid=5678)
    exited.poll.return_value = 0
    run = Mock()
    monkeypatch.setattr(server, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(server.subprocess, "run", run)
    monkeypatch.setattr(server, "_dashboard_processes", {"next": live, "window": exited})
    server._stop_dashboard()
    run.assert_called_once_with(
        ["taskkill", "/F", "/T", "/PID", "1234"], capture_output=True, timeout=10
    )
    live.wait.assert_called_once_with(timeout=5)
    exited.wait.assert_not_called()
    assert not server._dashboard_processes


def test_launch_refuses_unowned_port_and_passes_token_only_to_the_window(monkeypatch, tmp_path):
    build_dir = tmp_path / "dashboard" / ".next"
    build_dir.mkdir(parents=True)
    (build_dir / "BUILD_ID").write_text("test-build")
    window = ["pythonw.exe", "-B", "-m", "dbv.mcp_server.panel_window"]
    monkeypatch.setattr(server, "_panel_window_command", lambda: window)
    from dbv.mcp_server import panel_server
    served = []
    fake_server = Mock()
    monkeypatch.setattr(panel_server, "start", lambda directory, port: served.append((directory, port)) or fake_server)
    monkeypatch.setattr(server.paths, "DASHBOARD_DIR", tmp_path / "dashboard")
    monkeypatch.setattr(server, "os", SimpleNamespace(name="nt", environ={
        server.EXIT_FILE_ENV: "stale", "ELECTRON_RUN_AS_NODE": "1", "DBV_DASHBOARD_TOKEN": "old"}))
    monkeypatch.setattr(server.state, "tray_exit_file", tmp_path / "quit", raising=False)
    monkeypatch.setattr(server.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(server, "_dashboard_processes", {})
    monkeypatch.setattr(server, "_panel_server", None)
    monkeypatch.setattr(server, "_engine_label", Mock(side_effect=AssertionError("must reuse startup identity")))
    popen = Mock(side_effect=[Mock()])
    monkeypatch.setattr(server.subprocess, "Popen", popen)
    monkeypatch.setattr(server, "_check_port_listening", lambda _: True)
    server._launch_dashboard()
    popen.assert_not_called()
    assert not served
    monkeypatch.setattr(server, "_check_port_listening", lambda _: False)
    server._launch_dashboard()
    # The panel is served from this process; only its window is started.
    assert served == [(tmp_path / "dashboard", server.DASHBOARD_PORT)]
    assert popen.call_count == 1
    window_call = popen.call_args
    assert window_call.args[0] == window
    assert window_call.kwargs["cwd"] == str(server.paths.INSTALL_ROOT)
    env = window_call.kwargs["env"]
    assert env[server.EXIT_FILE_ENV] == str(tmp_path / "quit")
    assert env["DBV_DASHBOARD_TOKEN"] == SESSION_TOKEN
    assert env["DBV_ENGINE_LABEL"] == server.ENGINE_LABEL
    assert "ELECTRON_RUN_AS_NODE" not in env
    assert window_call.kwargs['creationflags'] == 0x08000000
    # Prevent atexit from touching mocked processes after monkeypatch teardown.
    server._dashboard_processes.clear()
    server._stop_panel_server()
    fake_server.shutdown.assert_called_once_with()


def test_the_panel_window_runs_on_the_bundled_windowed_python(monkeypatch, tmp_path):
    python = tmp_path / "python.exe"
    (tmp_path / "pythonw.exe").write_text("")
    monkeypatch.setattr(server.sys, "executable", str(python))
    monkeypatch.setattr(server.importlib.util, "find_spec", lambda name: object())
    assert server._panel_window_command() == [str(tmp_path / "pythonw.exe"), "-B", "-m", "dbv.mcp_server.panel_window"]
    (tmp_path / "DBVictory.exe").write_text("")  # the release's pythonw under the bot's name and icon
    assert server._panel_window_command()[0] == str(tmp_path / "DBVictory.exe")
    monkeypatch.setattr(server.importlib.util, "find_spec", lambda name: None)
    assert server._panel_window_command() is None
