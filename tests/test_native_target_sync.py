"""Native Game selection stays withdrawn; visual-only marker has a separate capability."""
import asyncio
import ctypes
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from dbv.client_bridge.protocol import build_attack_packet
from test_automation_safety import proxy


def test_server_attack_never_calls_withdrawn_native_callback():
    async def scenario():
        p = proxy()
        p.client_target_sync = Mock(side_effect=AssertionError('native hook invoked'))
        task = asyncio.create_task(p._process_inject_queue())
        try:
            await p.inject_to_server(build_attack_packet(0x40000001), guard=lambda: False)
            await asyncio.sleep(.01)
            p.server_writer.write.assert_not_called()
            attack = build_attack_packet(0x40000002)
            await p.inject_to_server(attack, guard=lambda: True)
            await asyncio.sleep(.01)
            p.server_writer.write.assert_called_once_with(attack)
            p.server_writer.drain.assert_awaited_once()
            p.client_target_sync.assert_not_called()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    asyncio.run(scenario())


def test_observer_injection_loads_a_fresh_copy_of_the_visual_only_binary(monkeypatch, tmp_path):
    from actions import dll_bridge as action
    from dbv.client_bridge.client_profiles import UPDATED
    dlls = tmp_path / 'dll'
    dlls.mkdir()
    (dlls / 'dbvobserver_marker_20261117.dll').write_bytes(b'visual-only observer fixture')
    (dlls / 'dbvobserver_target_20261117.dll').write_bytes(b'withdrawn fixture')
    monkeypatch.setattr(action, 'PROJECT_ROOT', str(tmp_path))
    inject = Mock()
    monkeypatch.setattr(action.injector, 'inject', inject)
    copy = action._load_fresh_copy(UPDATED)
    inject.assert_called_once_with(copy)
    assert Path(copy).name.startswith('dbvobserver_live_')
    assert Path(copy).read_bytes() == b'visual-only observer fixture'


def test_visual_marker_follows_only_dispatched_attacks_and_pause_clears_intent():
    async def run():
        p=proxy();p.client_target_marker=Mock()
        task=asyncio.create_task(p._process_inject_queue())
        try:
            await p.inject_to_server(build_attack_packet(0x40000001),guard=lambda:False)
            await asyncio.sleep(.01);p.client_target_marker.assert_not_called()
            await p.inject_to_server(build_attack_packet(0x40000002),guard=lambda:True)
            await asyncio.sleep(.01);p.client_target_marker.assert_called_once_with(0x40000002)
            p.pause_injections();assert p.client_target_marker.call_args.args==(0,)
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
    asyncio.run(run())


def test_manual_target_clears_visual_intent_without_altering_game_packet():
    p=proxy();p.client_target_marker=Mock();p._client_packet_callbacks=[Mock()]
    p._decrypt_game_packet=lambda _:build_attack_packet(0x40000003)
    assert p._process_client_game_packet(b'original')==b'original'
    p.client_target_marker.assert_called_once_with(0)


def test_visual_lease_is_bounded_throttled_and_does_not_write_from_proxy_callback(monkeypatch):
    import time
    from dbv.client_bridge import observer_pipe
    from dbv.client_bridge.client_profiles import UPDATED
    from test_dll_pipe import FakePipeApi
    api = FakePipeApi()
    monkeypatch.setattr(observer_pipe, '_api', api)
    b = observer_pipe.ObserverPipe(UPDATED)
    b.open(); b.marker_ready = True; b.marker_seen = time.monotonic(); b.client_target = 0
    sent = api.written
    for bad in [True, -1, 0x10000001, 0x80000000, 'bad']:
        assert not b.request_marker(bad)
    assert b.request_marker(0x40000001) and not sent
    assert b.renew_marker(active=True) and sent == [b'M 1073741825 0 1\n']
    assert not b.renew_marker(active=True)
    assert b.renew_marker(active=False) and sent[-1] == b'M 0 0 2\n' and b.marker_desired == 0
    b.marker_seen -= 1; b.request_marker(0x40000001)
    assert not b.renew_marker(active=True)


def test_snapshot_reports_marker_state(monkeypatch):
    from dbv.client_bridge import observer_pipe
    from dbv.client_bridge.client_profiles import UPDATED
    from test_dll_pipe import FakePipeApi
    snapshot = {'creatures': [{'id': 0x40000001, 'hp': 100, 'name': 'fixture'}], 'px': 669, 'py': 407,
                'pz': 8, 'target_marker': True, 'client_target': 0, 'marked_target': 0x40000001}
    api = FakePipeApi((json.dumps(snapshot) + '\n').encode())
    monkeypatch.setattr(observer_pipe, '_api', api)
    b = observer_pipe.ObserverPipe(UPDATED)
    b.open()
    assert b.poll() == snapshot['creatures']
    assert (b.position, b.marker_ready, b.client_target, b.marked_target) == ((669, 407, 8), True, 0, 0x40000001)
    assert not api.written


def test_marker_lease_stops_with_combat_even_when_healing_stays_on():
    import time
    from types import SimpleNamespace as NS
    from actions.dll_bridge import _marker_active
    p=NS(logged_in=True)
    gs=NS(client_snapshot={'hp':100,'monotonic':time.monotonic()})
    st=NS(game_proxy=p,automation_paused=False,_action_tasks={'auto_potions':NS(done=lambda:False)})
    assert not _marker_active(st,gs,p)
    st._action_tasks['adaptive_cavebot']=NS(done=lambda:False)
    assert _marker_active(st,gs,p)
    st._action_tasks['adaptive_cavebot']=NS(done=lambda:True)
    assert not _marker_active(st,gs,p)
