"""Ordinary-step recovery and per-creature first-sighting limits, without a game session."""
import asyncio

import pytest

from adaptive_cavebot import WalkOutcome
from cavebot_combat import validate_combat
from cavebot_walking import detour
from test_adaptive_cavebot import cell, setup, connect_fake_walk
from test_automation_safety import state
from test_v2_combat import armed, monster


def test_two_failed_departures_try_left_and_rejoin_at_waypoint_90(state,tmp_path):
    origin = [1869,465,9]
    north, right, left = ([x,464,9] for x in (1869,1870,1868))
    goal = [1869,460,9]
    cells = [cell(*p) for p in [origin,left,right]+[[1869,y,9] for y in range(460,465)]]
    c, _, _ = setup(state,tmp_path,cells,[origin,goal])
    sent = connect_fake_walk(state,c)
    arrived = []
    step = c.step

    async def recorded_step(target, *args, **kwargs):
        result = await step(target,*args,**kwargs)
        arrived.append(target)
        return result

    async def run():
        c.status = 'running'
        await c.handle_walk_failure(WalkOutcome.CANCELLED, origin, north)
        # Timeout on the next exit took 2.5 s. The original tile block has expired.
        c._failed_exits[tuple(north)] -= 2.5
        c.blocked[tuple(north)] -= 2.5
        await c.handle_walk_failure(WalkOutcome.TIMEOUT, origin, right)
        c.expire_movement_blocks()
        assert not c.blocked
        c.step = recorded_step
        await asyncio.wait_for(c.run(), 5)

    asyncio.run(run())
    assert sent and arrived[:2] == [left,[1869,463,9]]
    assert list(state.game_state.position) == goal and c.status=='complete'
    assert not c.delayed_departures(goal)


def test_all_exits_transiently_unavailable_do_not_block_other_origins(state,tmp_path):
    c, cells, _ = setup(state,tmp_path)
    start, a, b = [10,10,7], [11,10,7], [10,11,7]
    cells[:] = [cell(*p) for p in (start,a,b)]
    for point in (a,b):
        asyncio.run(c.handle_walk_failure(WalkOutcome.TIMEOUT,start,point))
    assert not detour(cells,start,a,skip_first=c.delayed_departures(start))
    for point in list(c._failed_exits):
        c._failed_exits[point] -= 9
    assert detour(cells,start,a,skip_first=c.delayed_departures(start)) == [start,a]
    asyncio.run(c.handle_walk_failure(WalkOutcome.TIMEOUT,start,a))
    assert not c.delayed_departures(b) and not c.blocked


def test_legacy_lure_limit_and_validation():
    assert validate_combat({})['lure_origin_limit']==12
    for value in (0,65,1.5,True):
        with pytest.raises(ValueError):
            validate_combat({'lure_origin_limit':value})


def test_first_sighting_precedes_attack_radius_and_survives_target_reset(state,tmp_path):
    c, cells = armed(state,tmp_path,mode='lure',radius=1,lure_origin_limit=2)
    c.path = [[10,10,7],[10,11,7]]
    cid,row,tile = monster(state,cells,x=13,y=10)
    assert not c.combat.candidates()
    assert c.combat.first_sightings[cid][0] == (13,10,7)
    c.combat.reset_lure()
    tile.update(occupants=0,occupant_types={})
    monster(state,cells,x=11,y=10,cid=cid)
    assert not c.combat.route_step_allowed() and c.combat.lure_stop_reason=='origin_limit'
    state.game_state.creatures[cid]['health']=0
    c.combat.candidates()
    assert cid not in c.combat.first_sightings


@pytest.mark.parametrize('recruit,reason', [(False,'clear_pack'),(True,None)])
def test_open_ground_with_partial_pack_waits_only_without_recruits(state,tmp_path,recruit,reason):
    c,cells=armed(state,tmp_path,mode='lure',engage='near',radius=2,lure_count=8,lure_origin_limit=6)
    cells[:]=[cell(x,y) for x in range(3,18) for y in range(6,15)]
    c.path=[[x,10,7] for x in range(10,17)]
    for cid,y in [(101,10),(102,11)]:
        monster(state,cells,x=5,y=y,cid=cid)
    c.combat.candidates()
    for cid,y in [(101,10),(102,11)]:
        next(t for t in cells if (t['x'],t['y'])==(5,y)).update(occupants=0,occupant_types={})
        monster(state,cells,x=9,y=y,cid=cid)
    if recruit:
        monster(state,cells,x=15,y=10,cid=103)
    targets=c.combat.candidates()
    assert {m['id'] for m in targets}=={101,102}
    assert c.combat.gather_stop_reason(c.map_now(),targets,{101,102},False)==reason


def test_movement_guard_uses_actual_detour_and_only_engaged_mobs(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure',engage='visible',lure_origin_limit=3)
    cells[:]=[cell(x,y) for x in range(5,17) for y in range(8,13)]
    cid,row,tile=monster(state,cells,x=6,y=10)
    c.combat.candidates()
    c.path=[[10,10,7],[10,11,7],[11,11,7]]
    # A wild monster that has not joined the pack must not veto the step.
    assert c.combat.route_step_allowed(destination=[11,10,7])
    tile.update(occupants=0,occupant_types={})
    monster(state,cells,x=8,y=10,cid=cid)
    c.combat.pursuit.following.add(cid)
    assert c.combat.route_step_allowed(destination=[10,11,7])
    assert not c.combat.route_step_allowed(destination=[11,10,7])
    assert c.combat.lure_stop_reason=='origin_limit'
