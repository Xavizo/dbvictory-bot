"""Synthetic regressions for dispatch races, room exits and local displacement."""
import asyncio,time
from types import SimpleNamespace
import pytest
import cavebot_combat
from protocol import ClientOpcode,PacketReader
from adaptive_cavebot import MovementDeferred
from test_automation_safety import state,proxy
from test_adaptive_cavebot import setup,cell,connect_fake_walk
from test_v2_combat import armed,monster,configure_rules

def refresh(state):
    state.game_state.stats_updated_at=time.time()
    for row in state.game_state.creatures.values():row['t']=time.time()

def three(c,state,cells):
    return [monster(state,cells,x=x,y=y,cid=0x40000001+i) for i,(x,y) in enumerate(((10,11),(11,11),(11,10)))]

def test_third_monster_arrives_while_attack_waits_no_route_step(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure',lure_count=3);c.status='running'
    monster(state,cells,x=10,y=11);monster(state,cells,x=11,y=11,cid=0x40000002)
    sent=[]
    async def inject(packet,guard=None):
        assert guard();sent.append(packet)
        monster(state,cells,x=11,y=10,cid=0x40000003)
    state.game_proxy.inject_to_server=inject
    assert asyncio.run(c.combat.tick(c.map_now()))
    assert c.combat.lure_stop_reason=='group' and c.combat.engaged_count==3
    assert [p[0] for p in sent]==[ClientOpcode.ATTACK]

def test_dispatch_guard_latches_group_and_rejects_queued_walk(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure',lure_count=3);c.status='running';c._route_command=True
    asyncio.run(c.send(b'\x66'))
    _,_,guard=state.game_proxy._inject_queue.get_nowait();assert guard()
    three(c,state,cells)
    assert not guard() and c.combat.lure_stop_reason=='group'
    # The group moving to 2 SQM cannot restart luring after the limit was reached.
    state.game_state.creatures.clear()
    for t in cells:t.update(occupants=0,occupant_types={})
    assert not guard() and c.status=='running'

def test_identity_gap_keeps_group_and_does_not_cancel_attack(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure',lure_count=3);c.status='running';three(c,state,cells)
    async def scenario():
        assert await c.combat.tick(c.map_now());target=c.combat.sent_target
        count=state.game_proxy._inject_queue.qsize()
        state.game_state.creatures.clear()
        assert await c.combat.tick(c.map_now())
        assert c.combat.sent_target==target and c.combat.lure_stop_reason=='group'
        assert state.game_proxy._inject_queue.qsize()==count
        for t in cells:t.update(occupants=0,occupant_types={})
        assert await c.combat.tick(c.map_now())
        c.combat.group_clear_since=time.monotonic()-.5
        assert not await c.combat.tick(c.map_now())
    asyncio.run(scenario())

def test_physical_third_monster_waits_for_identity_without_casting_at_unknown(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure',lure_count=3);c.status='running'
    rows=three(c,state,cells);state.game_state.creatures.pop(rows[2][0])
    assert asyncio.run(c.combat.tick(c.map_now()))
    assert c.combat.nearby_count==3 and c.combat.target['id']!=rows[2][0]


def test_map_only_companion_cannot_hold_floor_crossing_after_last_monster(state,tmp_path,monkeypatch):
    c,cells=armed(state,tmp_path,mode='lure',lure_count=4)
    configure_rules(c,[dict(pattern='*',action='attack')])
    c.status='running';clock=[10.]
    monkeypatch.setattr(cavebot_combat,'time',SimpleNamespace(time=time.time,monotonic=lambda:clock[0]))
    cid,row,mob=monster(state,cells,name='Fury Cell',x=10,y=11)
    row['health']=15
    pet=next(t for t in cells if t['x']==11 and t['y']==11)
    pet.update(occupants=1,occupant_types={'monster':1})
    current_pet=[pet]
    read_map=c.map_now
    published=[]
    def frame():
        sample=read_map()
        tile=current_pet[0]
        sample['pet_map_generation']=state.game_state.pets.observe_map(
            cells,time.time(),{(tile['x'],tile['y'],tile['z'],1):('visible',9)})
        published[:]=[sample]
        return sample
    monkeypatch.setattr(c,'map_now',lambda:published[0])
    async def scenario():
        assert await c.combat.tick(frame(),at_transition=True)
        assert c.combat.snapshot()['target']['name']=='Fury Cell'
        assert c.combat.snapshot()['lure']['engaged']==1 and c.combat.lure_stop_reason=='transition'
        state.game_state.creatures.clear()
        mob.update(occupants=0,occupant_types={})
        clock[0]+=.3
        assert await c.combat.tick(frame(),at_transition=True)
        assert c.combat.snapshot()['target'] is None and c.combat.snapshot()['lure'] is None
        # Same companion, different tile. The identity grace must not restart.
        pet.update(occupants=0,occupant_types={})
        mob.update(occupants=1,occupant_types={'monster':1});current_pet[0]=mob
        for advance in (.45,.45,.45):
            clock[0]+=advance
            await c.combat.tick(frame(),at_transition=True)
        clock[0]+=.45
        assert not await c.combat.tick(frame(),at_transition=True)
        assert c.combat.route_step_allowed(boundary=True)
        assert mob['occupants']==1 and c.combat.sent_target==0
        # A fresh hostile is eligible immediately, even with the companion still visible.
        new_id,_,_=monster(state,cells,name='Fury Cell',x=11,y=11,cid=cid+1)
        assert await c.combat.tick(frame(),at_transition=True)
        assert c.combat.target['id']==new_id and c.combat.lure_stop_reason=='transition'
    asyncio.run(scenario())


def test_one_anonymous_tile_cannot_permanently_latch_group(state,tmp_path,monkeypatch):
    c,cells=armed(state,tmp_path,mode='lure',lure_count=3)
    c.status='running';clock=[10.]
    monkeypatch.setattr(cavebot_combat,'time',SimpleNamespace(time=time.time,monotonic=lambda:clock[0]))
    rows=three(c,state,cells)
    state.game_state.creatures.pop(rows[-1][0])
    for elapsed in (0,.4,.8):
        clock[0]=10+elapsed
        assert not c.combat.route_step_allowed()
        assert c.combat.lure_stop_reason is None and c.combat.confirmed_count==2
    clock[0]=11.1
    assert c.combat.route_step_allowed() and c.combat.engaged_count==2
    state.game_state.creatures[rows[-1][0]]=rows[-1][1]
    assert not c.combat.route_step_allowed() and c.combat.lure_stop_reason=='group'

@pytest.mark.parametrize('kind',['door','level_gate','passage'])
def test_two_monsters_are_finished_before_open_room_exit(state,tmp_path,kind):
    c,cells=armed(state,tmp_path,mode='lure',lure_count=3);c.status='running'
    door=next(t for t in cells if (t['x'],t['y'])==(12,10))
    door['navigation']=[dict(kind=kind,door_state_hint='open',behavior_hint='open_then_step')]
    monster(state,cells,x=10,y=11);monster(state,cells,x=11,y=11,cid=0x40000002)
    assert c.route_boundary(c.map_now())
    assert asyncio.run(c.combat.tick(c.map_now(),at_transition=c.route_boundary(c.map_now())))
    assert c.combat.lure_stop_reason=='transition' and c.combat.engaged_count==2
    assert not c.combat.route_step_allowed(boundary=True)

def test_unknown_threat_behind_wall_does_not_latch_capacity(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure',lure_count=3);c.status='running'
    cells[:]=[cell(10,10),cell(11,10,kind='blocked'),cell(12,10)]
    cells[-1].update(occupants=3,occupant_types={'monster':3})
    assert c.combat.route_step_allowed(boundary=True)

def test_known_ignored_monsters_do_not_hold_room_exit(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure',lure_count=3);c.status='running';three(c,state,cells)
    configure_rules(c,[dict(pattern='Wolf',action='ignore'),dict(pattern='Dragon',action='attack')])
    assert c.combat.route_step_allowed(boundary=True)

def test_manual_step_preserves_combat_and_invalidates_pending_walk(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure',lure_count=3);monster(state,cells,x=10,y=11)
    async def scenario():
        task=asyncio.create_task(c.run())
        try:
            await asyncio.sleep(.08)
            for cb in list(state.game_proxy._client_packet_callbacks):cb(ClientOpcode.WALK_SOUTH,PacketReader(b''))
            assert c.status=='running' and c.manual_until>time.monotonic()
            state.game_state.position=(10,11,7)
            refresh(state)
            await asyncio.sleep(.1)
            assert c.status=='running'
            pending=list(state.game_proxy._inject_queue._queue)
            assert not any(payload==b'\xa1\0\0\0\0' for _,payload,_ in pending)
        finally:c.pause('test complete');await asyncio.wait_for(task,1)
    asyncio.run(scenario())

def test_external_push_recovers_without_global_pause(state,tmp_path):
    c,cells,_=setup(state,tmp_path);walked=connect_fake_walk(state,c);original=state.game_proxy.inject_to_server
    async def inject(payload,guard=None):
        await original(payload,guard)
        if len(walked)==1:state.game_state.position=(11,11,7)
    state.game_proxy.inject_to_server=inject
    asyncio.run(asyncio.wait_for(c.run(),1))
    assert c.status=='complete' and c.recovery_count>=1 and state.game_state.position==(13,10,7)

def test_repeated_local_pushes_do_not_exhaust_two_attempts(state,tmp_path):
    c,cells,_=setup(state,tmp_path);c.status='running'
    for pos in [(10,11,7),(10,12,7),(11,12,7),(11,11,7)]:
        c.recovery_next_at=0;state.game_state.position=pos
        assert c.recover_local(c.map_now()) and c.status=='running'
    assert c.recovery_count==4

def test_blocked_local_return_keeps_attack_then_rejoins(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure',lure_count=3);state.game_state.position=(10,11,7)
    c.status='running';c.path=[[10,10,7],[13,10,7]]
    cells[:]=[cell(10,11),cell(10,10,kind='blocked'),cell(11,11)]
    monster(state,cells,x=11,y=11)
    async def scenario():
        assert not c.recover_local(c.map_now())
        assert await c.combat.tick(c.map_now(),allow_movement=False)
        assert c.status=='running' and c.combat.sent_target
        cells[1]['kind']='clear';c.recovery_next_at=0
        assert c.recover_local(c.map_now())
    asyncio.run(scenario())

def test_unexpected_floor_or_large_displacement_remains_critical(state,tmp_path):
    c,cells,_=setup(state,tmp_path);c.status='running'
    for pos in [(10,11,8),(200,200,7)]:
        state.game_state.position=pos
        with pytest.raises(ValueError,match='Recovery'):c.recover_local(c.map_now())

def test_explicit_pause_still_expires_queue_after_manual_recovery(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure');c.status='running';c._route_command=True
    asyncio.run(c.send(b'\x66'));_,_,guard=state.game_proxy._inject_queue.get_nowait()
    assert guard();c.pause('user pause');assert not guard()

def test_dropped_queued_step_is_not_marked_as_terrain_blockage(state,tmp_path):
    c,cells,_=setup(state,tmp_path);c.status='running';c._route_command=True
    async def scenario():
        await c.send(b'\x66')
        _,_,guard=state.game_proxy._inject_queue.get_nowait()
        c.motion_generation+=1;assert not guard()
        with pytest.raises(MovementDeferred,match='nie został wysłany'):
            await c.wait_position([11,10,7],[10,10,7])
        assert c.blocked=={} and c.status=='running'
    asyncio.run(scenario())

def test_missing_adjacent_identity_adds_to_known_distant_follower(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure',lure_count=3);c.status='running'
    far=monster(state,cells,x=12,y=10)[0]
    c.combat.pursuit.following={far}
    for x,y in ((10,11),(11,11)):
        next(t for t in cells if t['x']==x and t['y']==y).update(occupants=1,occupant_types={'monster':1})
    assert not c.combat.route_step_allowed() and c.combat.engaged_count==3

