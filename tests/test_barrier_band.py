import struct
from types import SimpleNamespace
import pytest
from dbv.brain.combat.barrier_band import BandScheduler,validate_config,can_equip
from dbv.client_bridge.client_equipment import EquipmentReader
from dbv.client_bridge.client_profiles import UPDATED,LEGACY
from dbv.brain.navigation.passive_map import MapReadError
from test_automation_safety import state


def sample(t=10,slots=None,sources=None):
    return dict(monotonic=t,player_id=123,slots=slots or {5:0,6:0,9:0},sources=[dict(container=2,index=3,item_id=3051)] if sources is None else sources)


def test_default_band_renewal_requires_two_fresh_empty_samples_then_equipment_ack():
    s=BandScheduler();s.begin();cfg=validate_config({});assert cfg['slot']=='band'
    assert s.inspect(sample(slots={9:3088}),cfg,10) is None
    assert s.status=='Bariera aktywna'
    assert s.inspect(sample(11),cfg,11) is None
    request=s.inspect(sample(11.4),cfg,11.4)
    assert request==dict(container=2,index=3,item_id=3051,slot=9)
    s.sent(11.4)
    assert s.inspect(sample(12),cfg,12) is None and s.pending
    assert s.inspect(sample(12.1,slots={9:3088}),cfg,12.1) is None
    assert not s.pending and s.attempts==0 and s.status=='Bariera aktywna'


@pytest.mark.parametrize('slots',[{9:3052},{9:3051},{9:3088},{9:0,5:3088},{9:0,6:3051}])
def test_never_overwrites_equipment_or_doubles_an_equipped_band(slots):
    s=BandScheduler()
    for t in [10,11,12]:assert s.inspect(sample(t,slots),{},t) is None


def test_stale_unchanged_or_missing_stock_cannot_trigger_and_attempts_are_bounded():
    s=BandScheduler();cfg={'slot':'left_hand'}
    assert s.inspect(sample(10),cfg,11) is None
    assert s.inspect(sample(11,sources=[]),cfg,11) is None
    assert s.inspect(sample(12),cfg,12) is None
    assert s.inspect(sample(12),cfg,12.4) is None  # Same snapshot is not a second observation.
    for t in [13,19,25]:
        s.inspect(sample(t),cfg,t)
        r=s.inspect(sample(t+.4),cfg,t+.4);assert r['slot']==6
        s.sent(t+.4)
    assert s.inspect(sample(32),cfg,32) is None and '3 próbach' in s.status


@pytest.mark.parametrize("slot,observed,expected", [(9, 10.3, "confirmed"), (5, 10.3, "pending"),
                                                   (9, 9.9, "pending"), (9, 14., "pending")])
def test_seen_band_only_confirms_the_requested_slot_and_current_attempt(slot, observed, expected):
    import asyncio
    from dbv.client_bridge.command_delivery import CommandReceipt

    async def run():
        scheduler = BandScheduler()
        scheduler.sent(10., slot=slot)
        receipt = CommandReceipt(1, 1, "server", 0)
        receipt.finish("unknown")
        scheduler.receipt = receipt
        scheduler.inspect(sample(observed, slots={9: 3088}), {}, max(10.1, observed))
        assert scheduler.equipped_id == 3088 and receipt.effect == expected
    asyncio.run(run())


def test_unknown_delivery_does_not_repeat_equip_while_slot_is_unchanged():
    scheduler = BandScheduler()
    scheduler.sent(10., slot=9)
    scheduler.receipt = SimpleNamespace(status="unknown")
    for t in (10.5, 20., 20.5, 100., 101.):
        assert scheduler.inspect(sample(t), {}, t) is None
        assert scheduler.attempts == 1
    # A different physical item followed by an empty slot starts a new decision.
    assert scheduler.inspect(sample(102., slots={9: 3456}), {}, 102.) is None
    assert scheduler.inspect(sample(103.), {}, 103.) is None
    assert scheduler.inspect(sample(103.5), {}, 103.5)["slot"] == 9


def test_final_guard_rechecks_player_source_destination_and_freshness():
    request=dict(container=2,index=3,item_id=3051,slot=9)
    assert can_equip(sample(),request,123,10)
    for data,pid,now in [(sample(),999,10),(sample(),123,11),(sample(slots={9:3052}),123,10),(sample(sources=[]),123,10),(sample(slots={5:3088,9:0}),123,10)]:
        assert not can_equip(data,request,pid,now)
    with pytest.raises(ValueError):validate_config({'slot':'backpack'})
    with pytest.raises(ValueError):validate_config({'item_id':3052})


