import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest
import mcp_server as server
from proxy import OTProxy


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "load_settings", lambda: {"actions": {}})
    st = server.BotState()
    monkeypatch.setattr(server, "state", st)
    monkeypatch.setattr(server, "SETTINGS_FILE", tmp_path / "settings.json")
    return st


def proxy():
    p = OTProxy.__new__(OTProxy)
    p._inject_queue = asyncio.Queue()
    p.injections_paused = False
    p.logged_in = True
    p.xtea_keys = (1, 2, 3, 4)
    p.server_writer = Mock(drain=AsyncMock())
    p._encrypt_game_packet = lambda packet: packet
    p._wrap_packet = lambda packet: packet
    return p


def test_emergency_cancels_actions_clears_queue_and_persists_latch(state):
    async def exercise():
        state.game_proxy = proxy(); state.ready = True
        state.settings["actions"] = {"auto_combat": {"enabled": True}, "eat_food": {"enabled": True}}
        action = asyncio.create_task(asyncio.sleep(100))
        internal = asyncio.create_task(asyncio.sleep(100))
        state._action_tasks = {"auto_combat": action, "dll_bridge": internal}
        state._auto_task = asyncio.create_task(asyncio.sleep(100))
        await state.game_proxy.inject_to_server(b"queued item use")
        await server._async_emergency_stop()
        assert action.cancelled() and not internal.done()
        assert state.automation_paused and state.game_proxy.injections_paused
        assert state.game_proxy._inject_queue.empty()
        assert all(not c["enabled"] for n, c in state.settings["actions"].items() if n != "dll_bridge")
        assert json.loads(server.SETTINGS_FILE.read_text())["automation_paused"]
        assert [call.args[0][0] for call in state.game_proxy.server_writer.write.call_args_list] == [0x69, 0xA1, 0xA2]
        assert "Emergency" in await server._async_toggle_action("eat_food", True)
        await state.game_proxy.inject_to_server(b"blocked")
        assert state.game_proxy._inject_queue.empty()
        await server._async_unlock_automation()
        assert not state.automation_paused
        assert not state._action_tasks.get("auto_combat")
        assert all(not c["enabled"] for c in state.settings["actions"].values())
        internal.cancel()
        await asyncio.gather(internal, return_exceptions=True)
    asyncio.run(exercise())


def test_stop_still_latches_when_settings_cannot_be_saved(state, monkeypatch):
    def fail(_): raise OSError("disk unavailable")
    monkeypatch.setattr(server, "save_settings", fail)
    result = asyncio.run(server._async_emergency_stop())
    assert state.automation_paused and "could not be saved" in result


def test_unconfigured_potions_cannot_be_enabled(state):
    result = asyncio.run(server._async_toggle_action("auto_potions", True))
    assert "cooldown" in result
    assert not state.settings["actions"].get("auto_potions", {}).get("enabled")


def test_config_endpoint_cannot_bypass_toggle_or_pause(state):
    with pytest.raises(ValueError):
        asyncio.run(server._async_update_action_config("auto_potions", {"enabled": True}))


def test_rule_config_save_and_rejection_preserve_saved_settings(state):
    import copy
    rule = {"id": "emergency", "enabled": False, "resource": "hp", "threshold": 25,
            "jitter": 0, "item_id": 8465, "cooldown": 0, "interval": 1, "mode": "use"}
    asyncio.run(server._async_update_action_config("auto_potions", {"rules": [rule]}))
    assert not state._action_tasks
    saved = copy.deepcopy(state.settings)
    with pytest.raises(ValueError):
        asyncio.run(server._async_update_action_config("auto_potions", {"rules": [{**rule, "item_id": 1}]}))
    assert state.settings == saved


def test_cannot_unlock_during_pending_emergency_stop(state):
    state._emergency_stopping = True
    with pytest.raises(ValueError, match="Wait for emergency"):
        asyncio.run(server._async_unlock_automation())


def test_dashboard_control_endpoints_change_real_state_without_game(state, monkeypatch):
    from http.client import HTTPConnection
    from http.server import HTTPServer
    import sys
    import threading
    import dashboard_api as api
    from dashboard_security import SESSION_TOKEN, DASHBOARD_ORIGIN

    async def exercise():
        monkeypatch.setattr(api, "_state", state)
        monkeypatch.setattr(api, "_main_loop", asyncio.get_running_loop())
        for name in ("_async_emergency_stop", "_async_unlock_automation", "_async_pause_automation", "_async_resume_automation", "_async_update_action_config", "_async_potion_diagnostics"):
            monkeypatch.setattr(sys.modules["__main__"], name, getattr(server, name), raising=False)
        http = HTTPServer(("127.0.0.1", 0), api._Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
        def request(path, body=None):
            conn = HTTPConnection("127.0.0.1", http.server_port, timeout=3)
            try:
                conn.request("POST", path, body=json.dumps(body) if body is not None else None,
                             headers={"Origin": DASHBOARD_ORIGIN, "Authorization": f"Bearer {SESSION_TOKEN}"})
                res = conn.getresponse()
                return res.status, json.loads(res.read())
            finally: conn.close()
        try:
            status, _ = await asyncio.to_thread(request, "/api/automation/pause")
            assert status == 200 and state.automation_paused and state.automation_pause_kind == "manual"
            status, _ = await asyncio.to_thread(request, "/api/automation/resume")
            assert status == 200 and not state.automation_paused and not state._action_tasks
            status, _ = await asyncio.to_thread(request, "/api/emergency-stop")
            assert status == 200 and state.automation_paused
            status, _ = await asyncio.to_thread(request, "/api/automation/resume")
            assert status == 400 and state.automation_paused
            status, _ = await asyncio.to_thread(request, "/api/automation/unlock")
            assert status == 200 and not state.automation_paused
            assert not state._action_tasks and not state.connected
            status, _ = await asyncio.to_thread(request, "/api/actions/auto_potions/config", {"health_cooldown": -1})
            assert status == 400
            status, _ = await asyncio.to_thread(request, "/api/actions/auto_potions/config", {"health_cooldown": 2, "ki_cooldown": 3})
            assert status == 200
            assert state.settings["actions"]["auto_potions"]["health_cooldown"] == 2
            assert not state.settings["actions"]["auto_potions"].get("enabled")
            status, _ = await asyncio.to_thread(request, "/api/potions/diagnostics", {"enabled": True})
            assert status == 200 and state.potion_diagnostics.snapshot()["active"]
        finally:
            await asyncio.to_thread(http.shutdown)
            http.server_close(); thread.join(timeout=2)
    asyncio.run(exercise())
