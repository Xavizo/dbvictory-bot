"""Windows launch/cleanup behavior with mocked OS processes, no game access."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import mcp_server as server
from dashboard_security import SESSION_TOKEN


def test_engine_label_includes_revision_and_timezone_and_survives_missing_git(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    git = Mock(return_value="abc2345\n")
    monkeypatch.setattr(server.subprocess, "check_output", git)
    label, timestamp = server._engine_label(tmp_path).split(" · start ")
    assert label == "Silnik abc2345" and datetime.fromisoformat(timestamp).utcoffset() is not None
    assert git.call_args.kwargs["cwd"] == tmp_path
    git.side_effect = FileNotFoundError()
    assert "wersja nieznana · start " in server._engine_label(tmp_path)


def test_cleanup_targets_only_live_owned_process_trees(monkeypatch):
    live = Mock(pid=1234)
    live.poll.return_value = None
    exited = Mock(pid=5678)
    exited.poll.return_value = 0
    run = Mock()
    monkeypatch.setattr(server, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(server.subprocess, "run", run)
    monkeypatch.setattr(server, "_dashboard_processes", {"next": live, "electron": exited})
    server._stop_dashboard()
    run.assert_called_once_with(
        ["taskkill", "/F", "/T", "/PID", "1234"], capture_output=True, timeout=10
    )
    live.wait.assert_called_once_with(timeout=5)
    exited.wait.assert_not_called()
    assert not server._dashboard_processes


def test_launch_refuses_unowned_port_and_passes_token_only_to_electron(monkeypatch, tmp_path):
    build_dir = tmp_path / "dashboard" / ".next"
    build_dir.mkdir(parents=True)
    (build_dir / "BUILD_ID").write_text("test-build")
    monkeypatch.setattr(server, "__file__", str(tmp_path / "mcp_server.py"))
    monkeypatch.setattr(server, "os", SimpleNamespace(name="nt", environ={}))
    monkeypatch.setattr(server.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr("shutil.which", lambda _: r"C:\Program Files\nodejs\npm.cmd")
    monkeypatch.setattr(server, "_dashboard_processes", {})
    monkeypatch.setattr(server, "_engine_label", Mock(side_effect=AssertionError("must reuse startup identity")))
    popen = Mock(side_effect=[Mock(), Mock()])
    monkeypatch.setattr(server.subprocess, "Popen", popen)
    monkeypatch.setattr(server, "_check_port_listening", lambda _: True)
    server._launch_dashboard()
    popen.assert_not_called()
    monkeypatch.setattr(server, "_check_port_listening", lambda _: False)
    server._launch_dashboard()
    assert popen.call_count == 2
    next_call, electron_call = popen.call_args_list
    assert next_call.args[0].endswith('run start')
    assert "DBV_DASHBOARD_TOKEN" not in next_call.kwargs["env"]
    assert electron_call.kwargs["env"]["DBV_DASHBOARD_TOKEN"] == SESSION_TOKEN
    assert electron_call.kwargs["env"]["DBV_ENGINE_LABEL"] == server.ENGINE_LABEL
    assert next_call.kwargs['creationflags'] == 0x08000000
    assert electron_call.kwargs['creationflags'] == 0x08000000
    # Prevent atexit from touching mocked processes after monkeypatch teardown.
    server._dashboard_processes.clear()
