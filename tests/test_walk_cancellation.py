import asyncio
import logging
import time

import pytest

from cavebot_walking import WalkOutcome
from adaptive_cavebot import OFFSETS, MovementDeferred
from game_state import scan_packet
from protocol import ServerOpcode, WALK_OPCODES
from test_adaptive_cavebot import cell, connect_fake_walk, setup
from test_automation_safety import state


def cancel_walk(gs, direction, received_at):
    scan_packet(bytes([ServerOpcode.PLAYER_CANCEL_WALK, direction]), gs, received_at=received_at)


@pytest.mark.parametrize("invalid_direction", [8, 138, 255])
def test_invalid_cancel_leaves_position_and_previous_event_intact(state, invalid_direction):
    gs = state.game_state
    gs.position = gs.packet_position = (673, 411, 9)
    gs._last_walk_delta = (0, 1)
    gs.last_walk_cancel = (10., (673, 410, 9))
    cancel_walk(gs, invalid_direction, 20.)
    assert gs.position == gs.packet_position == (673, 411, 9)
    assert gs._last_walk_delta == (0, 1)
    assert gs.last_walk_cancel == (10., (673, 410, 9))
    assert gs.cancel_walk_time == 0 and not gs.server_events


@pytest.mark.parametrize("origin,target,facing,during_wait", [
    ((697, 394, 9), (697, 395, 9), 3, False),
    ((671, 475, 7), (672, 476, 7), 2, True),
])
def test_cancel_from_saiyan_walk_releases_position_wait(state, tmp_path, caplog, origin, target, facing, during_wait):
    cells = [cell(x, y, origin[2]) for x in range(origin[0], origin[0]+3)
             for y in range(origin[1], origin[1]+3)]
    ctrl, _, _ = setup(state, tmp_path, cells, [list(origin), list(target)])
    caplog.set_level(logging.INFO, logger="adaptive_cavebot")

    async def inject(payload, guard):
        assert guard()
        if during_wait:
            asyncio.get_running_loop().call_later(.02, lambda: cancel_walk(state.game_state, facing, time.perf_counter()))
        else:
            # The parser can publish the reply before transport drain returns.
            cancel_walk(state.game_state, facing, time.perf_counter())

    async def scenario():
        ctrl.status = "running"
        ctrl._route_command = True
        state.game_proxy.inject_to_server = inject
        assert not await asyncio.wait_for(ctrl.step(list(target)), .6)
        assert ctrl.index == 0 and ctrl.status == "running"
        assert ctrl._map_after_walk is None

    asyncio.run(scenario())
    assert "map_ms=- outcome=position_cancelled" in caplog.text


def test_gotrunks_cancel_with_late_arrival_does_not_send_a_detour(state, tmp_path):
    origin, arrived, end = [1055,1281,7], [1055,1282,7], [1055,1283,7]
    cells = [cell(x,y) for x in range(1054,1058) for y in range(1281,1285)]
    ctrl, _, _ = setup(state, tmp_path, cells, [origin, end])
    visited = []
    gs = state.game_state

    async def dispatch(payload, guard):
        assert guard()
        x,y,z = gs.position
        dx,dy = next(delta for delta, direction in OFFSETS.items() if int(WALK_OPCODES[direction]) == payload[0])
        tile = (x+dx,y+dy,z)
        visited.append(tile)
        if len(visited) == 1:
            cancel_walk(gs, 1, time.perf_counter())
            asyncio.get_running_loop().call_later(.047, setattr, gs, 'position', tile)
        else:
            gs.position = tile
        ctrl.last_command = 0

    state.game_proxy.inject_to_server = dispatch
    asyncio.run(asyncio.wait_for(ctrl.run(), 2))
    assert ctrl.status == 'complete' and visited == [tuple(arrived), tuple(end)]


