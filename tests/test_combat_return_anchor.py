"""Regression: stale fight anchors must not trap an already rejoined route."""
import asyncio
import time
import pytest
from test_automation_safety import state
from test_v2_combat import armed,monster
from test_adaptive_cavebot import connect_fake_walk
from test_automation_domains import until


@pytest.mark.parametrize('anchor', [[10,10,7],[50,50,7],[10,10,8]])
def test_rejoined_route_drops_obsolete_fight_anchor_without_walking_back(state,tmp_path,anchor):
    c,cells=armed(state,tmp_path,mode='lure');c.status='running'
    c.index=2;state.game_state.position=tuple(c.path[c.index])
    c.combat.anchor=anchor;c.combat.battle_started=time.monotonic()-10
    c.combat.group_clear_since=time.monotonic()-1
    next(v for v in cells if (v['x'],v['y'])==(10,10)).update(kind='blocked',items=[dict(id=1234,flags=['not_walkable'])])
    assert not asyncio.run(c.combat.tick(state.passive_map_monitor.snapshot()))
    assert c.combat.anchor is None and c.combat.phase=='route' and c.status=='running'
    assert state.game_proxy._inject_queue.empty()
    assert state.game_state.position==tuple(c.path[c.index])


def test_manual_movement_grace_does_not_latch_lure_as_stationary_combat(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure',lure_count=3);c.status='running'
    monster(state,cells,x=11,y=11)
    async def scenario():
        assert await c.combat.tick(state.passive_map_monitor.snapshot(),allow_movement=False)
        assert c.combat.battle_started is None and c.combat.anchor is None
        assert c.combat.lure_stop_reason is None
        assert not await c.combat.tick(state.passive_map_monitor.snapshot())
        assert c.combat.phase=='lure' and c.status=='running'
    asyncio.run(scenario())


def test_route_workers_pass_occupied_return_anchor_after_fight(state, tmp_path):
    controller, cells = armed(state, tmp_path, mode='lure')
    anchor = controller.path[0]
    destination = controller.path[-1]
    monster(state, cells, name='Ignored monster', x=anchor[0], y=anchor[1])
    moves = connect_fake_walk(state, controller)

    async def scenario():
        run = asyncio.create_task(controller.run())
        try:
            await asyncio.sleep(0)
            # Two tiles from the old fight position, with the route still ahead.
            state.game_state.position = (10, 12, 7)
            controller.combat.anchor = anchor
            await until(lambda: controller.domains.movement_state == 'complete', timeout=3)
            assert list(state.game_state.position) == destination
            assert moves and controller.recovery_count == 1
            assert controller.combat.anchor is None and controller.status == 'running'
            assert controller.route_draft['points'] == [anchor, destination]
        finally:
            controller.pause('end test')
            await asyncio.wait_for(run, 1)

    asyncio.run(scenario())


def test_blocked_transition_approach_still_requires_reaching_its_entrance(state, tmp_path):
    controller, cells = armed(state, tmp_path, mode='chase')
    entrance = controller.path[0]
    controller.edges = [dict(origin=entrance, entry=entrance, exit=controller.path[1])]
    controller.combat.anchor = entrance
    controller.status = 'running'
    state.game_state.position = (10, 12, 7)
    monster(state, cells, name='Ignored monster', x=entrance[0], y=entrance[1])
    initial_path = controller.path.copy()
    assert not asyncio.run(controller.combat.tick(controller.map_now()))
    assert not controller.recover_local(controller.map_now())
    assert controller.path == initial_path and controller.index == 0
    assert state.game_proxy._inject_queue.empty()