def fake_equipment():
    memory={};base=0x400000;player=0x10000;head=0x20000;node=0x30000;container=0x40000;blocks=0x50000;block=0x60000;band=0x70000;active=0x80000
    def obj(iid):
        b=bytearray(0x3b);struct.pack_into('<I',b,0,base+UPDATED.item_vtable);struct.pack_into('<H',b,0x35,iid);return bytes(b)
    inv=bytearray(88);struct.pack_into('<I',inv,9*8,active);memory[player+0x46c]=bytes(inv)
    identity=bytearray(0x3c);struct.pack_into('<I',identity,0,base+0x8ba9b0);struct.pack_into('<I',identity,0x38,123);memory[player]=bytes(identity)
    memory[base+0xb73880+0x2c]=struct.pack('<II',head,1)
    h=bytearray(28);struct.pack_into('<III',h,0,node,node,node);h[13]=1;memory[head]=bytes(h)
    memory[node]=struct.pack('<III',head,head,head)+bytes(4)+struct.pack('<III',2,container,0)
    c=bytearray(88);struct.pack_into('<I',c,0x10,2);struct.pack_into('<IIII',c,0x48,blocks,8,0,1);memory[container]=bytes(c)
    memory[blocks]=struct.pack('<I',block);memory[block]=struct.pack('<II',band,0);memory[band]=obj(3051);memory[active]=obj(3088)
    def read(a,n):
        if a not in memory or len(memory[a])!=n:raise MapReadError('fake range')
        return memory[a]
    source=SimpleNamespace(profile=UPDATED,base=base,read=read)
    p=SimpleNamespace(source=source,ptr=player,player_id=123,snapshot=lambda:{'hp':100})
    return EquipmentReader(p),memory,container,band


def test_verified_inventory_layout_reads_active_and_spare_ids_without_exposing_addresses():
    r,mem,c,b=fake_equipment();s=r.snapshot()
    assert s['slots'][9]==3088 and s['sources']==[dict(container=2,index=0,item_id=3051)]
    assert set(s)=={'slots','sources','monotonic','player_id'}
    mem[b]=mem[b][:0x35]+struct.pack('<H',3052)+mem[b][0x37:]
    assert not r.snapshot()['sources']


def test_reader_rejects_wrong_profile_invalid_bounds_and_torn_inventory():
    r,mem,c,b=fake_equipment();r.source.profile=LEGACY
    with pytest.raises(MapReadError):EquipmentReader(r.player)
    r.source.profile=UPDATED;raw=bytearray(mem[c]);struct.pack_into('<I',raw,0x54,999);mem[c]=bytes(raw)
    with pytest.raises(MapReadError):r.snapshot()
    r,mem,c,b=fake_equipment();old=r.source.read;calls=0
    def torn(a,n):
        nonlocal calls
        result=old(a,n)
        if a==b:
            calls+=1
            if calls>1:return bytes(n)
        return result
    r.source.read=torn
    with pytest.raises(MapReadError):r.snapshot()


def test_action_sends_only_move_to_selected_empty_slot_and_revokes_queued_guard(monkeypatch):
    import asyncio,time
    from dbv.brain.combat import barrier_band as band
    from dbv.client_bridge import client_equipment; from dbv.client_bridge import client_player_state; from dbv.client_bridge import passive_map_win32
    from dbv.client_bridge.protocol import build_move_item_packet
    source=SimpleNamespace(close=lambda:None)
    cfg={'enabled':True,'slot':'right_hand'}
    state=SimpleNamespace(settings={'actions':{band.NAME:cfg}},automation_paused=False,band_scheduler=BandScheduler())
    def snapshot():return sample(time.monotonic())
    monkeypatch.setattr(passive_map_win32,'ReadOnlyClient',lambda:source)
    monkeypatch.setattr(client_player_state,'ClientPlayerReader',lambda *a:None)
    monkeypatch.setattr(client_equipment,'EquipmentReader',lambda p:SimpleNamespace(snapshot=snapshot))
    request=dict(container=2,index=3,item_id=3051,slot=5)
    monkeypatch.setattr(state.band_scheduler,'inspect',lambda *args:request)
    guards=[];sent=[]
    async def inject(packet,guard):
        assert guard();sent.append(packet);guards.append(guard)
        state.automation_paused=True;assert not guard();state.automation_paused=False
    async def sleep(_):cfg['enabled']=False
    state.game_proxy=SimpleNamespace(logged_in=True,inject_to_server=inject)
    bot=SimpleNamespace(state=state,player_id=123,is_connected=True,sleep=sleep)
    asyncio.run(band.run_band(bot))
    assert sent==[build_move_item_packet((65535,66,3),3051,3,(65535,5,0),1)]
    assert not guards[0]()


def test_dead_character_does_not_publish_equipment():
    r,_,_,_=fake_equipment();r.player.snapshot=lambda:{'hp':0}
    with pytest.raises(MapReadError):r.snapshot()


def test_configuration_and_restart_validate_band_without_enabling_other_actions(state,monkeypatch):
    import asyncio
    from dbv.mcp_server import server
    calls=[]
    monkeypatch.setattr(server,'_start_action',lambda name:calls.append(name))
    for name in ['eat_food','auto_barrier_band']:
        assert 'restarted' in asyncio.run(server._async_restart_action(name))
    assert calls==['eat_food','auto_barrier_band'] and not state.settings['actions']
    cfg=asyncio.run(server._async_update_action_config('auto_barrier_band',{'slot':'left_hand'}))
    assert cfg=={'slot':'left_hand'}
    with pytest.raises(ValueError):asyncio.run(server._async_update_action_config('auto_barrier_band',{'item_id':3052}))
    assert state.settings['actions']['auto_barrier_band']==cfg