def test_gotrunks_offroute_retries_after_block_expires(state, tmp_path):
    origin, waypoint, end = [1055,1282,7], [1055,1281,7], [1054,1280,7]
    pos = (1056,1282,7)
    tiles = [cell(*p) for p in (origin, waypoint, end, pos)]
    ctrl, _, _ = setup(state, tmp_path, tiles, [origin, waypoint, end])
    ctrl.status = 'running'
    state.game_state.position = pos
    now = time.monotonic()
    ctrl.blocked = {tuple(p):now+10 for p in (origin, waypoint, end)}
    assert not ctrl.recover_local(ctrl.map_now())
    ctrl.recovery_next_at = 0
    ctrl.blocked[tuple(origin)] = now-1
    tiles[0].update(occupants=1, occupant_types={'monster':1})
    assert not ctrl.recover_local(ctrl.map_now())
    ctrl.recovery_next_at = 0
    tiles[0].update(occupants=0, occupant_types={})
    assert ctrl.recover_local(ctrl.map_now())
    assert tuple(origin) not in ctrl.blocked and ctrl.blocked[tuple(waypoint)] > now
    assert ctrl.path[ctrl.index] == list(pos)


def test_gotrunks_displacement_keeps_reached_waypoint_and_route(state, tmp_path):
    origin, onward = [1055,1281,7], [1055,1284,7]
    tiles = [cell(x,y) for x in range(1054,1058) for y in range(1281,1285)]
    ctrl, _, _ = setup(state, tmp_path, tiles, [origin, onward])
    saved_path = list(ctrl.path)
    saved_points = list(ctrl.checkpoints)
    ctrl.status = 'running'
    ctrl.failures = 2  # two consecutive position_unconfirmed events in the recording
    state.game_state.position = (1056,1282,7)
    connect_fake_walk(state, ctrl)
    assert asyncio.run(ctrl.recover_position(ctrl.map_now()))
    assert ctrl.path == saved_path and ctrl.checkpoints == saved_points
    assert state.game_state.position != tuple(origin)
    assert ctrl.waypoint_progress()['current'] == 1
    # Navigation reconciles the confirmed temporary step on its next tick.
    assert asyncio.run(ctrl.recover_position(ctrl.map_now()))
    assert ctrl.path[ctrl.index] == list(state.game_state.position)
    asyncio.run(asyncio.wait_for(ctrl.run(), 2))
    assert ctrl.status == 'complete' and state.game_state.position == tuple(onward)


def test_cancelled_route_step_replans_and_finishes_without_manual_resume(state, tmp_path):
    ctrl, _, _ = setup(state, tmp_path)
    origin, rejected = (10, 10, 7), (11, 10, 7)
    connect_fake_walk(state, ctrl)
    accept = state.game_proxy.inject_to_server
    visited = []

    async def inject(payload, guard):
        if not visited:
            assert guard()
            await asyncio.sleep(.02)
            cancel_walk(state.game_state, 3, time.perf_counter())
        else:
            await accept(payload, guard)
        visited.append(state.game_state.position)

    state.game_proxy.inject_to_server = inject
    asyncio.run(asyncio.wait_for(ctrl.run(), 1.5))
    assert visited[0] == origin and rejected not in visited
    assert visited[-1] == (13, 10, 7) and ctrl.status == "complete"
    assert any("serwer anulował krok" in msg for msg in ctrl.logs)



@pytest.mark.parametrize('start_x', [11, 14])
def test_join_stairs_from_other_side_without_walking_to_recorded_origin(state,tmp_path,start_x):
    recorded,entry,landing = [9,10,7],[10,10,7],[10,11,6]
    side = [11,10,7]
    floor = cell(*entry,kind='blocked',item=5259,navigation=[dict(kind='stairs')])
    edge = dict(id='up',origin=recorded,entry=entry,exit=landing,action='step',entry_items=[5259],evidence='observed')
    cells = [cell(*p) for p in (recorded,landing)] + [cell(x,10,7) for x in range(11,15)] + [floor]
    ctrl,_,draft = setup(state,tmp_path,cells,[recorded,landing],[edge])
    gs = state.game_state
    gs.position = (start_x,10,7)
    ctrl.prepare(state.passive_map_monitor.atlas.scope,draft,False)
    assert ctrl.path == [[x,10,7] for x in range(start_x,10,-1)] + [landing]
    assert ctrl.checkpoints == [dict(index=start_x-11,waypoint=0),dict(index=start_x-10,waypoint=1)]
    async def dispatch(payload,guard):
        assert guard()
        if list(gs.position) == side:
            assert ctrl.last_waypoint == 0
            gs.position = tuple(landing)
        else:
            gs.position = (gs.position[0]-1,10,7)
        ctrl.last_command = 0
    state.game_proxy.inject_to_server=dispatch
    asyncio.run(asyncio.wait_for(ctrl.run(),2))
    assert ctrl.status == 'complete' and ctrl.last_waypoint == 1


