import asyncio
from types import SimpleNamespace
from test_adaptive_cavebot import cell, setup, connect_fake_walk
from test_automation_safety import state


def test_blocked_route_that_revisits_current_tile_waits_then_resumes(state,tmp_path):
    c,cells,_=setup(state,tmp_path)
    a,b=[10,10,7],[11,10,7]
    c.path=[a,b,a];c.checkpoints=[dict(index=2,waypoint=1)]
    # Only the player and their next step exist in this cul-de-sac.
    cells[:]=[t for t in cells if [t['x'],t['y'],t['z']] in (a,b)]
    target=next(t for t in cells if t['x']==11)
    target.update(occupants=1,occupant_types={'monster':1})
    sent=connect_fake_walk(state,c)
    async def scenario():
        run=asyncio.create_task(c.run())
        async with asyncio.timeout(5):
            while not c.blockage and not run.done():await asyncio.sleep(.01)
            assert c.blockage and not sent and not c.navigation_hold
            target.update(occupants=0,occupant_types={})
            await run
    asyncio.run(scenario())
    assert c.laps==1 and c.status=='complete' and len(sent)==2


def test_late_observed_exit_resumes_held_transition_without_another_use(state,tmp_path):
    c,_,_=setup(state,tmp_path)
    start,exit_tile=[10,10,7],[10,10,8]
    c.path=[start,exit_tile]
    c.edges=[dict(origin=start,entry=start,exit=exit_tile)]
    c.domains=SimpleNamespace(movement_state="running")
    c.status="running"
    c.navigation_hold="Oczekiwanie na wyjście"
    async def observe():
        while c.domains.movement_state!="held":await asyncio.sleep(.01)
        assert c.index==0 and c.navigation_hold
        state.game_state.position=tuple(exit_tile)
    def saved():
        assert c.index==1 and c.navigation_hold is None
        c.status="complete"
    c.save_session_cursor=saved
    async def scenario():
        await asyncio.wait_for(asyncio.gather(c._navigation_loop(),observe()),2)
    asyncio.run(scenario())


def test_replace_offscreen_step_keeps_waypoint_and_validates_live_hops(state,tmp_path):
    origin, target = [77,391,10], [94,391,10]
    cells = [cell(x,391,10) for x in range(77,95)]
    c,_,_ = setup(state,tmp_path,cells=cells,points=[origin,target])
    c.path=[origin,target]
    c.checkpoints=[dict(index=0,waypoint=0),dict(index=1,waypoint=1)]
    monitor = state.passive_map_monitor
    snapshot = monitor.snapshot
    def visible():
        sample = snapshot()
        sample['cells']=[t for t in cells if abs(t['x']-state.game_state.position[0])<=6]
        return sample
    monitor.snapshot = visible
    sent = connect_fake_walk(state,c)
    asyncio.run(asyncio.wait_for(c.run(),10))
    assert c.status == 'complete' and len(sent)==17
    assert c.path == [[x,391,10] for x in range(77,95)]
    assert c.checkpoints[-1] == {'index':17,'waypoint':1}


def test_rerouting_first_leg_keeps_start_for_every_lap(state,tmp_path):
    cells=[cell(x,391,10) for x in range(77,96)]
    c,_,_=setup(state,tmp_path,cells=cells,points=[[77,391,10],[95,391,10]],loop=True)
    anchor=c.path[0]
    for end in (8,16):
        c.replace_path(0,end,c.path[:end+1])
        assert c.path[c.loop_start]==c.path[-1]==anchor
    sent=connect_fake_walk(state,c)
    async def scenario():
        run=asyncio.create_task(c.run())
        try:
            async with asyncio.timeout(12):
                while c.laps<2:
                    await asyncio.sleep(.01)
        finally:
            c.pause('end of test')
            await run
    asyncio.run(scenario())
    assert len(sent)>=72


def test_closed_cycle_traverses_repaired_seam_before_counting_lap(state,tmp_path):
    cells=[cell(x,391,10) for x in range(77,96)]
    c,_,_=setup(state,tmp_path,cells=cells,points=[[77,391,10],[95,391,10]],loop=True)
    c.path=c.path[:19]   # position at the last recorded tile, missing return leg
    c.index=len(c.path)-1
    c.checkpoints=[r for r in c.checkpoints if r['index']<=c.index]
    state.game_state.position=tuple(c.path[-1])
    sent=connect_fake_walk(state,c)
    async def scenario():
        run=asyncio.create_task(c.run())
        try:
            async with asyncio.timeout(10):
                while c.laps<1:await asyncio.sleep(.01)
        finally:
            c.pause('end of test')
            await run
    asyncio.run(scenario())
    assert len(sent)>=18 and c.path[-1]==c.path[c.loop_start]


