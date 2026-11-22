"""Offline route cursor, obstacle tolerance and sampler starvation regressions."""
import asyncio
import threading
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest
import mcp_server as server
from adaptive_cavebot import AdaptiveCavebot
from protocol import ClientOpcode
from test_automation_safety import state
from test_adaptive_cavebot import setup,cell,connect_fake_walk


def stage(state,tmp_path,points=None,loop=False):
    return setup(state,tmp_path,[cell(x,y) for x in range(10,22) for y in range(10,15)],
                 points or [[10,10,7],[12,10,7],[17,10,7]],loop=loop)


def test_slow_preparation_keeps_sampler_and_other_actions_alive(state,tmp_path,monkeypatch):
    c,_,draft=stage(state,tmp_path)
    c.status='complete'  # A fresh Start, not resumption of the fixture's ready plan.
    original=AdaptiveCavebot._plan_prepared
    def slow(self,*args):
        time.sleep(.65)
        original(self,*args)
    monkeypatch.setattr(AdaptiveCavebot,'_plan_prepared',slow)
    start=Mock(return_value=None);monkeypatch.setattr(server,'_start_action',start)
    gs=state.game_state;gs.client_state_required=True
    def refresh():gs.client_snapshot={'monotonic':time.monotonic(),'hp':100,'max_hp':100,'mana':100,'max_mana':100,'position':(10,10,7),'level':200}
    refresh()
    async def exercise():
        heartbeats=[]
        async def sampler():
            while True:refresh();heartbeats.append(time.monotonic());await asyncio.sleep(.02)
        ticker=asyncio.create_task(sampler())
        try:result=await server._async_adaptive_control('start',dict(scope=c.scope,draft_id=draft,loop=False))
        finally:ticker.cancel();await asyncio.gather(ticker,return_exceptions=True)
        assert len(heartbeats)>15
        assert max(b-a for a,b in zip(heartbeats,heartbeats[1:]))<.3
        assert result['status']=='ready' and result['waypoint']['current']==1
        assert time.monotonic()-gs.client_snapshot['monotonic']<.5
    asyncio.run(exercise())
    start.assert_called_once_with('adaptive_cavebot')
    assert state.game_proxy._inject_queue.empty()


@pytest.mark.parametrize('change',['stop','pause','emergency','move','move_back','draft','delete','stale','disconnect','recording','cancel'])
def test_pending_start_never_escapes_cancellation_or_changed_inputs(state,tmp_path,monkeypatch,change):
    c,_,draft=stage(state,tmp_path);c.status='complete';original=AdaptiveCavebot._plan_prepared
    entered=threading.Event();release=threading.Event()
    def slow(self,*args):
        entered.set();assert release.wait(3);original(self,*args)
    monkeypatch.setattr(AdaptiveCavebot,'_plan_prepared',slow)
    start=Mock(return_value=None);monkeypatch.setattr(server,'_start_action',start)
    async def exercise():
        pending=asyncio.create_task(server._async_adaptive_control('start',dict(scope=c.scope,draft_id=draft,loop=False)))
        while not entered.is_set():await asyncio.sleep(.005)
        with pytest.raises(ValueError,match='przygotowanie'):
            await server._async_adaptive_control('start',dict(scope=c.scope,draft_id=draft,loop=False))
        if change in {'stop','pause'}:await server._async_adaptive_control(change)
        elif change=='emergency':await server._async_emergency_stop()
        elif change=='move':state.game_state.position=(11,10,7)
        elif change=='move_back':
            for callback in list(state.game_proxy._client_packet_callbacks):callback(ClientOpcode.NEW_WALK,None)
        elif change=='draft':state.passive_map_monitor.atlas.save_draft('changed',[[10,10,7],[14,10,7]],draft)
        elif change=='delete':state.passive_map_monitor.atlas.change_draft(draft,'delete')
        elif change=='stale':state.game_state.stats_updated_at=0
        elif change=='disconnect':state.ready=False
        elif change=='recording':state.recording_active=True
        elif change=='cancel':pending.cancel()
        release.set()
        with pytest.raises((ValueError,asyncio.CancelledError)):await pending
        assert not state.settings['actions'].get('adaptive_cavebot',{}).get('enabled')
        assert state.adaptive_cavebot is c and state._adaptive_prepare_token is None
        assert not state.game_proxy._client_packet_callbacks
    try:asyncio.run(exercise())
    finally:release.set()
    start.assert_not_called()