def test_stairs_with_same_landing_respect_selected_entrance(state,tmp_path):
    side,entry,landing = [10,10,7],[10,11,7],[10,11,6]
    stairs = cell(*entry,kind='blocked',item=5259,navigation=[dict(kind='stairs')])
    edge = dict(id='chosen',origin=side,entry=entry,exit=landing,action='step',entry_items=[5259],evidence='observed')
    ctrl,_,_=setup(state,tmp_path,[cell(*side),stairs,cell(*landing)],[side,landing],[edge])
    chosen = ctrl.edges[0]
    ctrl.edges.insert(0,{**chosen,'id':'other','entry':[11,11,7]})
    assert ctrl.floor_edge_at(0) == chosen
    ctrl.route_draft['links'] = []
    assert ctrl.floor_edge_at(0) is None


def test_stair_cancel_retries_from_current_side_after_entrance_clears(state,tmp_path):
    anchor,approach,entry,landing=[1039,1300,4],[1038,1299,4],[1038,1300,4],[1037,1300,3]
    stairs=cell(*entry,kind='blocked',item=5259,navigation=[{'kind':'stairs'}])
    cells=[cell(*p) for p in (anchor,approach,landing)]+[stairs]
    edge=dict(id='stairs',origin=approach,entry=entry,exit=landing,action='step',entry_items=[5259],evidence='observed')
    ctrl,_,_=setup(state,tmp_path,cells,[approach,landing],[edge])
    atlas=state.passive_map_monitor.atlas
    draft=atlas.save_draft('side approach',[anchor,landing],links=[dict(from_index=0,id='stairs')])
    gs=state.game_state
    gs.position=tuple(anchor)
    ctrl.prepare(atlas.scope,draft,False)
    walked=[]
    async def simulate_step(payload,guard):
        assert guard()
        walked.append(gs.position)
        assert gs.position == tuple(anchor)
        if len(walked)==1:
            cancel_walk(gs,2,time.perf_counter())
            stairs.update(occupants=1,occupant_types={'monster':1})
            asyncio.get_running_loop().call_later(.15,stairs.update,dict(occupants=0,occupant_types={}))
        else:
            assert not stairs['occupants'] and ctrl.last_waypoint == 0
            gs.position=tuple(landing)
    state.game_proxy.inject_to_server=simulate_step
    asyncio.run(asyncio.wait_for(ctrl.run(),2))
    assert walked==[tuple(anchor),tuple(anchor)]
    assert ctrl.status=='complete' and ctrl.laps==1


