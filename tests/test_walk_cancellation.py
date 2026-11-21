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
