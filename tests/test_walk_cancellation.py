import asyncio
import logging
import time

import pytest

from game_state import scan_packet
from protocol import ServerOpcode
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


def test_stair_cancel_retries_from_observed_side_after_entrance_clears(state,tmp_path):
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
        if gs.position==tuple(anchor):
            gs.position=tuple(approach)
        elif len(walked)==2:
            cancel_walk(gs,2,time.perf_counter())
            stairs.update(occupants=1,occupant_types={'monster':1})
            asyncio.get_running_loop().call_later(.15,stairs.update,dict(occupants=0,occupant_types={}))
        else:
            assert not stairs['occupants'] and gs.position==tuple(approach)
            gs.position=tuple(landing)
    state.game_proxy.inject_to_server=simulate_step
    asyncio.run(asyncio.wait_for(ctrl.run(),2))
    assert walked==[tuple(anchor),tuple(approach),tuple(approach)]
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
    assert ctrl.path==[anchor,approach,landing,onward]
    cells[1]['kind']='unknown'
    sample=state.passive_map_monitor.snapshot()
    stairs['transitions'].append(dict(id='other',exit=[1039,1300,1],action='step'))
    assert not ctrl.use_stairs_from_saved_waypoint(sample)
    stairs['transitions'].pop()
    stairs['occupants']=1
    assert not ctrl.use_stairs_from_saved_waypoint(sample)
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
    assert ctrl.index==2 and ctrl.last_waypoint==1 and state.game_proxy._inject_queue.empty()
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
        dest=onward if current==landing else approach if current==anchor and restore_preferred else landing
        state.game_state.position=tuple(dest)
    state.game_proxy.inject_to_server=step
    asyncio.run(asyncio.wait_for(ctrl.run(),2.5))
    assert ctrl.status=='complete' and ctrl.last_waypoint==2
    assert visited==([anchor,anchor,approach,landing] if restore_preferred else [anchor,landing])
    assert ctrl.path[-1]==onward and (approach in ctrl.path)==restore_preferred


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