@pytest.mark.parametrize('restore_preferred', [False, True])
def test_gotrunks_unknown_approach_takes_linked_stairs_and_preserves_progress(state,tmp_path,restore_preferred):
    anchor,approach,entry,landing=[1039,1300,2],[1038,1299,2],[1038,1300,2],[1037,1300,1]
    onward=[1037,1299,1]
    stairs=cell(*entry,kind='blocked',item=5259,navigation=[{'kind':'stairs'}])
    stairs['transitions']=[dict(id='up',exit=landing,action='step')]
    cells=[cell(*p) for p in (anchor,approach,landing,onward)]+[stairs]
    edge=dict(id='up',origin=approach,entry=entry,exit=landing,action='step',entry_items=[5259],evidence='observed')
    ctrl,_,_=setup(state,tmp_path,cells,[approach,landing,onward],[edge])
    atlas=state.passive_map_monitor.atlas
    draft=atlas.save_draft('WP66-67',[anchor,landing,onward],links=[dict(from_index=0,id='up')])
    state.game_state.position=tuple(anchor)
    ctrl.prepare(atlas.scope,draft,False)
    assert ctrl.path==[anchor,landing,onward]
    cells[1]['kind']='unknown'
    sample=state.passive_map_monitor.snapshot()
    stairs['transitions'].append(dict(id='other',exit=[1039,1300,1],action='step'))
    with pytest.raises(MovementDeferred, match='potwierdzenie'):
        asyncio.run(ctrl.transition(ctrl.edges[0],sample))
    stairs['transitions'].pop()
    stairs['occupants']=1
    with pytest.raises(MovementDeferred, match='zajęte'):
        asyncio.run(ctrl.transition(ctrl.edges[0],sample))
    stairs['occupants']=0
    # If the player takes this exact staircase manually, recovery credits the
    # landing, even while the cursor is still before the unreadable approach.
    ctrl.status='running'
    state.game_state.position=tuple(onward)
    with pytest.raises(ValueError,match='Recovery'):
        asyncio.run(ctrl.recover_position(state.passive_map_monitor.snapshot()))
    state.game_state.position=tuple(landing)
    with pytest.raises(ValueError,match='Recovery'):
        asyncio.run(ctrl.recover_position({**state.passive_map_monitor.snapshot(),'stale':True}))
    assert ctrl.index==0
    assert asyncio.run(ctrl.recover_position(state.passive_map_monitor.snapshot()))
    assert ctrl.index==1 and ctrl.last_waypoint==1 and state.game_proxy._inject_queue.empty()
    state.game_state.position=tuple(anchor)
    ctrl.prepare(atlas.scope,draft,False)
    visited=[]
    async def step(payload,guard):
        assert guard()
        current=list(state.game_state.position)
        visited.append(current)
        if restore_preferred and len(visited)==1:
            cancel_walk(state.game_state,3,time.perf_counter())
            cells[1]['kind']='clear'
            return
        dest=onward if current==landing else landing
        state.game_state.position=tuple(dest)
    state.game_proxy.inject_to_server=step
    asyncio.run(asyncio.wait_for(ctrl.run(),2.5))
    assert ctrl.status=='complete' and ctrl.last_waypoint==2
    assert visited==([anchor,anchor,landing] if restore_preferred else [anchor,landing])
    assert ctrl.path==[anchor,landing,onward]


