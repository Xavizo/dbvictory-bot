"""Deterministic eight-follower simulations through the combat movement entry point."""
import asyncio
from collections import deque
from itertools import product
from types import SimpleNamespace
import time

import pytest

import cavebot_combat
from cavebot_lure import queued_pack_step
from test_adaptive_cavebot import cell, connect_fake_walk
from test_automation_safety import state
from test_v2_combat import armed, monster


def room(left, right, top, bottom):
    return {(x+20,y+20,7) for x,y in product(range(left,right+1),range(top,bottom+1))}


TAIL = [(x,y) for x in range(1,5) for y in (0,1)]
SIDES = [(2,0),(3,0),(4,0),(3,1),(8,0),(8,1),(9,0),(9,1)]
OPEN = room(0,12,-3,4)
CASES = [
    ("corridor_two",room(0,12,0,1),SIDES,(6,0),4),
    ("corridor_three",room(0,12,0,2),SIDES,(6,0),7),
    ("single_queue",room(0,12,0,1),TAIL,(5,0),2),
    ("two_opens_into_room",room(0,5,0,1)|room(6,13,-3,4),TAIL,(5,0),6),
    ("three_opens_into_room",room(0,5,0,2)|room(6,13,-3,4),TAIL,(5,0),6),
    ("room_wall",OPEN,SIDES,(6,-3),8),
    ("room_center",OPEN,SIDES,(6,0),8),
]


def separation(a,b):
    return max(abs(a[0]-b[0]),abs(a[1]-b[1]))


def approach(terrain, mobs, hero, tick):
    # Independent actors: no shared slot assignment, no stacking, stop on
    # first contact. Two of the eight walk only on alternate ticks.
    for ident,pos in sorted(enumerate(mobs),key=lambda m:(separation(m[1],hero),m[0])):
        if separation(pos,hero)<=1 or (ident in (0,2) and tick%2):
            continue
        free=terrain-set(mobs)-{hero}
        queue=deque([(pos,None)])
        seen={pos}
        while queue:
            p,first=queue.popleft()
            if separation(p,hero)<=1:
                mobs[ident]=first
                break
            neighbors=((p[0]+dx,p[1]+dy,p[2]) for dx,dy in product((-1,0,1),repeat=2))
            for q in sorted((q for q in neighbors if q in free and q not in seen),
                            key=lambda q:(separation(q,hero),q)):
                seen.add(q)
                queue.append((q,first or q))


async def exercise(state,tmp_path,monkeypatch,terrain,pack,start):
    controller,cells=armed(state,tmp_path,mode="lure",lure_count=8,engage="visible")
    gs=state.game_state
    start=(start[0]+20,start[1]+20,7)
    gs.position=start
    controller.path=[[start[0]+i,start[1],7] for i in range(6)]
    controller.status="running"
    cells[:]=[cell(*p) for p in sorted(terrain)]
    tiles={(c["x"],c["y"],c["z"]):c for c in cells}
    mobs=sorted((x+20,y+20,7) for x,y in pack)
    rows=[monster(state,cells,x=p[0],y=p[1],cid=0x40000001+i)[1] for i,p in enumerate(mobs)]
    combat=controller.combat
    # The scenario starts at the moment of reaching the lure limit.
    def followers(player,candidates,now):
        combat.pursuit.following={c["id"] for c in candidates}
        return candidates
    combat.pursuit.update=followers
    combat.defer_movement=True
    clock=time.monotonic()
    monkeypatch.setattr(cavebot_combat,"time",SimpleNamespace(monotonic=lambda:clock,time=time.time))
    connect_fake_walk(state,controller)
    walk=state.game_proxy.inject_to_server
    async def dispatch(payload,guard):
        if payload[0]==0xa1:
            assert guard()
        else:
            await walk(payload,guard)
    state.game_proxy.inject_to_server=dispatch
    positions=[start]
    for tick in range(24):
        clock+=.5
        for row in rows:
            row["t"]=gs.stats_updated_at=time.time()
        combat.movement_intent=None
        consumed=await combat.tick(controller.map_now())
        assert combat.lure_stop_reason=="group" and combat.sent_target
        intent=combat.movement_intent
        if intent:
            assert intent["validate"]()
            assert await controller.step(intent["target"],validate=intent["validate"])
        elif not consumed:
            assert combat.route_step_allowed()
            controller._route_command=True
            try:
                assert await controller.step(controller.path[controller.index+1])
            finally:
                controller._route_command=False
            controller.index+=1
        if positions[-1]!=gs.position:
            positions.append(gs.position)
        approach(terrain,mobs,gs.position,tick)
        for tile in tiles.values():
            tile.update(occupants=0,occupant_types={})
        for pos,row in zip(mobs,rows):
            row.update(x=pos[0],y=pos[1])
            tiles[pos].update(occupants=1,occupant_types={"monster":1})
    return sum(separation(p,gs.position)<=1 for p in mobs),positions


@pytest.mark.parametrize("name,terrain,pack,start,expected",CASES,ids=[r[0] for r in CASES])
def test_full_pack_reaches_distinct_slots(state,tmp_path,monkeypatch,name,terrain,pack,start,expected):
    adjacent,path=asyncio.run(exercise(state,tmp_path,monkeypatch,terrain,pack,start))
    assert adjacent>=expected
    assert len(path)==len(set(path))<=4
    if name in {"room_center","single_queue","corridor_two"}:
        assert len(path)==1
    elif name in {"room_wall","corridor_three"}:
        assert len(path)>=3


def test_rear_with_free_approach_keeps_its_spacing():
    area=room(0,5,0,1)|room(6,13,-3,4)
    pack={(x+20,y+20,7) for x,y in TAIL}
    origin,target=(25,20,7),(26,19,7)
    assert queued_pack_step(area,origin,target,pack,3)
    # The rear can now catch up on its own. Do not stretch it one more tile.
    assert not queued_pack_step(area|room(0,5,-1,0),origin,target,pack,3)


def test_full_pack_correction_is_cancelled_when_destination_changes(state,tmp_path):
    controller,cells=armed(state,tmp_path,mode="lure",lure_count=8,engage="visible")
    cells[:]=[cell(*p) for p in OPEN]
    state.game_state.position=(26,17,7)
    controller.path=[[26+i,17,7] for i in range(6)]
    controller.status="running"
    for i,(x,y) in enumerate(SIDES):
        monster(state,cells,x=x+20,y=y+20,cid=0x40000001+i)
    combat=controller.combat
    combat.defer_movement=True
    combat.pursuit.update=lambda pos,rows,now:rows
    assert asyncio.run(combat.tick(controller.map_now()))
    intent=combat.movement_intent
    assert intent and intent["validate"]()
    final=combat.pack_destination
    assert final != tuple(intent["target"])
    next(c for c in cells if (c["x"],c["y"],c["z"])==final)["kind"]="blocked"
    assert not intent["validate"]()
    assert combat.lure_stop_reason=="group" and combat.sent_target
