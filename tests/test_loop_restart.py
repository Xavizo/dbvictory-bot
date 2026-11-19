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