@pytest.mark.parametrize("manual", [False, True])
def test_gotrunks_62_unreadable_waypoint_before_known_stairs(state, tmp_path, manual):
    start, waypoint = [1040,1299,4], [1039,1300,4]
    side, observed, stairs = [1039,1299,4], [1038,1299,4], [1038,1300,4]
    landing, onward = [1037,1300,3], [1038,1299,3]
    stair = cell(*stairs, kind='blocked', item=5259, navigation=[dict(kind='stairs')])
    edge = dict(id='up', origin=observed, entry=stairs, exit=landing, action='step',
                entry_items=[5259], evidence='observed')
    stair['transitions'] = [dict(id='up', exit=landing, action='step')]
    cells = [cell(*p) for p in (start, waypoint, side, observed, landing, onward)] + [stair]
    ctrl, _, _ = setup(state, tmp_path, cells, [observed, landing], [edge])
    atlas = state.passive_map_monitor.atlas
    draft = atlas.save_draft('Gotrunks 62', [start, waypoint, landing, onward], links=[dict(from_index=1,id='up')])
    gs = state.game_state
    gs.position = tuple(start)
    ctrl.prepare(atlas.scope, draft, False)
    assert ctrl.path == [start, side, landing, onward]
    cells[2]['kind'] = 'unknown'
    ctrl.status = 'running'
    if manual:
        gs.position = tuple(onward)
        with pytest.raises(ValueError, match='Recovery'):
            asyncio.run(ctrl.recover_position(ctrl.map_now()))
        gs.position = tuple(landing)
        assert asyncio.run(ctrl.recover_position(ctrl.map_now()))
        assert ctrl.path[ctrl.index] == landing and ctrl.last_waypoint == 2
        assert state.game_proxy._inject_queue.empty()
        return
    cells[1]['kind'] = 'blocked'
    assert not ctrl.reroute_floor_approach(ctrl.map_now(), blocked_step=True)
    cells[1]['kind'] = 'clear'
    stair['transitions'].append(dict(id='wrong', exit=onward, action='step'))
    assert not ctrl.reroute_floor_approach(ctrl.map_now(), blocked_step=True)
    stair['transitions'].pop()
    positions = []
    async def dispatch(payload, guard):
        assert guard()
        x, y, z = gs.position
        dx, dy = next(delta for delta, direction in OFFSETS.items() if int(WALK_OPCODES[direction]) == payload[0])
        requested = [x + dx, y + dy, z]
        positions.append(requested)
        if requested == stairs:
            assert gs.position == tuple(waypoint) and ctrl.last_waypoint == 1
        gs.position = tuple(landing if requested == stairs else requested)
        ctrl.last_command = 0
    state.game_proxy.inject_to_server = dispatch
    asyncio.run(asyncio.wait_for(ctrl.run(), 2))
    assert ctrl.status == 'complete' and ctrl.last_waypoint == 3
    assert positions == [waypoint, stairs, onward]


@pytest.mark.parametrize("case", ["queued_before_write", "old_origin", "ambiguous_retry",
                                  "manual_during_send", "transition", "already_arrived"])
def test_uncorrelated_cancel_does_not_abort_next_position_confirmation(state, tmp_path, case):
    ctrl, _, _ = setup(state, tmp_path)
    gs = state.game_state
    origin, target = [10, 10, 7], [11, 10, 7]

    async def scenario():
        ctrl.status = "running"
        ctrl._route_command = True
        dispatch = time.perf_counter()
        ctrl._route_attempt = dict(valid=True, checks=2, sent=time.monotonic(), motion=ctrl.motion_generation,
                                   walk_checked_at=dispatch)
        if case == "ambiguous_retry":
            assert not await ctrl._confirm_walk(target, origin, timeout=0, recover=True)
        if case == "old_origin":
            gs.position = (9, 10, 7)
        received = dispatch - .05 if case == "queued_before_write" else dispatch + .001
        cancel_walk(gs, 3, received)
        gs.position = tuple(origin)
        if case == "manual_during_send":
            ctrl.motion_generation += 1
        if case == "already_arrived":
            gs.position = tuple(target)
        else:
            asyncio.get_running_loop().call_later(.02, setattr, gs, "position", tuple(target))
        assert await ctrl._confirm_walk(target, origin, timeout=.5, recover=case != "transition")
        assert not ctrl._route_attempt.get("cancelled")

    asyncio.run(scenario())

@pytest.mark.parametrize("manual", [False, True], ids=["correction", "manual"])
def test_displacement_keeps_target_walkable_and_rejoins_once(state, tmp_path, manual):
    origin, end = [1055,1281,7], [1055,1284,7]
    tiles = [cell(x,y) for x in range(1054,1058) for y in range(1280,1286)]
    ctrl, _, _ = setup(state, tmp_path, tiles, [origin, end])
    gs = state.game_state
    outcomes = []
    ctrl._observe_report = lambda method,*args: outcomes.append(args) if method == "interrupted" else None
    sent = []
    step = ctrl.step

    async def dispatch(payload, guard):
        assert guard()
        dx,dy = next(delta for delta,d in OFFSETS.items() if int(WALK_OPCODES[d]) == payload[0])
        x,y,z = gs.position
        target = (x+dx,y+dy,z)
        sent.append(target)
        if len(sent) == 1:
            loop = asyncio.get_running_loop()
            def displace():
                if manual:
                    ctrl.motion_generation += 1
                    ctrl.manual_until = time.monotonic() + .02
                gs.position = (1056,1282,7)
            loop.call_later(.01, displace)
        else:
            gs.position = target
        ctrl.last_command = 0

    results = []
    async def observe(*args, **kwargs):
        result = await step(*args, **kwargs)
        results.append(result)
        return result

    ctrl.step = observe
    state.game_proxy.inject_to_server = dispatch
    asyncio.run(asyncio.wait_for(ctrl.run(), 3))
    assert results[0] is (WalkOutcome.MANUAL if manual else WalkOutcome.DISPLACED)
    assert all(result is WalkOutcome.ARRIVED for result in results[1:])
    assert not ctrl.blocked and not ctrl._failed_exits
    assert tuple(origin) not in sent[1:] and gs.position == tuple(end)
    assert ctrl.status == 'complete' and ctrl.laps == 1
    assert outcomes[0][1] == 'position_interrupted' and outcomes[0][2]