@pytest.mark.parametrize('obstacle',['monster','player','blocked'])
def test_tolerance_marks_original_waypoint_and_replans_continuation(state,tmp_path,obstacle):
    c,cells,_=stage(state,tmp_path);c.status='running'
    target=next(t for t in cells if [t['x'],t['y'],t['z']]==[12,10,7])
    target.update(kind='blocked') if obstacle=='blocked' else target.update(occupants=1,occupant_types={obstacle:1})
    before=deepcopy(c.path)
    assert c.tolerate_waypoint(state.passive_map_monitor.snapshot())
    assert c.waypoint_progress()['current']==2 and c.waypoint_progress()['last_reached']==1
    assert [12,10,7] not in c.path and c.path[-1]==before[-1]
    assert 'Waypoint 2' in c.logs[-1] and '2 SQM' in c.logs[-1]
    sent=connect_fake_walk(state,c);asyncio.run(c.run())
    assert c.status=='complete' and state.game_state.position==(17,10,7)
    assert sent and c.waypoint_progress()['last_reached']==2


@pytest.mark.parametrize('missing_kind', ['unknown', 'no_ground'])
def test_route_passes_beside_unreadable_saiyan_anchor_using_only_observed_floor(state, tmp_path, missing_kind):
    start, anchor, end = [673, 411, 9], [673, 412, 9], [676, 412, 9]
    cells = [cell(x, y, 9) for x in range(672, 677) for y in range(410, 415)]
    controller, _, _ = setup(state, tmp_path, cells, [start, anchor, end])
    tile = next(c for c in cells if [c['x'], c['y'], c['z']] == anchor)
    tile.update(kind=missing_kind, items=[])
    walked = []
    connect_fake_walk(state, controller)
    accept = state.game_proxy.inject_to_server

    async def walk(payload, guard):
        await accept(payload, guard)
        walked.append(list(state.game_state.position))

    state.game_proxy.inject_to_server = walk
    asyncio.run(asyncio.wait_for(controller.run(), 2))
    assert controller.status == 'complete' and controller.tolerated_waypoints == 1
    assert walked[-1] == end and anchor not in walked


def test_manual_step_around_unreadable_waypoint_keeps_future_laps_connected(state, tmp_path):
    start, anchor, end = [673, 411, 9], [673, 412, 9], [676, 412, 9]
    manual = [674, 411, 9]
    cells = [cell(x, y, 9) for x in range(672, 677) for y in range(410, 415)]
    controller, _, _ = setup(state, tmp_path, cells, [start, anchor, end, start], loop=True)
    next(tile for tile in cells if [tile['x'], tile['y'], tile['z']] == anchor).update(kind='unknown', items=[])
    state.game_state.position = tuple(manual)
    controller.status = 'running'
    assert controller.recover_local(state.passive_map_monitor.snapshot())
    assert controller.path[controller.index] == manual and controller.tolerated_waypoints == 1
    assert controller.path[controller.loop_start] == controller.path[-1] == start
    assert all(max(abs(a-b) for a, b in zip(prev, curr)) == 1
               for prev, curr in zip(controller.path, controller.path[1:]))
    connect_fake_walk(state, controller)
    accept = state.game_proxy.inject_to_server
    walked = []

    async def walk_two_laps(payload, guard):
        await accept(payload, guard)
        walked.append(list(state.game_state.position))
        if controller.laps == 2:
            controller.stop(preserve_progress=True)

    state.game_proxy.inject_to_server = walk_two_laps
    asyncio.run(asyncio.wait_for(controller.run(), 3))
    assert walked[0] != start and anchor not in walked and controller.laps == 2


