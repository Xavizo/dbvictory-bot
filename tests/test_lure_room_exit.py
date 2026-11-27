"""Synthetic room and corridor maps. The combat controller uses a fake game session."""
import asyncio

import pytest

from dbv.brain.cavebot.cavebot_lure import pack_access_before_choke, room_before_corridor
from test_adaptive_cavebot import cell
from test_automation_safety import state
from test_v2_combat import armed, monster


GROUP = [(9,9), (9,10), (9,11), (10,11)]
ROUTE = [[x,10,7] for x in range(10,18)]


def room(width=2, length=6):
    return [cell(x,y,kind='blocked' if 12 <= x < 12+length and not 9 <= y < 9+width else 'clear')
            for x in range(5,20) for y in range(5,16)]


@pytest.mark.parametrize('width, length, expected', [
    (1,5,True), (2,5,True), (3,5,True), (4,5,False), (2,1,False), (2,0,False),
])
@pytest.mark.parametrize('vertical',[False,True])
def test_room_to_known_corridor(width,length,expected,vertical):
    cells = room(width,length)
    # Center the one-tile corridor on the route.
    if width == 1:
        for t in cells:
            t['y'] += 1
    def point(x,y,z):
        return (y,x,z) if vertical else (x,y,z)
    for t in cells:
        t['x'],t['y'],t['z'] = point(t['x'],t['y'],t['z'])
    assert room_before_corridor(cells,[point(*p) for p in ROUTE],[],{point(x,y,7) for x,y in GROUP}) is expected


def test_diagonal_corridor_and_open_map_edge():
    cells = [cell(x,y,kind='no_ground' if x>=12 and abs(y-x)>1 else 'clear')
             for x in range(5,19) for y in range(5,19)]
    route = [[n,n,7] for n in range(10,16)]
    members = {(x,y,7) for x,y in GROUP}
    assert room_before_corridor(cells,route,[],members)
    for t in cells:
        if t['kind']=='no_ground':t['kind']='unknown'
    assert not room_before_corridor(cells,route,[],members)


@pytest.mark.parametrize('case',['unknown_wall','occupants','already_inside','across_mouth','transition','no_melee_access'])
def test_ambiguous_or_unfavorable_terrain_does_not_end_lure(case):
    cells = room()
    route = ROUTE
    members = {(x,y,7) for x,y in GROUP}
    edges = []
    if case == 'unknown_wall':
        cells[:] = [t for t in cells if t['y'] != 8]
    elif case == 'occupants':
        for t in cells:
            if t['kind']=='blocked':
                t.update(occupants=1,occupant_types={'player':1})
    elif case == 'already_inside':
        route = ROUTE[3:]
    elif case == 'across_mouth':
        route = [[11,10,7],[12,10,7],[12,9,7],[11,9,7]]
    elif case == 'transition':
        edges = [dict(origin=ROUTE[1],entry=ROUTE[2],exit=ROUTE[2])]
    else:
        for t in cells:
            if t['x']==8:t['kind']='blocked'
        members = {(7,10,7)}
    assert not room_before_corridor(cells,route,edges,members)


@pytest.mark.parametrize('count, extra, stop', [
    (4,None,True), (3,None,False), (4,'ahead',True), (4,'sealed',True), (4,'ignored',True),
])
def test_four_of_eight_at_room_exit(state,tmp_path,count,extra,stop):
    c,cells = armed(state,tmp_path,mode='lure',lure_count=8,engage='near',radius=3)
    c.path = ROUTE
    cells[:] = room()
    c.status='running'
    for i,(x,y) in enumerate(GROUP[:count]):
        monster(state,cells,x=x,y=y,cid=0x40000001+i)
    if extra:
        if extra=='sealed':
            for t in cells:
                if t['x']==15:t['kind']='blocked'
        monster(state,cells,name='Rat' if extra=='ignored' else 'Wolf',x=16,y=10,cid=0x40000009)
    assert c.combat.route_step_allowed() is (not stop)
    assert asyncio.run(c.combat.tick(c.map_now())) is stop
    assert c.combat.confirmed_count==count and c.combat.sent_target
    assert c.combat.snapshot()['lure']['stop_reason']==('corridor' if stop else None)
    if stop:
        # Stay in the room as the pack shrinks, then resume the route when clear.
        for i,(x,y) in enumerate(GROUP[:count]):
            state.game_state.creatures.pop(0x40000001+i)
            next(t for t in cells if (t['x'],t['y'])==(x,y)).update(occupants=0,occupant_types={})
            assert asyncio.run(c.combat.tick(c.map_now()))
        c.combat.group_clear_since -= 1
        assert not asyncio.run(c.combat.tick(c.map_now()))
        assert c.combat.route_step_allowed() and c.combat.lure_stop_reason is None


