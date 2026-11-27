import struct
from types import SimpleNamespace
import pytest
from dbv.client_bridge import client_player_state as cp
from dbv.client_bridge.client_profiles import UPDATED
from dbv.brain.navigation.passive_map import MapReadError
from dbv.brain.state.game_state import GameState
from dbv.brain.combat.potions import PotionScheduler, validate_config


@pytest.fixture
def reader():
    base, ptr, pid=0x400000,0x20000,0x10000061
    obj=bytearray(0x600)
    struct.pack_into('<I',obj,0,base+0x8BA9B0)
    struct.pack_into('<I',obj,0x38,pid)
    struct.pack_into('<10d',obj,0x540,36,100,522495,522495,101407684,184,55,3,100,66)
    def read(addr,size):
        if addr==base+UPDATED.map_center:return struct.pack('<III',669,158,6)
        if ptr<=addr and addr+size<=ptr+len(obj):return bytes(obj[addr-ptr:addr-ptr+size])
        raise MapReadError('synthetic address')
    source=SimpleNamespace(base=base,read=read,profile=UPDATED)
    r=cp.ClientPlayerReader(source,pid);r.ptr=ptr
    return r,obj


def test_client_stats_match_exact_local_values_and_win_over_delayed_packets(reader,monkeypatch):
    r,_=reader
    monkeypatch.setattr(cp.time,'monotonic',lambda:10)
    snapshot=r.snapshot()
    gs=GameState();gs.client_state_required=True;gs.client_snapshot=snapshot
    gs.hp=66;gs.max_hp=100;gs.mana=84;gs.max_mana=100;gs.stats_updated_at=1
    gs.position=(41057,100,0)
    assert (gs.hp,gs.max_hp,gs.mana,gs.position)==(36,100,3,(669,158,6))
    cfg=validate_config({'shared_cooldown':.3,'rules':[{'id':'heal','enabled':True,'resource':'hp',
        'item_id':8465,'threshold':50,'jitter':0,'cooldown':.2,'interval':.2,'mode':'use'}]})
    assert PotionScheduler().next_rule(cfg,gs,10,snapshot['stats_updated_at'])['id']=='heal'
    monkeypatch.setattr(cp.time,'monotonic',lambda:10.51)
    assert gs.stats_updated_at==0 and gs.max_hp==0
    assert PotionScheduler().next_rule(cfg,gs,10.51,snapshot['stats_updated_at']) is None


@pytest.mark.parametrize('offset,value',[(0x540,float('nan')),(0x548,0),(0x540,101),(0x578,-1)])
def test_invalid_or_torn_stats_cannot_refresh_healing(reader,offset,value):
    r,obj=reader;struct.pack_into('<d',obj,offset,value)
    with pytest.raises(MapReadError):r.snapshot()
    assert r.ptr is None


def test_reused_creature_address_is_rejected(reader):
    r,obj=reader;struct.pack_into('<I',obj,0x38,0x10000062)
    with pytest.raises(MapReadError):r.snapshot()


def test_absolute_resource_display_accepts_values_above_50000(reader):
    r,obj=reader
    struct.pack_into('<2d',obj,0x540,188098,522495)
    struct.pack_into('<2d',obj,0x578,522495,522495)
    snapshot=r.snapshot()
    assert (snapshot['hp'],snapshot['max_hp'],snapshot['mana'],snapshot['max_mana'])==(188098,522495,522495,522495)


@pytest.mark.parametrize('offset,value',[(0x548,2**31),(0x580,2**31),(0x540,123.5)])
def test_unbounded_or_fractional_resource_values_remain_invalid(reader,offset,value):
    r,obj=reader;struct.pack_into('<d',obj,offset,value)
    with pytest.raises(MapReadError):r.snapshot()


def test_unverified_legacy_client_does_not_use_updated_local_offsets():
    from dbv.client_bridge.client_profiles import LEGACY
    with pytest.raises(MapReadError):cp.ClientPlayerReader(SimpleNamespace(profile=LEGACY),1)