@pytest.mark.parametrize('reason',['clear','distant','unknown','stairs','door','entry','exit','origin','paused','stale','no_path','no_continuation','floor'])
def test_tolerance_does_not_falsely_credit_special_unreachable_or_distant_points(state,tmp_path,reason):
    c,cells,_=stage(state,tmp_path);c.status='running'
    target=next(t for t in cells if [t['x'],t['y'],t['z']]==[12,10,7]);target.update(occupants=1)
    if reason=='clear':target['occupants']=0
    elif reason=='distant':state.game_state.position=(10,13,7);c.path[0]=[10,13,7]
    elif reason=='unknown':cells.remove(target)
    elif reason in {'stairs','door'}:target['navigation']=[dict(kind=reason)]
    elif reason in {'origin','entry','exit'}:
        c.edges=[dict(origin=[20,10,7],entry=[20,11,7],exit=[20,10,6])];c.edges[0][reason]=[12,10,7]
    elif reason=='paused':c.pause('manual')
    elif reason=='no_path':
        for tile in cells:
            if (tile['x'],tile['y'])!=(10,10):tile['kind']='blocked'
    elif reason=='no_continuation':c.checkpoints=c.checkpoints[:2]
    elif reason=='floor':state.game_state.position=(10,10,6);c.path[0]=[10,10,6]
    sample=state.passive_map_monitor.snapshot()
    if reason=='stale':sample['age_seconds']=.7
    path=deepcopy(c.path);progress=deepcopy(c.waypoint_progress())
    assert not c.tolerate_waypoint(sample)
    assert c.path==path and c.waypoint_progress()==progress
    assert state.game_proxy._inject_queue.empty()


def test_generic_detour_does_not_credit_far_away_waypoint(state,tmp_path):
    c,cells,_=stage(state,tmp_path,points=[[10,10,7],[17,10,7],[20,10,7]])
    for t in cells:
        if t['x'] in {11,17} and t['y']==10:t.update(occupants=1)
    sent=connect_fake_walk(state,c);positions=[];inject=state.game_proxy.inject_to_server
    async def observe(payload,guard=None):
        if c.tolerated_waypoints:positions.append(state.game_state.position)
        await inject(payload,guard)
    state.game_proxy.inject_to_server=observe
    asyncio.run(c.run())
    assert sent and c.status=='complete' and c.tolerated_waypoints==1
    assert positions and max(abs(positions[0][0]-17),abs(positions[0][1]-10))<=2


def test_loop_endpoint_tolerance_has_real_return_and_keeps_next_waypoint_identity(state,tmp_path):
    c,cells,_=stage(state,tmp_path,points=[[10,10,7],[14,10,7],[14,13,7],[10,10,7]],loop=True)
    c.index=len(c.path)-2;state.game_state.position=tuple(c.path[c.index]);c.status='running'
    next(t for t in cells if (t['x'],t['y'])==(10,10)).update(occupants=1)
    assert c.tolerate_waypoint(state.passive_map_monitor.snapshot())
    assert c.path[-1]==c.path[c.loop_start] and c.waypoint_progress()['last_reached']==3
    sent=connect_fake_walk(state,c);inject=state.game_proxy.inject_to_server
    async def bounded(payload,guard=None):
        await inject(payload,guard)
        if len(sent)>10:c.pause('bounded')
    state.game_proxy.inject_to_server=bounded;asyncio.run(c.run())
    assert c.laps>=1 and (10,10,7)!=state.game_state.position


