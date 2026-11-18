"""Synthetic geometry, fresh-facing acknowledgments and manual-target races."""
import asyncio
import struct
import time
import pytest
import combat_wave as wave
from cavebot_combat import validate_spell,validate_combat
from combat_spell_conditions import evaluate_spell,evaluate_spells
from protocol import ClientOpcode,Direction,PacketReader,build_turn_packet
from test_v2_combat import armed,monster
from test_adaptive_cavebot import cell,setup
from test_automation_safety import state,proxy
from test_client_player_state import reader


@pytest.fixture(autouse=True)
def synthetic_wave_profile(monkeypatch):
    # Exercise the directional engine without misclassifying a real spell.
    monkeypatch.setattr(wave, 'PROFILES', {'test wave': ((0, -1),)})


def look(gs,direction,stamp=None):
    gs.client_snapshot=dict(position=gs.position,direction=direction,
                            monotonic=time.monotonic() if stamp is None else stamp)


def configured(state,tmp_path):
    c,cells=armed(state,tmp_path,spells=[dict(words='Test Wave',range=5,range_op='<=',count=1,ki=10)])
    c.combat_only=True;c.status='running'
    state.game_state.mana=state.game_state.max_mana=100
    look(state.game_state,0)
    return c,cells


@pytest.mark.parametrize('direction,delta',list(enumerate(wave.CARDINALS)))
def test_conservative_core_rotates_and_counts_only_hit_cells(state,tmp_path,direction,delta):
    cells=[cell(x,y) for x in range(8,14) for y in range(8,14)]
    c,cells,_=setup(state,tmp_path,cells=cells)
    cfg=validate_combat(dict(rules=[dict(pattern='*')]))
    c.combat.config=cfg;look(state.game_state,(direction+1)%4)
    state.game_state.mana=state.game_state.max_mana=100
    monster(state,cells,x=10+delta[0],y=10+delta[1]);monster(state,cells,x=11,y=11,cid=0x40000002)
    targets=c.combat.candidates();target=targets[0]
    s=validate_spell(dict(words='TEST WAVE',trigger='visible',range=7,count=1))
    check=evaluate_spell(s,state.game_state,targets,target,sample=state.passive_map_monitor.snapshot())
    assert check['ready'] and check['wave_direction']==direction and check['matched_count']==1
    assert check['turn_required'] and 'Wave:' in check['wave_note']


@pytest.mark.parametrize('x,y,z',[(11,11,7),(12,10,7),(10,12,7),(11,10,6),(10,10,7)])
def test_range_or_visible_setting_never_expands_unknown_server_mask(state,tmp_path,x,y,z):
    c,cells=configured(state,tmp_path)
    target=dict(id=9,position=[x,y,z],distance=max(abs(x-10),abs(y-10)))
    s=validate_spell(dict(words='Test Wave',trigger='visible',range=7))
    check=evaluate_spell(s,state.game_state,[target],target,sample=state.passive_map_monitor.snapshot())
    assert not check['ready'] and check['matched_count']==0


@pytest.mark.parametrize('change',['wall','unknown','missing_flags','stale_map','stale_facing','unknown_facing'])
def test_wave_fails_closed_on_unknown_or_blocked_geometry(state,tmp_path,change):
    c,cells=configured(state,tmp_path);_,_,tile=monster(state,cells,x=11,y=10)
    target=c.combat.candidates()[0];sample=state.passive_map_monitor.snapshot()
    if change=='wall':tile['items'][0]['flags']=['blocks_projectiles']
    if change=='unknown':tile['kind']='unknown'
    if change=='missing_flags':tile['items'][0].pop('flags')
    if change=='stale_map':sample['stale']=True
    if change=='stale_facing':look(state.game_state,0,time.monotonic()-1)
    if change=='unknown_facing':look(state.game_state,None)
    check=evaluate_spell(c.combat.config['spells'][0],state.game_state,[target],target,sample=sample)
    assert not check['ready'] and check['reason'].startswith('Wave:')


def test_unknown_wave_never_inherits_an_invented_hit_mask(state,tmp_path,monkeypatch):
    c,cells=configured(state,tmp_path);monster(state,cells,x=11,y=10)
    monkeypatch.setattr(wave,'catalogue_waves',lambda:{'unknown wave'})
    target=c.combat.candidates()[0]
    check=evaluate_spell(validate_spell(dict(words='Unknown Wave')),state.game_state,[target],target,sample=state.passive_map_monitor.snapshot())
    assert not check['ready'] and 'profilu' in check['reason']


def test_turn_waits_for_fresh_client_ack_then_casts_without_any_walk(state,tmp_path):
    c,cells=configured(state,tmp_path);_,raw,_=monster(state,cells,x=11,y=10)
    sent=[]
    async def inject(payload,guard=None):
        assert guard();sent.append(payload);c.last_command=0
    state.game_proxy.inject_to_server=inject
    async def tick():
        raw['t']=time.time();await c.combat.tick(state.passive_map_monitor.snapshot())
    async def scenario():
        await tick()
        assert [p[0] for p in sent]==[ClientOpcode.ATTACK,ClientOpcode.TURN_EAST]
        assert not c.combat.deadlines and c.combat.last_spell_attempt is None
        await tick();assert len(sent)==2  # no repeated turn while waiting
        look(state.game_state,1,c.combat.wave_turn[1]-.001)
        await tick();assert len(sent)==2  # old direction is not an acknowledgment
        look(state.game_state,1)
        await tick()
        assert [p[0] for p in sent]==[ClientOpcode.ATTACK,ClientOpcode.TURN_EAST,ClientOpcode.SAY]
        assert c.combat.cast_after>time.monotonic() and state.game_state.position==(10,10,7)
    asyncio.run(scenario())