def test_verified_conditions_and_speed_are_optional_and_never_replace_health(reader):
    r,obj=reader;r.conditions_supported=True
    struct.pack_into('<I',obj,0x530,54);struct.pack_into('<H',obj,0xD4,1984)
    s=r.snapshot();assert s['conditions']==54 and s['movement_speed']==1984 and s['hp']==36
    original=r.source.read
    def fail_conditions(address,size):
        if address==r.ptr+0x530:raise MapReadError('synthetic condition failure')
        return original(address,size)
    r.source.read=fail_conditions
    s=r.snapshot();assert 'conditions' not in s and s['hp']==36


def test_bound_vitals_continue_through_optional_worker_wait_and_reader_error():
    import asyncio
    from unittest.mock import Mock

    async def exercise():
        reads = 0
        delayed = asyncio.Event()
        gs = GameState()
        bot = SimpleNamespace(is_connected=True)
        state = SimpleNamespace(game_state=gs)
        values = [dict(hp=100,position=(1,1,7)), RuntimeError(), dict(hp=40,position=(1,1,7))]
        reader = Mock(snapshot=Mock(side_effect=values))
        async def tick(_):
            nonlocal reads
            reads += 1
            if reads == 2:
                assert gs.client_state_error == 'RuntimeError' and gs.client_snapshot['hp'] == 100
            if reads == 3:
                state.game_state = GameState()
            await asyncio.sleep(0)
        bot.sleep = tick
        auxiliary = asyncio.create_task(delayed.wait())
        try:
            await cp.poll_bound_vitals(bot,state,gs,reader,.016)
            assert reads == 3 and not auxiliary.done()
            assert gs.client_snapshot['hp'] == 40 and gs.client_state_error is None
            assert state.game_state.client_snapshot is None
        finally:
            auxiliary.cancel()
            await asyncio.gather(auxiliary,return_exceptions=True)
    asyncio.run(exercise())

def test_creature_diagnostic_failure_does_not_stop_bound_vitals(monkeypatch, tmp_path):
    import asyncio
    import sys
    from unittest.mock import AsyncMock, Mock
    import actions.dll_bridge as action

    gs = GameState()
    state = SimpleNamespace(game_state=gs,game_proxy=SimpleNamespace())
    bot = SimpleNamespace(is_connected=True,player_id=1)
    samples = [dict(hp=90,position=(1,1,7)), dict(hp=40,position=(1,1,7))]
    reader = Mock(snapshot=Mock(side_effect=samples))
    bridge = Mock(read_only=True,read_creatures=Mock(side_effect=RuntimeError('auxiliary stream lost')))
    source = Mock()
    monkeypatch.setattr(sys.modules['__main__'],'state',state,raising=False)
    monkeypatch.setattr(action,'DEBUG_LOG',str(tmp_path/'diagnostic.log'))
    monkeypatch.setattr(action,'_make_bridge',lambda:bridge)
    monkeypatch.setattr(action,'_connect_with_inject',AsyncMock(return_value=True))
    monkeypatch.setattr('dbv.client_bridge.passive_map_win32.ReadOnlyClient',lambda:source)
    monkeypatch.setattr(cp,'ClientPlayerReader',lambda *_:reader)
    monkeypatch.setattr(cp,'bind_local_player',AsyncMock(return_value=True))
    async def tick(_):
        if reader.snapshot.call_count == 2:
            bot.is_connected = False
        await asyncio.sleep(0)
    bot.sleep = tick
    asyncio.run(asyncio.wait_for(action.run(bot),1))
    assert reader.snapshot.call_count == 2 and gs.client_snapshot['hp'] == 40
    assert state.game_proxy.client_target_marker is None
    source.close.assert_called_once()
    bridge.disconnect.assert_called_once()