@pytest.mark.parametrize('loop',[False,True])
def test_stop_resume_keeps_waypoint_and_safe_approach_without_commands(state,tmp_path,loop):
    c,_,_=stage(state,tmp_path,loop=loop);c.index=3;c.mark_waypoint()
    state.game_state.position=tuple(c.path[c.index]);c.status='running'
    before=c.waypoint_progress();c.last_command=123
    c.stop(preserve_progress=True)
    assert c.status=='idle' and c.snapshot()['resumable'] and c.waypoint_progress()==before
    same=asyncio.run(c.resume_candidate())
    assert same.index==c.index and same.path==c.path and same.last_command==123
    state.game_state.position=(13,12,7)
    other=asyncio.run(c.resume_candidate())
    assert other.path[0]==[13,12,7] and c.path[c.index] in other.path
    assert other.waypoint_progress()['current']==before['current']
    if loop:assert other.path[-1]==other.path[other.loop_start]
    assert state.game_proxy._inject_queue.empty()


@pytest.mark.parametrize('change',['floor','unknown','draft','emergency'])
def test_resume_refuses_invalid_saved_progress(state,tmp_path,change):
    c,_,draft=stage(state,tmp_path);c.stop(preserve_progress=True)
    if change=='floor':state.game_state.position=(10,10,6)
    elif change=='unknown':state.game_state.position=(100,100,7)
    elif change=='draft':state.passive_map_monitor.atlas.save_draft('changed',[[10,10,7],[11,10,7]],draft)
    else:state.automation_paused=True
    with pytest.raises(ValueError):asyncio.run(c.resume_candidate())
    assert state.game_proxy._inject_queue.empty()


def test_session_stop_clears_progress_and_never_autoresumes(state,tmp_path):
    c,_,_=stage(state,tmp_path);c.stop()
    assert not c.path and not c.snapshot()['resumable'] and c.waypoint_progress()['current'] is None


def test_resume_acknowledges_exact_exit_received_during_pause(state,tmp_path):
    from test_adaptive_cavebot import stair_edge
    edge=stair_edge()
    c,_,_=setup(state,tmp_path,[cell(10,10),cell(10,9,kind='blocked',item=1956),cell(20,20,6),cell(21,20,6)],
                [[10,10,7],[20,20,6],[21,20,6]],[edge])
    c.pause('Stopped while waiting for stair acknowledgment')
    state.game_state.position=(20,20,6)
    resumed=asyncio.run(c.resume_candidate())
    assert resumed.index==1 and resumed.waypoint_progress()['current']==2
    assert c.index==0 and state.game_proxy._inject_queue.empty()


def corner_route(state, tmp_path):
    points = [[10,10,7], [14,10,7], [14,14,7]]
    cells = [cell(x,y) for x in range(9,17) for y in range(8,16)]
    c, _, _ = setup(state, tmp_path, cells, points)
    c.path = [[x,10,7] for x in range(10,15)] + [[14,y,7] for y in range(11,15)]
    c.checkpoints = [dict(index=i, waypoint=w) for w,i in enumerate((0,4,8))]
    c.index = 3
    state.game_state.position = tuple(c.path[3])
    return c, cells


def test_open_corner_skips_only_after_ack_and_does_not_change_draft(state, tmp_path):
    c, _ = corner_route(state, tmp_path)
    original = deepcopy(c.route_draft)
    assert c.pass_ordinary_waypoint(c.map_now())
    assert c.index == 3 and c.waypoint_progress()['current'] == 1
    connect_fake_walk(state, c)
    send = state.game_proxy.inject_to_server
    walked = []

    async def walk(payload, guard):
        await send(payload, guard)
        walked.append(state.game_state.position)
        if len(walked) == 1:
            assert c.last_waypoint == 0

    state.game_proxy.inject_to_server = walk
    asyncio.run(asyncio.wait_for(c.run(), 2))
    assert walked[0] == (14,11,7) and (14,10,7) not in walked
    assert c.tolerated_waypoints == 1 and c.last_waypoint == 2
    assert c.route_draft == original and c.path[4] == [14,10,7]


