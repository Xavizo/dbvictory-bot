"""Observer pipe: status checks never steal the connection, and nothing but marker leases is written."""
from types import SimpleNamespace

import pytest

from dbv.client_bridge import observer_pipe
from dbv.client_bridge.client_profiles import LEGACY, UPDATED, NOVEMBER_26
from dbv.client_bridge.observer_pipe import ObserverPipe
from dbv.mcp_server import server


def win_error(code):
    error = OSError(f"Windows error {code}")
    error.winerror = code  # set by Windows itself; plain OSError lacks it elsewhere
    return error


class FakePipeApi:
    """Stands in for _winapi; ``incoming`` is what the DLL has written so far."""

    def __init__(self, incoming=b"", wait_error=None, open_error=None):
        self.incoming = bytearray(incoming)
        self.written = []
        self.closed = []
        self.opened = []
        self.wait_error = wait_error
        self.open_error = open_error

    def CreateFile(self, name, *args):
        if self.open_error:
            raise win_error(self.open_error)
        self.opened.append(name)
        return 77

    def WaitNamedPipe(self, name, timeout):
        if self.wait_error:
            raise win_error(self.wait_error)

    def PeekNamedPipe(self, handle, size=0):
        return len(self.incoming), 0

    def ReadFile(self, handle, size):
        data = bytes(self.incoming[:size])
        del self.incoming[:size]
        return data, 0

    def WriteFile(self, handle, data):
        self.written.append(bytes(data))
        return len(data), 0

    def CloseHandle(self, handle):
        self.closed.append(handle)


@pytest.fixture
def api(monkeypatch):
    fake = FakePipeApi()
    monkeypatch.setattr(observer_pipe, "_api", fake)
    return fake


def test_status_uses_existing_connection_without_probing(monkeypatch):
    monkeypatch.setattr(server.state, "game_state", SimpleNamespace(dll_bridge=SimpleNamespace(connected=True)))
    assert server._check_pipe_exists()


def test_disconnected_bridge_is_not_reported_as_healthy(monkeypatch):
    monkeypatch.setattr(server.state, "game_state", SimpleNamespace(dll_bridge=SimpleNamespace(connected=False)))
    assert not server._check_pipe_exists()


def test_each_build_uses_its_own_pipe():
    assert ObserverPipe(UPDATED).name == r"\\.\pipe\dbvbot_marker_20261117"
    assert ObserverPipe(NOVEMBER_26).name == r"\\.\pipe\dbvbot_marker_20261126"


def test_retired_or_unverified_builds_have_no_pipe():
    with pytest.raises(ValueError):
        ObserverPipe(LEGACY)
    from dataclasses import replace
    with pytest.raises(ValueError):
        ObserverPipe(replace(UPDATED, sha256="0" * 64))


def test_missing_pipe_never_connects(monkeypatch):
    fake = FakePipeApi(open_error=2)
    monkeypatch.setattr(observer_pipe, "_api", fake)
    pipe = ObserverPipe(UPDATED)
    assert not pipe.open() and not pipe.connected


@pytest.mark.parametrize("error,exists", [(121, True), (231, True), (2, False), (5, False)])
def test_busy_pipe_exists_but_absent_pipe_does_not(monkeypatch, error, exists):
    fake = FakePipeApi(wait_error=error)
    monkeypatch.setattr(observer_pipe, "_api", fake)
    assert ObserverPipe(UPDATED).exists() is exists
    assert not fake.opened


def test_snapshot_lines_split_across_reads_are_joined(api):
    pipe = ObserverPipe(UPDATED)
    assert pipe.open()
    api.incoming += b'{"creatures":[{"id":1073741825}],"px":5,'
    assert pipe.poll() is None
    api.incoming += b'"py":6,"pz":7}\n{"creatures":[{"id":2}]'
    assert pipe.poll() == [{"id": 1073741825}]
    assert pipe.position == (5, 6, 7)
    api.incoming += b',"px":0}\n'
    assert pipe.poll() == [{"id": 2}]
    assert pipe.position == (5, 6, 7)  # a zero position is ignored


def test_only_the_newest_complete_snapshot_is_used(api):
    pipe = ObserverPipe(UPDATED)
    pipe.open()
    api.incoming += b'{"creatures":[1]}\nnot json\n{"creatures":[2]}\n{"other":1}\n'
    assert pipe.poll() == [2]


def test_broken_pipe_closes_the_connection(api, monkeypatch):
    pipe = ObserverPipe(UPDATED)
    pipe.open()
    def broken(handle, size=0):
        raise win_error(109)
    monkeypatch.setattr(api, "PeekNamedPipe", broken)
    assert pipe.poll() is None and not pipe.connected and api.closed == [77]


def test_there_is_no_generic_command_channel():
    assert not any(hasattr(ObserverPipe, name) for name in ("send_command", "write", "send"))


def test_a_snapshot_with_non_utf8_name_bytes_is_still_read():
    from dbv.client_bridge import observer_pipe
    from dbv.client_bridge.client_profiles import NOVEMBER_26
    pipe = observer_pipe.ObserverPipe(NOVEMBER_26)
    pipe._handle = object()
    line = b'{"creatures":[{"id":1073741825,"name":"Kr\xb3l","hp":50,"x":1,"y":2,"z":7}]}\n'
    pipe._read_available = lambda: line
    creatures = pipe.poll()
    assert creatures and creatures[0]["id"] == 1073741825 and creatures[0]["hp"] == 50
