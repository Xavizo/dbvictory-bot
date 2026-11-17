"""Regression scenarios from the live lure run; synthetic game transport only."""
import asyncio,json,time
from copy import deepcopy
import pytest
import dashboard_api as api
from dashboard_security import SESSION_TOKEN,DASHBOARD_ORIGIN
from test_dashboard_security import http_api
from test_automation_safety import state,proxy
from test_adaptive_cavebot import setup,cell,connect_fake_walk
from test_v2_combat import armed,monster,configure_rules
from cavebot_combat import eligible_monsters
from combat_reachability import reachable_tiles
from adaptive_cavebot import MovementDeferred
from protocol import ClientOpcode

def test_sealed_room_monsters_do_not_hold_lure_or_receive_attacks(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure',engage='visible');c.status='running'
    for tile in cells:
        if tile['x']==11:tile['kind']='blocked'
    monster(state,cells,x=12,y=11)
    assert not eligible_monsters(state.game_state,c.map_now(),c.combat.config)
    assert not asyncio.run(c.combat.tick(c.map_now()))
    assert c.combat.target is None and state.game_proxy._inject_queue.empty()

@pytest.mark.parametrize('kind',['door','level_gate'])
def test_closed_door_seals_target_but_open_door_restores_access(state,tmp_path,kind):
    c,cells=armed(state,tmp_path,mode='lure');c.status='running'
    for tile in cells:
        if tile['x']==11:tile['kind']='blocked'
    door=next(t for t in cells if (t['x'],t['y'])==(11,10))
    door['navigation']=[dict(kind=kind,door_state_hint='closed')]
    cid,_,_=monster(state,cells,x=12,y=10)
    assert not eligible_monsters(state.game_state,c.map_now(),c.combat.config)
    door['kind']='clear';door['navigation'][0]['door_state_hint']='open'
    assert [t['id'] for t in eligible_monsters(state.game_state,c.map_now(),c.combat.config)]==[cid]

@pytest.mark.parametrize('kind',['hole','stairs','teleport','trapdoor','passage','shovel_spot'])
def test_reachability_never_crosses_implicit_transitions(kind):
    cells=[cell(10,10),cell(11,10,navigation=[dict(kind=kind)]),cell(12,10)]
    assert (12,10,7) not in reachable_tiles(cells,(10,10,7))

def test_occupants_do_not_make_monster_queue_unreachable():
    cells=[cell(x,10) for x in range(10,15)]
    for c in cells[1:]:c.update(occupants=1,occupant_types={'monster':1})
    assert (14,10,7) in reachable_tiles(cells,(10,10,7))
    cells[2]['kind']='unknown'
    assert (14,10,7) not in reachable_tiles(cells,(10,10,7))

def test_four_adjacent_monsters_stop_luring_immediately_without_pursuit_history(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure',lure_count=4,spells=[dict(words='Shunkan',aim='self',range=1,count=2)])
    cells[:]=[cell(x,y) for x in range(8,14) for y in range(8,14)]
    for i,(x,y) in enumerate(((9,9),(10,9),(11,9),(11,10))):monster(state,cells,x=x,y=y,cid=0x40000001+i)
    c.status='running';state.game_state.mana=state.game_state.max_mana=100;sent=[]
    async def inject(payload,guard=None):assert guard();sent.append(payload);c.last_command=0
    state.game_proxy.inject_to_server=inject
    assert asyncio.run(c.combat.tick(c.map_now()))
    result=c.combat.snapshot()['lure']
    assert result['engaged']==4 and result['following']==0 and result['stop_reason']=='group'
    assert [p[0] for p in sent]==[ClientOpcode.ATTACK,ClientOpcode.SAY]

def test_pursuit_and_adjacent_sets_are_deduplicated(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure',lure_count=4);c.status='running'
    for i,(x,y) in enumerate(((11,10),(11,11),(13,11))):monster(state,cells,x=x,y=y,cid=0x40000001+i)
    candidates=eligible_monsters(state.game_state,c.map_now(),c.combat.config)
    c.combat.policy_mode='lure';c.combat.pursuit.update=lambda *args:candidates
    asyncio.run(c.combat.tick(c.map_now()))
    assert c.combat.engaged_count==3 and c.combat.lure_stop_reason is None

def test_queued_attack_invalidated_when_door_closes(state,tmp_path):
    c,cells=armed(state,tmp_path);c.status='running';monster(state,cells,x=12,y=11)
    asyncio.run(c.combat.tick(c.map_now()))
    _,payload,guard=state.game_proxy._inject_queue.get_nowait()
    assert payload[0]==ClientOpcode.ATTACK and guard()
    for tile in cells:
        if tile['x']==11:tile['kind']='blocked'
    assert not guard()
    assert not asyncio.run(c.combat.tick(c.map_now()))
    assert c.combat.target is None

def test_target_identity_gap_before_transition_does_not_allow_instant_descent(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure');c.status='running'
    async def scenario():
        assert await c.combat.tick(c.map_now(),at_transition=True)
        assert state.game_proxy._inject_queue.empty()
        monster(state,cells,x=11,y=10)
        assert await c.combat.tick(c.map_now(),at_transition=True)
        assert c.combat.target and c.combat.lure_stop_reason=='transition'
    asyncio.run(scenario())

def test_route_can_cross_after_quiet_transition_check(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure');c.status='running'
    async def scenario():
        assert await c.combat.tick(c.map_now(),at_transition=True)
        c.combat.transition_clear_since=time.monotonic()-.5
        assert not await c.combat.tick(c.map_now(),at_transition=True)
    asyncio.run(scenario())

def test_moving_monster_before_scheduled_step_defers_without_sending_or_pausing(state,tmp_path,monkeypatch):
    c,cells=armed(state,tmp_path);c.status='running';target=[11,10,7]
    async def change(_):
        next(t for t in cells if [t['x'],t['y'],t['z']]==target).update(occupants=1,occupant_types={'monster':1})
    monkeypatch.setattr(asyncio,'sleep',change)
    with pytest.raises(MovementDeferred):asyncio.run(c.step(target))
    assert c.status=='running' and state.game_proxy._inject_queue.empty()

def test_route_rechecks_combat_after_blocked_path_and_resumes_when_clear(state,tmp_path):
    c,cells,_=setup(state,tmp_path,cells=[cell(10,10),cell(11,10),cell(12,10)],points=[[10,10,7],[12,10,7]])
    configure_rules(c,[dict(pattern='Wolf')],enabled=True,mode='lure')
    cells[1].update(occupants=1,occupant_types={'player':1})
    walked=connect_fake_walk(state,c);calls=[]
    original=c.combat.tick
    async def tick(sample,at_transition=False):
        calls.append(c.status)
        if len(calls)==3:cells[1].update(occupants=0,occupant_types={})
        return await original(sample,at_transition)
    c.combat.tick=tick
    asyncio.run(asyncio.wait_for(c.run(),2))
    assert len(calls)>=4 and all(v=='running' for v in calls)
    assert c.status=='complete' and len(walked)==2

def test_loop_toggle_updates_only_execution_option(state,tmp_path):
    c,cells,draft=setup(state,tmp_path);atlas=state.passive_map_monitor.atlas
    before=deepcopy(atlas.drafts()[0]);atlas.change_draft(draft,'set_loop',loop=True)
    after=atlas.drafts()[0]
    assert after['loop'] is True and after['navigation_revision']!=before['navigation_revision']
    for key in ('id','name','points','labels','links','combat'):assert after[key]==before[key]
    with pytest.raises(ValueError):atlas.change_draft(draft,'set_loop',loop=1)
    assert atlas.drafts()[0]==after

def test_no_damage_target_does_not_pause_other_combat(state,tmp_path):
    c,cells=armed(state,tmp_path);c.status='running'
    first,_,_=monster(state,cells,x=11,y=10)
    second,_,_=monster(state,cells,x=12,y=11,cid=0x40000002)
    async def scenario():
        await c.combat.tick(c.map_now())
        assert c.combat.target['id']==first
        c.combat.progress_at=time.monotonic()-21
        await c.combat.tick(c.map_now())
        await c.combat.tick(c.map_now())
        assert c.status=='running' and c.combat.target['id']==second
        assert first in c.combat.stalled_targets
    asyncio.run(scenario())

def test_uncertain_transition_keeps_stationary_combat_without_repeating_use(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='chase');monster(state,cells,x=13,y=11)
    c.edges=[dict(origin=c.path[0],entry=[10,9,7],exit=c.path[1],action='use')]
    # Reach transition before any target appears; then report a failed use.
    state.game_state.creatures.clear()
    for t in cells:t.update(occupants=0,occupant_types={})
    calls=[];sent=[]
    async def transition(*args):
        calls.append(True);monster(state,cells,x=13,y=11)
        raise ValueError('Test: drzwi nie potwierdziły otwarcia.')
    async def inject(payload,guard=None):
        assert guard();sent.append(payload);c.last_command=0
        if payload[0]==ClientOpcode.ATTACK:c.stop(preserve_progress=True)
    c.transition=transition;state.game_proxy.inject_to_server=inject
    asyncio.run(asyncio.wait_for(c.run(),2))
    assert len(calls)==1 and any(p[0]==ClientOpcode.ATTACK for p in sent)
    assert state.game_state.position==(10,10,7)

def test_navigation_retry_is_explicit_and_rechecks_position(state,tmp_path):
    c,cells=armed(state,tmp_path);c.status='running';c.navigation_hold='Test'
    state.game_state.position=(12,11,7)
    with pytest.raises(ValueError,match='Pozycja'):c.retry_navigation()
    assert c.navigation_hold=='Test'
    state.game_state.position=tuple(c.path[0]);c.retry_navigation()
    assert c.navigation_hold is None and c.status=='running'

def test_loop_toggle_http_is_separate_from_sketch_and_rejected_while_running(http_api,state,tmp_path,monkeypatch):
    c,cells,draft=setup(state,tmp_path);monkeypatch.setattr(api,'_state',state)
    request,_=http_api;body=json.dumps(dict(scope=c.scope,id=draft,operation='set_loop',loop=True))
    headers={'Authorization':f'Bearer {SESSION_TOKEN}','Origin':DASHBOARD_ORIGIN}
    c.status='stopped'
    assert request('POST','/api/map/drafts',body=body,**headers)[0]==200
    c.status='running';body=json.dumps(dict(scope=c.scope,id=draft,operation='set_loop',loop=False))
    assert request('POST','/api/map/drafts',body=body,**headers)[0]==400
    assert state.passive_map_monitor.atlas.drafts()[0]['loop'] is True
