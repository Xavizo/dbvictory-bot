"""Regression: stale fight anchors must not trap an already rejoined route."""
import asyncio
import time
import pytest
from test_automation_safety import state
from test_v2_combat import armed,monster


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