def test_fourth_member_arriving_during_attack_stops_the_pending_route_step(state,tmp_path):
    c,cells = armed(state,tmp_path,mode='lure',lure_count=8)
    c.path=ROUTE
    cells[:]=room()
    c.status='running'
    for i,(x,y) in enumerate(GROUP[:3]):
        monster(state,cells,x=x,y=y,cid=0x40000001+i)
    async def attack(_payload,guard):
        assert guard()
        monster(state,cells,x=10,y=11,cid=0x40000004)
    state.game_proxy.inject_to_server=attack
    assert c.combat.route_step_allowed()
    assert asyncio.run(c.combat.tick(c.map_now()))
    assert c.combat.lure_stop_reason=='corridor' and c.combat.sent_target
    assert not c.combat.route_step_allowed()


def test_open_bank_before_a_pinch_counts_nearby_recruits_only_for_position(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure',lure_count=8)
    c.path=ROUTE
    cells[:]=[cell(x,y,kind='blocked' if x>=13 and y!=10 else 'clear')
              for x in range(4,20) for y in range(5,16)]
    c.status='running'
    for i,(x,y) in enumerate([(9,10),(8,9),(8,10),(8,11)]):
        monster(state,cells,x=x,y=y,cid=0x40000001+i)
    assert not c.combat.route_step_allowed()
    assert c.combat.confirmed_count==1 and c.combat.lure_stop_reason=='corridor'
    # Changing selected target cannot release the stop while the group is present.
    c.combat.accept_manual_target(0x40000002)
    assert asyncio.run(c.combat.tick(c.map_now()))
    assert c.combat.lure_stop_reason=='corridor'


def test_preview_shows_only_contiguous_planned_steps_or_real_lure_stop(state,tmp_path):
    c,_=armed(state,tmp_path,mode='lure',lure_count=8)
    c.status='running'
    c.path=[*ROUTE,[17,10,8]]
    assert c.map_preview()=={'path':list(map(tuple,ROUTE)), 'stop':None}
    c.combat.phase='fight'
    c.combat.lure_stop_reason='corridor'
    assert c.map_preview()=={'path':[(10,10,7)],'stop':(10,10,7)}
    c.status='paused'
    assert c.map_preview() is None


@pytest.mark.parametrize('goal',[4,6,8,10])
def test_traffic_stop_works_with_partial_group_and_one_recruit_ahead(state,tmp_path,goal):
    c,cells=armed(state,tmp_path,mode='lure',lure_count=goal,engage='visible')
    c.path=ROUTE
    c.status='running'
    cells[:]=room(width=1)
    for tile in cells:
        tile['y'] += 1
    for i,(x,y) in enumerate([(9,10),(8,9),(8,11),(15,10)]):
        monster(state,cells,x=x,y=y,cid=0x40000001+i)
    assert not c.combat.route_step_allowed()
    assert c.combat.lure_access == (3,1)
    assert c.combat.bank_members == {0x40000001,0x40000002,0x40000003}
    assert asyncio.run(c.combat.tick(c.map_now()))
    assert c.combat.sent_target and c.combat.confirmed_count == 1
    assert c.combat.placement is None and c.combat.lure_stop_reason == 'corridor'
    # Removing the pack leaves an eligible recruit in front. A repeated
    # read of the very same empty-bank snapshot must not authorize movement.
    for cid in tuple(c.combat.bank_members):
        row=state.game_state.creatures.pop(cid)
        next(t for t in cells if (t['x'],t['y'])==(row['x'],row['y'])).update(occupants=0,occupant_types={})
    sample=dict(c.map_now(),captured_at=100)
    assert asyncio.run(c.combat.tick(sample))
    c.combat.group_clear_since -= 1
    assert asyncio.run(c.combat.tick(sample))
    assert c.combat.lure_access == (3,1)
    assert not asyncio.run(c.combat.tick(dict(sample,captured_at=101)))
    assert c.combat.route_step_allowed() and c.combat.lure_access is None


@pytest.mark.parametrize('width',[1,2,3,4])
@pytest.mark.parametrize('reverse',[False,True])
def test_access_uses_distinct_attack_slots_across_passage_widths(width,reverse):
    cells=room(width=width)
    if width==1:
        for tile in cells:
            tile['y']+=1
    group={(x,y,7) for x in (7,8) for y in (9,10,11)}
    route=ROUTE
    if reverse:
        for tile in cells:
            tile['x']=20-tile['x']
        route=[(20-x,y,z) for x,y,z in route]
        group={(20-x,y,z) for x,y,z in group}
    result=pack_access_before_choke(cells,route,[],group)
    if width<=3:
        assert result and (result.here,result.ahead)==(6,width)
    else:
        assert result is None


@pytest.mark.parametrize('case',['open','one_follower','only_ahead','ignored','unknown','transition','constant_width'])
def test_continue_without_forecast_loss(state,tmp_path,case):
    c,cells=armed(state,tmp_path,mode='lure',lure_count=8,engage='visible')
    c.path=ROUTE
    c.status='running'
    cells[:]=room(width=2,length=0 if case=='open' else 6)
    points=[(9,10),(8,9),(8,11)]
    if case=='one_follower':points=points[:1]
    if case=='only_ahead':points=[(12,10),(13,10),(14,10)]
    if case=='transition':c.edges=[dict(origin=ROUTE[0],entry=ROUTE[1],exit=ROUTE[1])]
    if case=='unknown':
        for tile in cells:
            if tile['kind']=='blocked':tile['kind']='unknown'
    if case=='constant_width':
        for tile in cells:
            if tile['y'] not in (9,10,11):tile['kind']='blocked'
    for i,(x,y) in enumerate(points):
        monster(state,cells,name='Rat' if case=='ignored' and i else 'Wolf',x=x,y=y,cid=0x40000001+i)
    assert c.combat.route_step_allowed()
    assert c.combat.lure_access is None


def test_closing_mob_is_forecast_while_distant_idle_mob_is_not(state,tmp_path):
    import time
    c,cells=armed(state,tmp_path,mode='lure',lure_count=8,engage='visible')
    c.path=ROUTE
    c.status='running'
    cells[:]=room(width=1)
    for tile in cells:tile['y']+=1
    monster(state,cells,x=9,y=10)
    cid,row,old=monster(state,cells,x=5,y=10,cid=0x40000002)
    now=time.monotonic()
    c.combat.pursuit.update((10,10,7),c.combat.candidates(c.map_now()),now-.3)
    assert c.combat.route_step_allowed()
    old.update(occupants=0,occupant_types={})
    row.update(x=6)
    next(t for t in cells if (t['x'],t['y'])==(6,10)).update(occupants=1,occupant_types={'monster':1})
    c.combat.pursuit.update((10,10,7),c.combat.candidates(c.map_now()),now)
    assert cid not in c.combat.pursuit.following
    assert not c.combat.route_step_allowed()
    assert c.combat.lure_access==(2,1)


def test_recruit_close_on_screen_with_long_walk_around_wall_is_excluded():
    cells=room(width=1)
    for tile in cells:
        tile['y']+=1
        if tile['x']==8 and tile['y']>6:tile['kind']='blocked'
    member=(9,10,7)
    distant=(7,10,7)
    assert pack_access_before_choke(cells,ROUTE,[],{member,distant},{distant:3}) is None
