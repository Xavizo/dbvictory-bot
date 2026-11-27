"""Local terrain and controller fixtures; no running game required."""
import asyncio
import pytest

from dbv.brain.cavebot.cavebot_lure import lure_stopping_path
from test_adaptive_cavebot import cell, setup, connect_fake_walk
from test_automation_safety import state
from test_v2_combat import armed, monster


def chamber():
    corridor = [cell(x, 10) for x in range(6, 11)]
    room = [cell(x, y) for x in range(11, 16) for y in range(8, 13)]
    return corridor + room


ROUTE = [[x, 10, 7] for x in range(10, 16)]
MONSTERS = [(8, 10, 7), (9, 10, 7)]


def side_room():
    return ([cell(x, 10) for x in range(5, 17)]
            + [cell(x, y) for x in range(8, 12) for y in range(6, 10)])


def test_open_side_room_beats_forward_corridor_without_stretching_group():
    for lead in (2, 3):
        assert lure_stopping_path(side_room(), ROUTE, [], MONSTERS, [], lead) == (
            (10, 10, 7), (10, 9, 7))


def test_side_step_rechecks_live_tile_and_returns_to_route_after_fight(state, tmp_path):
    c, cells = armed(state, tmp_path, mode="lure", lure_count=2, engage="visible")
    cells[:] = side_room()
    c.path = [list(point) for point in ROUTE]
    c.status = 'running'
    follower, _, _ = monster(state, cells, x=8, y=10)
    monster(state, cells, x=9, y=10, cid=follower+1)
    c.combat.pursuit.update = lambda pos, rows, now: [r for r in rows if r['id']==follower]
    c.combat.defer_movement = True

    async def scenario():
        assert await c.combat.tick(c.map_now())
        intent = c.combat.movement_intent
        assert c.combat.phase == 'positioning' and c.combat.sent_target
        assert not c.combat.route_step_allowed() and intent['target'] == [10,9,7]
        tile = next(t for t in cells if (t['x'],t['y'])==(10,9))
        tile['kind'] = 'blocked'
        assert not intent['validate']()
        tile['kind'] = 'clear'
        moves = connect_fake_walk(state,c)
        assert await c.step(intent['target'],validate=intent['validate'])
        assert state.game_state.position == (10,9,7) and c.index == 0
        assert await c.combat.tick(c.map_now()) and c.combat.phase == 'fight'
        assert c.combat.anchor == ROUTE[0]
        state.game_state.creatures.clear()
        for tile in cells:
            tile.update(occupants=0,occupant_types={})
        assert await c.combat.tick(c.map_now())
        c.combat.group_clear_since -= 1
        # The synthetic walker only accepts movement. Clear the completed selection.
        c.combat.sent_target = 0
        assert await c.combat.tick(c.map_now()) and c.combat.phase == 'return'
        assert await c.step(c.combat.movement_intent['target'])
        assert not await c.combat.tick(c.map_now())
        assert state.game_state.position == tuple(ROUTE[1]) and c.path == ROUTE and len(moves) == 2
        assert c.recover_local(c.map_now()) and c.index == 1
    asyncio.run(scenario())


def test_choose_first_open_spot_within_three_steps():
    path = lure_stopping_path(chamber(), ROUTE, [], MONSTERS, [])
    assert path == ((10, 10, 7), (11, 10, 7))
    corridor = [cell(x, 10) for x in range(5, 18)]
    assert not lure_stopping_path(corridor, ROUTE, [], MONSTERS, [])
    assert not lure_stopping_path(chamber(), ROUTE, [], MONSTERS, [(12, 10, 7)])
    assert not lure_stopping_path(chamber(), ROUTE, [], [(9, 10, 7), (11, 10, 7)], [])
    distant_room = [cell(x, y) for x in range(15, 19) for y in range(8, 13)]
    assert not lure_stopping_path(corridor + distant_room, ROUTE, [], MONSTERS, [])


def test_stop_before_occupancy_or_room_transition():
    for changes in (dict(kind="blocked"), dict(stairs=True), dict(occupants=1),
                    dict(navigation=[{"kind":"door","door_state_hint":"open"}])):
        cells = chamber()
        next(c for c in cells if c['x']==11 and c['y']==10).update(changes)
        assert not lure_stopping_path(cells, ROUTE, [], MONSTERS, [])
    edge = dict(origin=ROUTE[1], entry=ROUTE[2], exit=[12, 10, 8])
    assert not lure_stopping_path(chamber(), ROUTE, [edge], MONSTERS, [])


@pytest.mark.parametrize("change",["arrived","new_monster","occupied","door"])
def test_gathered_group_stays_latched_while_one_route_step_is_allowed(state, tmp_path, change):
    c, cells = armed(state, tmp_path, mode="lure", lure_count=2, engage="visible")
    cells[:] = chamber()
    c.path = [list(p) for p in ROUTE]
    c.status = 'running'
    follower, _, _ = monster(state,cells,x=8,y=10)
    monster(state,cells,x=9,y=10,cid=follower+1)
    c.combat.pursuit.following = {follower}
    c.combat.pursuit.update = lambda pos,rows,now: [r for r in rows if r['id']==follower]
    assert not asyncio.run(c.combat.tick(c.map_now()))
    assert c.combat.phase == "positioning" and c.combat.sent_target
    assert c.combat.lure_stop_reason == "group" and c.combat.route_step_allowed()
    if change == "arrived":
        state.game_state.position = tuple(ROUTE[1])
        c.index = 1
    elif change == "new_monster":
        monster(state,cells,x=12,y=11,cid=follower+2)
    else:
        tile=next(t for t in cells if (t['x'],t['y'])==(11,10))
        tile.update({'occupants':1,'occupant_types':{'player':1}} if change=="occupied"
                    else {'navigation':[{'kind':'door','door_state_hint':'open'}]})
    assert not c.combat.route_step_allowed()
    assert c.combat.lure_stop_reason == "group"


def test_positioning_accepts_delayed_movement_ack_and_keeps_attacking(state, tmp_path):
    c, cells = armed(state, tmp_path, mode="lure", lure_count=2, engage="visible")
    cells[:] = chamber()
    c.path = [list(p) for p in ROUTE]
    c.status = 'running'
    follower, _, _ = monster(state, cells, x=8, y=10)
    monster(state, cells, x=9, y=10, cid=follower + 1)
    c.combat.pursuit.following = {follower}
    c.combat.pursuit.update = lambda pos, rows, now: [r for r in rows if r['id'] == follower]

    async def scenario():
        assert not await c.combat.tick(c.map_now())
        assert c.combat.sent_target
        sent = connect_fake_walk(state, c)
        immediate_ack = state.game_proxy.inject_to_server
        pending = []

        async def delayed_ack(payload, guard=None):
            assert guard and guard()
            async def complete():
                await asyncio.sleep(.03)
                await immediate_ack(payload, guard)
            pending.append(asyncio.create_task(complete()))

        state.game_proxy.inject_to_server = delayed_ack
        c._route_command = True
        try:
            assert await c.step(list(ROUTE[1]))
            assert len(sent) == 1
            assert c.combat.lure_stop_reason == 'group'
            assert c.combat.sent_target
            c.index = 1
            assert not c.combat.route_step_allowed()
        finally:
            await asyncio.gather(*pending)
            c._route_command = False

    asyncio.run(scenario())