def test_resume_after_corner_arrival_before_route_tick(state, tmp_path):
    c, _ = corner_route(state, tmp_path)
    c.status = 'paused'
    c._pending_shortcut = [3, [14,11,7]]
    state.game_state.position = (14,11,7)
    resumed = asyncio.run(c.resume_candidate())
    assert (resumed.index, resumed.last_waypoint, resumed.tolerated_waypoints) == (5, 1, 1)
    assert resumed._pending_shortcut is None
    assert state.game_proxy._inject_queue.empty()


@pytest.mark.parametrize('condition', ['open', 'wall', 'action', 'label', 'stale', 'lure_stop'])
def test_displaced_beside_waypoint_requires_open_connection(state, tmp_path, condition):
    c, cells = corner_route(state, tmp_path)
    c.status = 'running'
    state.game_state.position = (13,12,7)  # two tiles from the draft marker
    sample = c.map_now()
    if condition == 'wall':
        next(t for t in cells if (t['x'],t['y']) == (13,11))['kind'] = 'blocked'
    if condition == 'action':
        c.edges = [dict(origin=[14,10,7],entry=[14,11,7],exit=[14,10,6])]
    if condition == 'label':
        c.route_draft['labels'] = ['', 'zbiórka', '']
    if condition == 'stale':
        sample['stale'] = True
    if condition == 'lure_stop':
        c.combat.lure_stop_reason = 'limit'
    if condition != 'open':
        assert not c.pass_ordinary_waypoint(sample, displaced=True)
        assert c.index == 3 and c.tolerated_waypoints == 0
        return
    assert c.recover_local(sample)
    assert c.index == 4 and c.last_waypoint == 1 and c.tolerated_waypoints == 1
    assert c.path[4] == [13,12,7]
    connect_fake_walk(state,c)
    asyncio.run(asyncio.wait_for(c.run(), 2))
    assert c.path[4] == [14,10,7] and c.last_waypoint == 2


def test_free_waypoint_in_single_tile_corridor_stays_exact(state, tmp_path):
    points = [[10,10,7], [14,10,7], [18,10,7]]
    c, _, _ = setup(state, tmp_path, [cell(x,10) for x in range(10,19)], points)
    connect_fake_walk(state,c)
    asyncio.run(asyncio.wait_for(c.run(), 2))
    assert c.tolerated_waypoints == 0 and c.status == 'complete'
    assert c.last_waypoint == 2


def test_open_room_prefers_clearance_and_restores_waypoint_after_pass(state, tmp_path):
    points = [[10,10,7], [14,10,7], [18,10,7]]
    c, _, _ = setup(state,tmp_path,[cell(x,y) for x in range(9,20) for y in range(10,15)],points)
    c.path = [[x,10,7] for x in range(10,19)]
    c.checkpoints = [dict(index=i,waypoint=w) for w,i in enumerate((0,4,8))]
    c.index = 3
    state.game_state.position = (13,10,7)
    c.pass_ordinary_waypoint(c.map_now())
    assert c.path[4] == [14,11,7] and c.last_waypoint == 0
    connect_fake_walk(state,c)
    asyncio.run(asyncio.wait_for(c.run(), 2))
    assert c.tolerated_waypoints == 1 and c.path[4] == points[1]


def test_new_door_between_player_and_marker_revokes_pending_shortcut(state, tmp_path):
    c, cells = corner_route(state, tmp_path)
    origin = c.path[c.index]
    assert c.pass_ordinary_waypoint(c.map_now())
    next(t for t in cells if (t['x'],t['y']) == (13,11))['navigation'] = [dict(kind='door')]
    assert not c.waypoint_pass_still_clear(origin)
    assert c.last_waypoint == 0 and c.index == 3
    assert state.game_proxy._inject_queue.empty()


def test_adjacent_draft_markers_keep_their_exact_step(state, tmp_path):
    c, cells = corner_route(state, tmp_path)
    c.checkpoints.insert(1, dict(index=3, waypoint=0))
    original = deepcopy(c.path)
    assert not c.pass_ordinary_waypoint(c.map_now())
    assert c.path == original