def test_lost_confirmation_skips_only_first_hop_and_can_enter_same_tile(state, tmp_path):
    origin, target = [10,10,7], [11,10,7]
    cells = [cell(*p) for p in (origin, target, [10,11,7])]
    ctrl, _, _ = setup(state, tmp_path, cells, [origin,target])
    gs = state.game_state
    sent = []
    real_wait = ctrl.wait_position

    async def wait(target, origin, timeout, intermediate, recover):
        return await real_wait(target, origin, .035, intermediate, recover)

    async def dispatch(payload, guard):
        assert guard()
        dx,dy = next(delta for delta,d in OFFSETS.items() if int(WALK_OPCODES[d]) == payload[0])
        x,y,z = gs.position
        destination = (x+dx,y+dy,z)
        sent.append(destination)
        if len(sent)>1:
            gs.position = destination
        ctrl.last_command = 0

    ctrl.wait_position = wait
    state.game_proxy.inject_to_server = dispatch
    asyncio.run(asyncio.wait_for(ctrl.run(), 1))
    assert sent == [tuple(target),(10,11,7),tuple(target)]
    assert ctrl.status == 'complete' and ctrl.laps == 1 and not ctrl.blocked


@pytest.mark.parametrize("origin,anchor,entry,exit_tile,side,kind,item", [
    ([1037,1299,3], [1038,1298,3], [1039,1298,3], [1039,1297,2], [1038,1299,3], 'stairs', 5258),
    ([1039,1300,6], [1039,1299,6], [1039,1298,6], [1039,1299,7], [1038,1299,6], 'passage', 413),
])
def test_gotrunks_unreadable_link_origin_keeps_exact_entry_and_landing(
        state, tmp_path, origin, anchor, entry, exit_tile, side, kind, item):
    stair = cell(*entry, kind='blocked', item=item, navigation=[dict(kind=kind)])
    stair['transitions'] = [dict(id='recorded', action='step', exit=exit_tile)]
    adjacent = [entry[0]+1, entry[1], entry[2]]
    other = cell(*adjacent, kind='blocked', item=item, navigation=[dict(kind=kind)])
    other['transitions'] = [dict(id='other', action='step', exit=[exit_tile[0]+1,*exit_tile[1:]])]
    cells = [cell(*p) for p in (origin,anchor,side,exit_tile)] + [stair,other]
    edge = dict(id='recorded', origin=anchor, entry=entry, exit=exit_tile, action='step',
                entry_items=[item], evidence='observed')
    ctrl, _, _ = setup(state,tmp_path,cells,[origin,anchor,exit_tile],[edge])
    planned = ctrl.path[1]
    backup = side if planned == anchor else anchor
    cells_by_pos = {(c['x'],c['y'],c['z']):c for c in cells}
    cells_by_pos[tuple(planned)].update(occupants=1)
    sample = ctrl.map_now()
    stair['occupants'] = 1
    assert not ctrl.reroute_floor_approach(sample, blocked_step=True)
    stair['occupants'] = 0
    stair['transitions'].append(dict(id='wrong', action='step', exit=origin))
    assert not ctrl.reroute_floor_approach(sample, blocked_step=True)
    stair['transitions'].pop()
    cells_by_pos[tuple(backup)]['kind'] = 'blocked'
    assert not ctrl.reroute_floor_approach(sample, blocked_step=True)
    cells_by_pos[tuple(backup)]['kind'] = 'clear'
    visited = []
    async def dispatch(payload, guard):
        assert guard()
        x,y,z = state.game_state.position
        dx,dy = next(d for d, direction in OFFSETS.items() if int(WALK_OPCODES[direction]) == payload[0])
        requested = [x+dx,y+dy,z]
        visited.append(requested)
        state.game_state.position = tuple(exit_tile if requested == entry else requested)
        state.game_state.stats_updated_at = time.time()
        ctrl.last_command = 0
    state.game_proxy.inject_to_server = dispatch
    asyncio.run(asyncio.wait_for(ctrl.run(), 2))
    assert visited == [backup,entry]
    assert ctrl.status == 'complete' and ctrl.last_waypoint == 2


