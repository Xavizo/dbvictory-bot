import asyncio
import json
import threading

import pytest

from dbv.client_bridge import session_bridge
from dbv.client_bridge.session_bridge import BridgeUnavailable, ObserverBridge


@pytest.fixture
def loop():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)
    loop.close()


def test_no_owner_is_reported(tmp_path):
    with pytest.raises(BridgeUnavailable):
        session_bridge.call("status", info_path=tmp_path / "observer.json")


def test_round_trip_and_allowlist(tmp_path, loop):
    seen = []

    async def journal(action="status", include_chat=False):
        seen.append((action, include_chat, threading.current_thread() is threading.main_thread()))
        return {"action": action, "include_chat": include_chat}

    async def status():
        return "ok"

    info = tmp_path / "observer.json"
    bridge = ObserverBridge(loop, {"status": status, "journal": journal}, info_path=info)
    assert bridge.start()
    try:
        assert session_bridge.call("status", info_path=info) == "ok"
        assert session_bridge.call("journal", info_path=info, action="start", include_chat=True) == {
            "action": "start", "include_chat": True}
        with pytest.raises(RuntimeError, match="unknown operation"):
            session_bridge.call("walk", info_path=info)
        with pytest.raises(RuntimeError, match="bad arguments"):
            session_bridge.call("status", info_path=info, direction="n")
        # A second owner does not replace a live one.
        assert not ObserverBridge(loop, {"status": status}, info_path=info).start()
    finally:
        bridge.close()
    assert not info.exists()
    assert seen[0][:2] == ("start", True)


def test_wrong_key_is_refused(tmp_path, loop):
    async def status():
        return "ok"

    info = tmp_path / "observer.json"
    bridge = ObserverBridge(loop, {"status": status}, info_path=info)
    bridge.start()
    try:
        data = json.loads(info.read_text())
        data["key"] = "00" * 32
        forged = tmp_path / "forged.json"
        forged.write_text(json.dumps(data))
        with pytest.raises(BridgeUnavailable):
            session_bridge.call("status", info_path=forged, timeout=3)
        assert session_bridge.call("status", info_path=info) == "ok"
    finally:
        bridge.close()


def test_stale_file_is_unavailable(tmp_path, loop):
    async def status():
        return "ok"

    info = tmp_path / "observer.json"
    bridge = ObserverBridge(loop, {"status": status}, info_path=info)
    bridge.start()
    stale = info.read_text()
    bridge.close()
    info.write_text(stale)
    with pytest.raises(BridgeUnavailable):
        session_bridge.call("status", info_path=info, timeout=3)
