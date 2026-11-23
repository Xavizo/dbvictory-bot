"""Synthetic reconnect regression; no DLL, sockets, or game commands."""
import ast
import asyncio
import logging
from pathlib import Path
import struct
from types import SimpleNamespace as NS

import pytest
import client_player_state as cp
from client_profiles import UPDATED
from game_state import GameState, scan_packet, _parse
from passive_map import MapReadError
from protocol import PacketReader, ServerOpcode, build_say_packet
from speed_up import SpeedScheduler, run_speed


@pytest.fixture
def local(monkeypatch):
    base, tile, vector, ptr = 0x400000, 0x10000, 0x11000, 0x20000
    center = (414, 106, 7)
    blocks = bytearray(8192)
    struct.pack_into('<I', blocks, ((106 % 32)*32+414 % 32)*8, tile)
    tile_data = bytearray(84)
    struct.pack_into('<I', tile_data, 0, base+UPDATED.tile_vtable)
    struct.pack_into('<III', tile_data, 56, *center)
    struct.pack_into('<III', tile_data, 44, vector, vector+8, vector+8)
    obj = bytearray(0x600)
    struct.pack_into('<I', obj, 0, base+0x8BA9B0)
    struct.pack_into('<I', obj, 0x38, 0x100003B9)
    struct.pack_into('<10d', obj, 0x540, 100, 100, 0, 0, 0, 486, 0, 100, 100, 81)
    memory = {tile: tile_data, vector: bytearray(struct.pack('<II', ptr, 0)), ptr: obj,
              base+UPDATED.map_center: bytearray(struct.pack('<III', *center)),
              base+0xB9DA0: bytearray.fromhex('66 8b 81 d4 00 00 00 c3'),
              base+0xBA1C0: bytearray.fromhex('8b 81 30 05 00 00 c3')}
    def read(addr, length):
        for start, data in memory.items():
            if start <= addr and addr+length <= start+len(data):
                return bytes(data[addr-start:addr-start+length])
        raise MapReadError('synthetic address')
    monkeypatch.setattr(cp.MapReader, '_blocks', lambda *a: {(106//32)*2048+414//32: blocks})
    return NS(base=base, profile=UPDATED, read=read), obj, tile_data, memory


def test_new_login_discovers_actual_local_player_without_old_packet_id(local):
    source, obj, _, _ = local
    old = cp.ClientPlayerReader(source, 0x100002F6)
    with pytest.raises(MapReadError): old.snapshot()
    reader = cp.ClientPlayerReader(source, 0)
    sample = reader.snapshot()
    assert reader.player_id == sample['player_id'] == 0x100003B9
    assert sample['hp'] == 100 and sample['conditions'] == 0
    # Pinned identity must not silently switch while automation is running.
    struct.pack_into('<I', obj, 0x38, 0x100003BA)
    with pytest.raises(MapReadError): reader.snapshot()
    assert reader.player_id == 0x100003B9


@pytest.mark.parametrize('kind', ['other_vtable', 'bad_id', 'two_local_players', 'moving_center', 'torn_identity'])
def test_discovery_rejects_unverified_or_torn_identity(local, kind):
    source, obj, tile, memory = local
    if kind == 'other_vtable': struct.pack_into('<I', obj, 0, source.base+0x12345)
    if kind == 'bad_id': struct.pack_into('<I', obj, 0x38, 0x40000001)
    if kind == 'two_local_players':
        memory[0x21000] = bytearray(obj)
        memory[0x11000].extend(struct.pack('<II', 0x21000, 0))
        struct.pack_into('<III', tile, 44, 0x11000, 0x11010, 0x11010)
    original = source.read
    calls = [0]
    def unstable(addr, length):
        if kind == 'moving_center' and addr == source.base+UPDATED.map_center:
            calls[0] += 1
            if calls[0] > 1: return struct.pack('<III', 415, 106, 7)
        if kind == 'torn_identity' and addr == 0x20000 and length == 0x3C:
            calls[0] += 1
            if calls[0] > 2: struct.pack_into('<I', obj, 0x38, 0x100003BA)
        return original(addr, length)
    source.read = unstable
    reader = cp.ClientPlayerReader(source, 0)
    with pytest.raises(MapReadError): reader.snapshot()
    assert reader.player_id == 0


def test_relogin_binding_enables_speed_only_from_new_fresh_client_sample(local):
    async def exercise():
        source, *_ = local
        gs = GameState(); gs.player_id = 0x100002F6
        state = NS(game_state=gs, automation_paused=False,
                   speed_scheduler=SpeedScheduler(), settings={'actions': {'speed_up': {'enabled': True}}})
        sent = []
        class Bot:
            is_connected = True
            @property
            def player_id(self): return gs.player_id
            async def sleep(self, seconds): pass
            def log(self, message): pass
        bot = Bot(); bot.state = state
        async def inject(data, guard):
            assert guard()
            sent.append(data)
            bot.is_connected = False
        state.game_proxy = NS(logged_in=True, inject_to_server=inject)
        assert await cp.bind_local_player(bot, state, gs, cp.ClientPlayerReader(source, 0))
        assert gs.player_id == gs.client_player_id == 0x100003B9
        await run_speed(bot)
        assert sent == [build_say_packet('speed up')]
    asyncio.run(exercise())


def test_binding_does_not_publish_into_replaced_session(local):
    async def exercise():
        source, *_ = local
        gs = GameState(); state = NS(game_state=GameState())
        assert not await cp.bind_local_player(NS(is_connected=True), state, gs, cp.ClientPlayerReader(source,0))
        assert gs.client_snapshot is None and gs.player_id == 0
    asyncio.run(exercise())


def test_packet_identity_cannot_override_verified_local_player():
    gs=GameState(); gs.player_id=gs.client_player_id=0x100003B9
    scan_packet(bytes([ServerOpcode.LOGIN_OR_PENDING])+struct.pack('<I',0x100002F6)+b'\x00'*8,gs)
    assert gs.player_id==0x100003B9
    _parse(ServerOpcode.LOGIN_OR_PENDING,PacketReader(struct.pack('<I',0x100002F6)),gs)
    assert gs.player_id==0x100003B9


def callback(name, env):
    tree=ast.parse((Path(__file__).resolve().parents[1]/'mcp_server.py').read_text(encoding='utf-8'))
    node=next(n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
    exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual session callback>','exec'),env)
    return env[name]


def test_login_callback_replaces_old_state_and_old_delayed_start_cannot_run():
    async def exercise():
        from session_startup import LoginStartup
        from session_observations import session_observations
        starts=[]
        old=GameState();old.player_id=0x100002F6;old.haste_cast_at=100
        old.client_snapshot={'hp':100,'conditions':2}
        settings={'actions':{'speed_up':{'enabled':True}}}
        proxy = NS(logged_in=True, server_writer=object(), session_generation=1)
        state = NS(game_state=old, ready=False, connected=True, game_proxy=proxy,
                   settings=settings, login_startup=LoginStartup())
        fn=callback('on_login_success',dict(state=state,GameState=GameState,observer=True,log=logging.getLogger('test'),
            observation_proxy=proxy, session_observations=session_observations,
            _start_all_enabled_actions=lambda:starts.append(state.game_state.session_id) or 1))
        fn(None);first=state.game_state
        assert first is not old and first.player_id==0 and first.client_snapshot is None
        assert first.client_state_required and not hasattr(first,'haste_cast_at')
        fn(None)
        assert state.game_state is first  # repeated signal for the same login
        proxy.session_generation += 1
        fn(None)
        await asyncio.sleep(0)
        assert starts == [2] and state.login_startup.phase == 'activated'
        assert state.settings is settings and settings['actions']['speed_up']['enabled']
        assert not first.movement_history.status()["active"]
        assert state.game_state.movement_history.status()["active"]
        state.game_state.movement_history.close(wait=True)
    asyncio.run(exercise())


def test_disconnect_cancels_passive_reader_as_well_as_user_actions():
    stopped=[];invalidated=[]
    state=NS(game_state=GameState(),_action_tasks={'dll_bridge':object(),'speed_up':object()},
             _auto_task=None,automation_paused=True,game_proxy=NS(pause_injections=lambda:None),
             potion_diagnostics=NS(invalidate_inventory=lambda:invalidated.append(True)))
    from session_startup import LoginStartup
    state.login_startup = LoginStartup()
    callback('on_disconnect',dict(state=state,observation_proxy=state.game_proxy,_stop_action=stopped.append))()
    assert stopped==['dll_bridge','speed_up']
    assert not state.ready and invalidated==[True]


def test_user_action_waits_for_verified_identity_before_capturing_player_id():
    async def exercise():
        gs=GameState();gs.client_state_required=True
        calls=[]
        async def run(_): calls.append(gs.player_id)
        state=NS(game_state=gs,connected=True,_action_completed=set(),settings={},automation_paused=False)
        fn=callback('_wrapper',dict(state=state,name='speed_up',INTERNAL_ACTIONS={'dll_bridge'},
            ctx=object(),mod=NS(run=run),asyncio=asyncio,log=logging.getLogger('test')))
        task=asyncio.create_task(fn());await asyncio.sleep(.06)
        gs.player_id=0x100002F6  # A packet ID alone is insufficient on observer profile.
        await asyncio.sleep(.06);assert calls==[]
        gs.player_id=gs.client_player_id=0x100003B9
        await task;assert calls==[0x100003B9]
    asyncio.run(exercise())


def test_speed_status_explains_missing_post_login_sample():
    scheduler=SpeedScheduler()
    assert not scheduler.inspect(None,{},now=100)
    assert 'Brak świeżych danych postaci' in scheduler.status
    assert scheduler.attempts==0
