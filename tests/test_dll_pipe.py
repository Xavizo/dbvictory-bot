"""Pipe health checks must not steal the bridge connection or truncate HANDLEs."""
import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from dbv.mcp_server import server


def test_status_uses_existing_connection_without_probing(monkeypatch):
    monkeypatch.setattr(server.state, "game_state", SimpleNamespace(dll_bridge=SimpleNamespace(connected=True)))
    assert server._check_pipe_exists()


def test_disconnected_bridge_is_not_reported_as_healthy(monkeypatch):
    monkeypatch.setattr(server.state, "game_state", SimpleNamespace(dll_bridge=SimpleNamespace(connected=False)))
    assert not server._check_pipe_exists()


@pytest.fixture
def pipe_module():
    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("Native Windows ctypes API")
    from dbv.client_bridge import dll_bridge
    return dll_bridge


def test_createfile_declares_pointer_sized_handle(pipe_module):
    from ctypes import wintypes
    assert pipe_module.kernel32.CreateFileA.restype is wintypes.HANDLE


def test_invalid_handle_never_connects(pipe_module, monkeypatch):
    api = Mock()
    api.CreateFileA.return_value = pipe_module.INVALID_HANDLE_VALUE
    monkeypatch.setattr(pipe_module, "kernel32", api)
    bridge = pipe_module.DllBridge()
    assert not bridge.connect()
    assert not bridge.connected
    api.SetNamedPipeHandleState.assert_not_called()
    api.WriteFile.assert_not_called()


def test_failed_pipe_mode_closes_handle(pipe_module, monkeypatch):
    api = Mock()
    api.CreateFileA.return_value = 123
    api.SetNamedPipeHandleState.return_value = False
    monkeypatch.setattr(pipe_module, "kernel32", api)
    bridge = pipe_module.DllBridge()
    assert not bridge.connect()
    assert not bridge.connected
    api.CloseHandle.assert_called_once_with(123)


@pytest.mark.parametrize("error,exists", [(121, True), (231, True), (2, False), (5, False)])
def test_busy_pipe_exists_but_absent_pipe_does_not(pipe_module, monkeypatch, error, exists):
    api = Mock()
    api.WaitNamedPipeA.return_value = False
    monkeypatch.setattr(pipe_module, "kernel32", api)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error)
    assert pipe_module.DllBridge().pipe_exists() is exists
    api.CreateFileA.assert_not_called()


@pytest.mark.parametrize("cmd", ["hook_xtea", "hook_peekmsg", "game_attack", "set_player_pos", "init"])
def test_observer_rejects_every_command_even_when_connected(pipe_module, monkeypatch, cmd):
    api = Mock()
    monkeypatch.setattr(pipe_module, "kernel32", api)
    bridge = pipe_module.DllBridge(read_only=True)
    bridge._handle = 123
    assert not bridge.send_command({"cmd": cmd})
    api.WriteFile.assert_not_called()
    assert bridge.pipe_name != pipe_module.PIPE_NAME


def test_observer_initialization_never_sends_hooks_or_offsets():
    from actions.dll_bridge import _send_init_commands
    bridge = SimpleNamespace(read_only=True, send_command=Mock())
    from unittest.mock import patch
    with patch("actions.dll_bridge._dbg"):
        _send_init_commands(bridge, 0x10000130)
    bridge.send_command.assert_not_called()
