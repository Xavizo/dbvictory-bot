import asyncio
from test_adaptive_cavebot import cell, setup, connect_fake_walk
from test_automation_safety import state


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