@pytest.mark.parametrize('mutation',['moved_player','moved_target','turned_player','wall','paused','manual_target'])
def test_queue_rechecks_hit_area_orientation_and_lifetime(state,tmp_path,mutation):
    c,cells=configured(state,tmp_path);cid,raw,tile=monster(state,cells,x=11,y=10)
    look(state.game_state,1)
    async def scenario():
        await c.combat.tick(state.passive_map_monitor.snapshot())
        queued=[]
        while not state.game_proxy._inject_queue.empty():queued.append(state.game_proxy._inject_queue.get_nowait())
        cast=next(row for row in queued if row[1][0]==ClientOpcode.SAY)
        assert cast[2]()
        if mutation=='moved_player':state.game_state.position=(10,11,7)
        if mutation=='moved_target':raw.update(x=12);tile.update(occupants=0,occupant_types={})
        if mutation=='turned_player':look(state.game_state,0)
        if mutation=='wall':tile['items'][0]['flags']=['blocks_projectiles']
        if mutation=='paused':c.pause('test')
        if mutation=='manual_target':c.combat.accept_manual_target(cid+1)
        assert not cast[2]()
    asyncio.run(scenario())


def test_shunkan_priority_does_not_turn_towards_lower_priority_wave(state,tmp_path):
    c,cells=configured(state,tmp_path)
    c.combat.config['spells'].insert(0,validate_spell(dict(words='Shunkan',aim='self',range=1,count=2)))
    monster(state,cells,x=11,y=10);monster(state,cells,x=11,y=11,cid=0x40000002)
    asyncio.run(c.combat.tick(state.passive_map_monitor.snapshot()))
    packets=[]
    while not state.game_proxy._inject_queue.empty():packets.append(state.game_proxy._inject_queue.get_nowait()[1])
    assert [p[0] for p in packets]==[ClientOpcode.ATTACK,ClientOpcode.SAY]
    assert b'Shunkan' in packets[-1]


def test_facing_field_is_optional_and_does_not_drop_healing(reader):
    r,obj=reader
    struct.pack_into('<I',obj,0x58,3);assert r.snapshot()['direction']==3
    struct.pack_into('<I',obj,0x58,7);assert r.snapshot()['direction']==7
    struct.pack_into('<I',obj,0x58,8)
    snap=r.snapshot();assert snap['direction'] is None and snap['hp']==36


def test_unreadable_facing_does_not_interrupt_healing(reader):
    from passive_map import MapReadError
    r,_=reader;read=r.source.read
    def missing(addr,size):
        if addr==r.ptr+0x58:raise MapReadError('facing only')
        return read(addr,size)
    r.source.read=missing
    snapshot=r.snapshot();assert snapshot['hp']==36 and snapshot['direction'] is None


def test_diagonal_facing_can_be_turned_to_cardinal_before_cast(state,tmp_path):
    c,cells=configured(state,tmp_path);monster(state,cells,x=11,y=10)
    look(state.game_state,4)
    asyncio.run(c.combat.tick(state.passive_map_monitor.snapshot()))
    packets=[]
    while not state.game_proxy._inject_queue.empty():packets.append(state.game_proxy._inject_queue.get_nowait()[1])
    assert [p[0] for p in packets]==[ClientOpcode.ATTACK,ClientOpcode.TURN_EAST]


@pytest.mark.parametrize('combat_only',[False,True])
def test_manual_selection_continues_combat_pins_target_and_invalidates_queue(state,tmp_path,combat_only):
    c,cells=armed(state,tmp_path);c.combat_only=combat_only
    first,_,_=monster(state,cells,x=11,y=10)
    second,_,_=monster(state,cells,x=12,y=10,cid=0x40000002)
    async def scenario():
        task=asyncio.create_task(c.run())
        try:
            await asyncio.sleep(.04)
            _,_,old_guard=state.game_proxy._inject_queue.get_nowait();assert old_guard()
            for cb in list(state.game_proxy._client_packet_callbacks):cb(ClientOpcode.ATTACK,PacketReader(struct.pack('<I',second)))
            assert c.status=='running' and not old_guard()
            await asyncio.sleep(.07)
            assert c.combat.target['id']==second and c.combat.sent_target==second
            assert c.status=='running' and state.game_state.position==(10,10,7)
            # Cancel/escape is still an explicit way to stop automation.
            for cb in list(state.game_proxy._client_packet_callbacks):cb(ClientOpcode.CANCEL_ATTACK,PacketReader(b''))
            assert c.status=='paused'
        finally:
            c.pause('test done');await asyncio.wait_for(task,1)
    asyncio.run(scenario())


def test_unmatched_manual_target_does_not_make_bot_attack_someone_else(state,tmp_path):
    c,cells=configured(state,tmp_path);monster(state,cells,x=11,y=10)
    c.combat.accept_manual_target(12345)
    asyncio.run(c.combat.tick(state.passive_map_monitor.snapshot()))
    assert c.status=='running' and c.combat.target is None and state.game_proxy._inject_queue.empty()


@pytest.mark.parametrize('direction,opcode',[(Direction.NORTH,0x6f),(Direction.EAST,0x70),(Direction.SOUTH,0x71),(Direction.WEST,0x72)])
def test_turn_is_a_single_stationary_opcode(direction,opcode):
    assert build_turn_packet(direction)==bytes([opcode])