@pytest.mark.parametrize("first_bypass", ["clear", "occupied", "action_tile", "missing_sample"])
def test_shenlong_289_rejoins_stair_approach_after_two_map_samples(state, tmp_path, first_bypass):
    start, marker, around = [1184,556,4], [1185,556,4], [1185,555,4]
    approach, entry, landing = [1186,555,4], [1187,554,4], [1187,553,3]
    stair = cell(*entry, kind="blocked", item=1977, navigation=[dict(kind="stairs")])
    edge = dict(id="shenlong", origin=approach, entry=entry, exit=landing,
                action="step", entry_items=[1977], evidence="observed")
    cells = [cell(*p) for p in (start, marker, around, approach, landing)] + [stair]
    ctrl, _, _ = setup(state, tmp_path, cells, [start, marker, approach, landing], [edge])
    assert ctrl.path == [start, marker, approach, landing]
    cells[1].update(kind="unknown", items=[], reason="Pole zmieniło położenie podczas odczytu.")
    if first_bypass == "occupied":
        cells[2].update(occupants=1, occupant_types={"player":1})
    elif first_bypass == "action_tile":
        cells[2]["navigation"] = [dict(kind="ladder")]

    ctrl.status = "running"
    snapshot = state.passive_map_monitor.snapshot
    sample = dict(snapshot(), captured_at=time.time())
    assert not ctrl.tolerate_waypoint(sample)
    ctrl.describe_blockage(sample, marker)
    assert not ctrl.tolerate_waypoint(sample)  # Re-reading the cached snapshot is insufficient.
    if first_bypass == "missing_sample":
        assert not ctrl.tolerate_waypoint(snapshot())
    elif first_bypass != "clear":
        assert not ctrl.tolerate_waypoint(dict(snapshot(), captured_at=time.time()+.01))
        assert ctrl.path == [start, marker, approach, landing]
        cells[2].update(occupants=0, occupant_types={}, navigation=[])
    state.passive_map_monitor.snapshot = lambda: dict(snapshot(), captured_at=time.time())
    moves = []

    async def dispatch(payload, guard):
        assert guard()
        pos = state.game_state.position
        dx, dy = next(delta for delta, direction in OFFSETS.items() if int(WALK_OPCODES[direction]) == payload[0])
        dest = [pos[0]+dx, pos[1]+dy, pos[2]]
        moves.append(dest)
        if dest == entry:
            assert list(pos) == approach and ctrl.last_waypoint == 2
        state.game_state.position = tuple(landing if dest == entry else dest)
        state.game_state.stats_updated_at = time.time()
        ctrl.last_command = 0

    state.game_proxy.inject_to_server = dispatch
    asyncio.run(asyncio.wait_for(ctrl.run(), 3))
    assert moves == [around, approach, entry]
    assert ctrl.tolerated_waypoints == 1 and ctrl.status == "complete"
    assert state.game_state.position == tuple(landing) and ctrl.last_waypoint == 3
