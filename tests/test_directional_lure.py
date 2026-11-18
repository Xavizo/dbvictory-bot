"""Route pacing regressions, without any game connection or native calls."""
import asyncio
from unittest.mock import Mock

import pytest
from cavebot_lure import route_approaches
from protocol import ClientOpcode
from test_automation_safety import state
from test_adaptive_cavebot import cell
from test_v2_combat import armed,monster


def scene(state,tmp_path,**kwargs):
    c,cells=armed(state,tmp_path,mode='lure',engage='visible',lure_count=3,lure_distance=4,**kwargs)
    cells[:]=[cell(x,y) for x in range(4,19) for y in range(7,14)]
    c.path=[[x,10,7] for x in range(10,18)];c.index=0;c.status='running'
    sent=[]
    async def inject(payload,guard=None):
        assert guard();sent.append(payload);c.last_command=0
    state.game_proxy.inject_to_server=inject
    return c,cells,sent


def test_visible_monsters_ahead_do_not_stop_approach_or_start_gather_timer(state,tmp_path,monkeypatch):
    c,cells,sent=scene(state,tmp_path)
    monster(state,cells,x=16,y=10)
    monster(state,cells,x=17,y=11,cid=0x40000002)
    tick_clock=Mock(return_value=100.)
    # Replace only the combat module's clock, leaving asyncio's clock alone.
    import cavebot_combat as mod
    real_time=mod.time
    monkeypatch.setattr(mod,'time',type('Clock',(),{'monotonic':tick_clock,'time':staticmethod(real_time.time)})())
    async def exercise():
        assert not await c.combat.tick(c.map_now())
        assert c.combat.lure_started is None and not c.combat.lure_waiting
        tick_clock.return_value=120.
        assert not await c.combat.tick(c.map_now())
        assert c.combat.lure_stop_reason is None and 'podchodzę trasą' in c.reason
    asyncio.run(exercise())
    assert sent and sent[0][0]==ClientOpcode.ATTACK


@pytest.mark.parametrize('confirmed',[False,True])
def test_monster_ahead_can_be_approached_even_if_previously_following(state,tmp_path,confirmed):
    c,cells,_=scene(state,tmp_path)
    monster(state,cells,x=16,y=11)
    if confirmed:
        targets=c.combat.candidates();c.combat.pursuit.update=lambda *args:targets
    assert not asyncio.run(c.combat.tick(c.map_now()))
    assert not c.combat.lure_waiting and c.combat.phase=='lure'


def test_confirmed_follower_behind_waits_and_keeps_casting(state,tmp_path):
    c,cells,sent=scene(state,tmp_path,spells=[dict(words='Shinretsuha',ki=0,range=7,cooldown=1)])
    state.game_state.mana=state.game_state.max_mana=100
    monster(state,cells,x=6,y=10)
    targets=c.combat.candidates();c.combat.pursuit.update=lambda *args:targets
    assert asyncio.run(c.combat.tick(c.map_now()))
    assert c.combat.lure_waiting and 'z tyłu' in c.reason and c.status=='running'
    assert [p[0] for p in sent]==[ClientOpcode.ATTACK,ClientOpcode.SAY]


def test_unconfirmed_distant_monster_behind_does_not_freeze_the_route(state,tmp_path):
    c,cells,_=scene(state,tmp_path);monster(state,cells,x=6,y=10)
    assert not asyncio.run(c.combat.tick(c.map_now()))
    assert c.combat.lure_started is None


def test_first_contact_waits_before_outrunning_slow_monster_and_releases(state,tmp_path):
    c,cells,sent = scene(state,tmp_path)
    cid, row, old = monster(state,cells,x=9,y=10)
    async def exercise():
        assert not await c.combat.tick(c.map_now())
        for index in (1,2,3):
            c.index = index
            state.game_state.position = tuple(c.path[index])
            held = await c.combat.tick(c.map_now())
            assert held == (index == 3)
        assert c.combat.lure_waiting and not c.combat.pursuit.following
        assert not c.combat.route_step_allowed() and c.combat.sent_target == cid
        old.update(occupants=0,occupant_types={})
        row['x'] = 10
        next(t for t in cells if (t['x'],t['y'])==(10,10)).update(occupants=1,occupant_types={'monster':1})
        assert not await c.combat.tick(c.map_now())
        assert c.combat.route_step_allowed() and sent
    asyncio.run(exercise())


def test_pacing_uses_walkable_distance_around_a_wall():
    from cavebot_lure import group_needs_time
    cells = [cell(x,y) for x in range(5) for y in range(5) if x != 1 or y == 0]
    assert group_needs_time(cells,(2,3,7),(2,4,7),{(0,3,7)},4)
    assert not group_needs_time(cells,(2,3,7),(2,2,7),{(0,3,7)},4)
    cells = [c for c in cells if (c['x'],c['y']) != (1,0)]
    assert not group_needs_time(cells,(2,3,7),(2,4,7),{(0,3,7)},4)


def test_wait_rechecks_position_after_awaited_attack(state,tmp_path):
    c,cells,sent=scene(state,tmp_path)
    _,row,tile=monster(state,cells,x=6,y=10)
    targets=c.combat.candidates();c.combat.pursuit.update=lambda *args:targets
    inject=state.game_proxy.inject_to_server
    async def moved(payload,guard=None):
        await inject(payload,guard)
        tile.update(occupants=0,occupant_types={});row['x']=16
        next(t for t in cells if (t['x'],t['y'])==(16,10)).update(occupants=1,occupant_types={'monster':1})
    state.game_proxy.inject_to_server=moved
    assert not asyncio.run(c.combat.tick(c.map_now()))
    assert not c.combat.lure_waiting


def test_nearby_group_still_stops_at_capacity_despite_targets_ahead(state,tmp_path):
    c,cells,_=scene(state,tmp_path)
    for i,(x,y) in enumerate(((10,11),(11,11),(11,10))):
        monster(state,cells,x=x,y=y,cid=0x40000001+i)
    assert asyncio.run(c.combat.tick(c.map_now()))
    assert c.combat.lure_stop_reason=='group'


def test_timer_starts_only_after_first_adjacent_monster(state,tmp_path):
    c,cells,_=scene(state,tmp_path)
    _,row,tile=monster(state,cells,x=16,y=11)
    async def exercise():
        assert not await c.combat.tick(c.map_now());assert c.combat.lure_started is None
        tile.update(occupants=0,occupant_types={});row['x']=11
        next(t for t in cells if (t['x'],t['y'])==(11,11)).update(occupants=1,occupant_types={'monster':1})
        assert not await c.combat.tick(c.map_now());assert c.combat.lure_started is not None
    asyncio.run(exercise())


def test_route_lookahead_handles_bends_but_never_crosses_floor_or_object_edge():
    p=[10,10,7];monster=[14,10,7]
    path=[p,[10,11,7],[11,11,7],[12,11,7]]
    assert route_approaches(p,monster,path,0)
    assert not route_approaches(p,monster,path,0,[dict(origin=p,exit=path[1])])
    assert not route_approaches(p,monster,[p,[11,10,8],[14,10,7]],0)
    assert not route_approaches([9,10,7],monster,path,0)
    assert not route_approaches(p,[6,10,7],path,0)


def test_room_exit_still_finishes_small_group(state,tmp_path):
    c,cells,_=scene(state,tmp_path);monster(state,cells,x=16,y=11)
    assert asyncio.run(c.combat.tick(c.map_now(),at_transition=True))
    assert c.combat.lure_stop_reason=='transition'