def test_gotrunks_displacement_after_stairs_preserves_landing_for_next_lap(state, tmp_path):
    origin, landing = [1086, 1300, 6], [1086, 1298, 5]
    displaced, onward = [1087, 1299, 5], [1088, 1299, 5]
    cells = [cell(x,y,z) for x in range(1085,1090) for y in range(1297,1302) for z in (5,6)]
    c, _, _ = setup(state, tmp_path, cells, [landing, onward])
    edge = dict(origin=origin, entry=[1086,1299,6], exit=landing, action='step')
    c.loop = True
    c.path = [origin, landing, onward, landing, origin]
    c.edges = [edge]
    c.index = 1
    c.checkpoints = [dict(index=1,waypoint=1), dict(index=2,waypoint=2)]
    c.status = 'running'
    state.game_state.position = tuple(displaced)
    original = [p[:] for p in c.path]
    walked = connect_fake_walk(state, c)
    assert asyncio.run(c.recover_position(c.map_now()))
    assert len(walked) == 1
    assert c.path == original and c.path[c.index] == landing
    assert list(state.game_state.position) == landing
    assert c.path[:2] == [edge['origin'], edge['exit']]


def test_gotrunks_repairs_only_the_confirmed_floor_crossing_at_current_waypoint(state, tmp_path):
    origin, landing = [1086, 1300, 6], [1086, 1298, 5]
    target = [1087, 1299, 5]
    cells = [cell(*p) for p in (origin, landing, target)]
    c, _, _ = setup(state, tmp_path, cells, [landing, target])
    c.status = 'running'
    c.route_draft = {'points':[origin, landing, target]}
    c.checkpoints = [dict(index=0,waypoint=0),dict(index=1,waypoint=1)]
    c.path = [origin, target]
    state.game_state.position = tuple(origin)
    event = dict(origin=origin, entry=[1086,1299,6], exit=landing, action='step')
    assert not asyncio.run(c.reconnect_route_step(origin,target))
    c.edges = [event]
    c.checkpoints[1]['waypoint'] = 2
    assert not asyncio.run(c.reconnect_route_step(origin,target))
    c.checkpoints[1]['waypoint'] = 1
    assert asyncio.run(c.reconnect_route_step(origin,target))
    assert c.path == [origin,landing,target] and c.index == 0
    assert c.checkpoints[1] == dict(index=1, waypoint=1)
    assert state.game_proxy._inject_queue.empty()

    # Run the repaired segment in both directions for two laps. Transport is
    # replaced by local position observations, including the actual stair exit.
    c.path.extend([landing, origin])
    reverse = dict(origin=landing, entry=[1086,1299,5], exit=origin, action='step')
    c.edges.append(reverse)
    c.loop = True
    crossings = []
    async def cross(edge, sample):
        assert list(state.game_state.position) == edge['origin']
        crossings.append(edge['exit'])
        state.game_state.position = tuple(edge['exit'])
    async def walk(target):
        assert list(state.game_state.position)[2] == target[2]
        state.game_state.position = tuple(target)
        return True
    async def no_combat(sample, boundary):
        return False
    def report(method, *args):
        if method == 'sample' and c.laps == 2:
            c.status = 'complete'
    c.transition = cross
    c.step = walk
    c.domains = SimpleNamespace(movement_state='running', consume_movement=no_combat)
    c._observe_report = report
    asyncio.run(asyncio.wait_for(c._navigation_loop(), 2))
    assert crossings == [landing,origin,landing,origin] and c.laps == 2


def test_gotrunks_door_gap_routes_to_checkpoint_through_exact_approved_door(state, tmp_path):
    from unittest.mock import Mock
    origin, approach, door, exit_tile = [[x,1299,7] for x in (1038,1037,1036,1035)]
    c, _, _ = setup(state,tmp_path,cells=[cell(*p) for p in [origin,approach,door,exit_tile]],
                    points=[origin,exit_tile])
    edge = dict(id='door', action='use_then_step', origin=approach, entry=door, exit=exit_tile, item_id=5281)
    c.status = 'running'
    c.edges = [edge]
    c.path = [origin,door,exit_tile]
    c.checkpoints = [dict(index=0,waypoint=0),dict(index=2,waypoint=1)]
    plan = dict(complete=True, avoid_tiles=[], path=[origin,approach,exit_tile],transitions=[dict(edge)])
    query = Mock(return_value=plan)
    state.passive_map_monitor.atlas.plan = query
    plan['transitions'] = [dict(edge,item_id=9999)]
    assert not asyncio.run(c.reconnect_route_step(origin,door))
    assert c.path == [origin,door,exit_tile]
    plan['transitions'] = [dict(edge)]
    assert asyncio.run(c.reconnect_route_step(origin,door))
    assert query.call_args.args[0] == [origin,exit_tile]
    assert c.path == [origin,approach,exit_tile]
    assert c.checkpoints[-1] == dict(index=2,waypoint=1)
    assert state.game_proxy._inject_queue.empty()
