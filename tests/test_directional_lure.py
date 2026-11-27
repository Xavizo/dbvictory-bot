"""Route pacing regressions, without any game connection or native calls."""
import asyncio
from unittest.mock import Mock

import pytest
from dbv.brain.cavebot.cavebot_lure import route_approaches
from dbv.client_bridge.protocol import ClientOpcode
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
    from dbv.brain.cavebot import cavebot_combat as mod
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
    from dbv.brain.cavebot.cavebot_lure import group_needs_time
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


@pytest.fixture
def combat_clock(monkeypatch):
    import time
    from types import SimpleNamespace
    from dbv.brain.cavebot import cavebot_combat
    clock = Mock(return_value=100.)
    monkeypatch.setattr(cavebot_combat, 'time', SimpleNamespace(monotonic=clock, time=time.time))
    return clock


@pytest.mark.parametrize('boundary', [False, True], ids=['gathering', 'already-fighting'])
def test_lure_closes_on_mob_beside_player_and_rejoins_route(state, tmp_path, combat_clock, boundary):
    from test_adaptive_cavebot import connect_fake_walk
    from dbv.brain.cavebot.cavebot_walking import distance
    from dbv.client_bridge.protocol import WALK_OPCODES

    c, cells, sent = scene(state, tmp_path)
    cid, mob, tile = monster(state, cells, x=6, y=10)
    player = (7, 10, 7)
    next(t for t in cells if (t['x'], t['y'], t['z']) == player).update(occupants=1, occupant_types={'player': 1})
    anchor = state.game_state.position
    attack = state.game_proxy.inject_to_server
    walked = connect_fake_walk(state, c)
    walk = state.game_proxy.inject_to_server

    async def dispatch(payload, guard=None):
        if payload[0] in WALK_OPCODES.values():
            await walk(payload, guard)
            assert state.game_state.position != player
        else:
            await attack(payload, guard)

    state.game_proxy.inject_to_server = dispatch
    async def exercise():
        for second in range(3):
            combat_clock.return_value = 100. + second
            mob['health'] -= 1  # Ranged damage is not evidence that the monster is following.
            await c.combat.tick(c.map_now(), at_transition=boundary)
            assert state.game_state.position == anchor
        for second in range(3, 6):
            combat_clock.return_value = 100. + second
            assert await c.combat.tick(c.map_now(), at_transition=boundary)
            assert c.combat.phase == 'chase' and c.combat.anchor == list(anchor)
            assert c.index == 0 and c.combat.sent_target == cid
        assert len(walked) == 3 and distance(state.game_state.position, (6, 10, 7)) == 1
        assert await c.combat.tick(c.map_now(), at_transition=boundary)
        assert c.combat.phase == 'fight' and len(walked) == 3
        assert c.combat.lure_stop_reason == ('transition' if boundary else 'no_approach')
        mob['health'] = 0
        tile.update(occupants=0, occupant_types={})
        for _ in range(8):
            combat_clock.return_value += .5
            if not await c.combat.tick(c.map_now()):
                break
        assert c.combat.phase == 'route' and c.combat.anchor is None
        assert state.game_state.position == anchor

    asyncio.run(exercise())
    assert sent[0][0] == ClientOpcode.ATTACK


def test_slowly_approaching_mob_keeps_its_grace_at_lure_stop(state, tmp_path, combat_clock):
    c, cells, _ = scene(state, tmp_path)
    cid, row, tile = monster(state, cells, x=16, y=11)
    c.combat.defer_movement = True
    for second in range(7):
        combat_clock.return_value = 100. + second
        if second in (2, 4):
            tile.update(occupants=0, occupant_types={})
            cid, row, tile = monster(state, cells, x=row['x']-1, y=11, cid=cid)
        assert asyncio.run(c.combat.tick(c.map_now(), at_transition=True))
        assert c.combat.phase == 'fight' and c.combat.movement_intent is None


@pytest.mark.parametrize('change', ['target-moved', 'player-in-way', 'target-gone', 'manual-control'])
def test_queued_lure_approach_rechecks_the_world(state, tmp_path, combat_clock, change):
    c, cells, _ = scene(state, tmp_path)
    cid, row, tile = monster(state, cells, x=6, y=10)
    c.combat.defer_movement = True
    for second in range(4):
        combat_clock.return_value = 100. + second
        asyncio.run(c.combat.tick(c.map_now(), at_transition=True))
    intent = c.combat.movement_intent
    assert intent and intent['validate']()
    if change == 'target-moved':
        tile.update(occupants=0, occupant_types={})
        monster(state, cells, x=7, y=10, cid=cid)
    elif change == 'player-in-way':
        step = next(t for t in cells if [t['x'], t['y'], t['z']] == intent['target'])
        step.update(occupants=1, occupant_types={'player': 1})
    elif change == 'target-gone':
        row['health'] = 0
    else:
        c.combat.accept_manual_target(cid)
    assert not intent['validate']()


def test_unresponsive_mob_across_occupied_or_protected_passage_has_no_melee_step():
    from dbv.brain.cavebot.cavebot_lure import lure_melee_step
    cells = [cell(x, 10) for x in range(5, 11)]
    origin, target = (10, 10, 7), (5, 10, 7)
    gate = cells[3]
    def step(edges=(), blocked=(), radius=5):
        return lure_melee_step(cells, origin, target, edges, blocked, origin, radius)
    assert step() == (9, 10, 7)
    assert step(radius=2) is None
    assert step(blocked={(8, 10, 7)}) is None
    assert step(edges=[dict(origin=(8,10,7), entry=(7,10,7), exit=(6,10,7))]) is None
    gate.update(occupants=1, occupant_types={'player': 1})
    assert step() is None
    gate.update(occupants=0, occupant_types={}, navigation=[dict(kind='door', door_state_hint='open')])
    assert step() is None


def test_route_closing_on_a_stationary_recruit_keeps_gathering(state, tmp_path, combat_clock):
    c, cells, _ = scene(state, tmp_path)
    monster(state, cells, x=17, y=12)
    c.combat.defer_movement = True
    for second in range(6):
        combat_clock.return_value = 100. + second
        c.index = second // 2
        state.game_state.position = tuple(c.path[c.index])
        assert not asyncio.run(c.combat.tick(c.map_now()))
        assert c.combat.phase == 'lure' and c.combat.movement_intent is None


def test_lure_approach_waits_for_movement_permission_and_fresh_identity(state, tmp_path, combat_clock):
    c, cells, _ = scene(state, tmp_path)
    cid, row, _ = monster(state, cells, x=6, y=10)
    c.combat.defer_movement = True
    for second in range(5):
        combat_clock.return_value = 100. + second
        assert asyncio.run(c.combat.tick(c.map_now(), at_transition=True, allow_movement=False))
        assert c.combat.movement_intent is None
    state.game_state.creatures.pop(cid)
    asyncio.run(c.combat.tick(c.map_now(), at_transition=True))
    state.game_state.creatures[cid] = row
    for second in range(5, 8):
        combat_clock.return_value = 100. + second
        assert asyncio.run(c.combat.tick(c.map_now(), at_transition=True))
        assert c.combat.movement_intent is None
    combat_clock.return_value = 108.
    assert asyncio.run(c.combat.tick(c.map_now(), at_transition=True))
    assert c.combat.movement_intent['validate']()
