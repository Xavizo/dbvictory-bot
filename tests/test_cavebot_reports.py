import asyncio
import time
from types import SimpleNamespace as NS

from dbv.brain.cavebot.cavebot_reports import Lap, replay_reports
from dbv.brain.cavebot.cavebot_history import RunHistory, read_reports, save_report


def frame(ms, lap=0, **kwargs):
    return dict(dict(ms=ms,route='Saiyan',revision='r1',lap=lap,connection='in_game',status='running',
                     position=[667,481,7],movement='running',combat='route',waypoint=7), **kwargs)


def replay(*frames):
    return replay_reports(dict(format='dbv-route-replay',version=1,started_at='2026-11-19T13:00:00Z',frames=frames))


def test_legacy_lap_bounds_and_unknown_metrics():
    results = replay(frame(0),frame(500,1),frame(1000,1,combat='clear_path'),frame(2000,2),frame(3000,2))
    assert [(r['lap'],r['complete'],r['duration_ms']) for r in results] == [(1,False,500),(2,True,1500),(3,False,1000)]
    assert results[1]['blocked_ms'] == 1000
    assert all(r['xp'] is None and r['senzu'] is None and r['observed_deaths'] is None for r in results)


def test_no_invented_loops_blockers_or_kills_across_discontinuity():
    rows = replay(frame(0),frame(100,1),frame(9000,1,movement='waiting',blocked_target=[1,2,7]),
                  frame(9100,2),frame(9200,2,connection='panel_offline'),frame(9500,0,revision='changed'))
    assert not any(r['complete'] for r in rows)
    assert rows[1]['unobserved_ms'] == 8900 and rows[1]['blocked_ms'] == 0
    assert len(replay(frame(0),frame(100,4))) == 2


def test_metrics_use_confirmed_counters_and_distinguish_zero():
    a=frame(100,xp=1234,senzu=9,deaths=4)
    group=dict(reason='group',engaged=3,goal=4,waiting=True)
    lap=Lap(a,True,100)
    lap.observe(frame(500,combat='fight',lure=group))
    lap.observe(frame(1000,combat='fight',lure={**group,'engaged':1}))
    r=lap.report(frame(1700,xp=1634,senzu=11,deaths=6),True,'okrążenie')
    assert (r['xp'],r['xp_hour'],r['senzu'],r['senzu_million'],r['observed_deaths'],r['group_median']) == (400,900000,2,5000,2,3)
    r=Lap(a,True,100).report(frame(100,xp=1234,senzu=8),False,'stop')
    assert (r['xp'],r['xp_hour'],r['senzu'],r['senzu_million']) == (0,None,None,None)


def test_stationary_event_and_retained_destination():
    lap=Lap(frame(0),False,0)
    for ms in (1000,2000,3000,4000,5000):
        lap.observe(frame(ms,step_target=[667,481,7],route_next=[668,481,7],reason='czekam na mapę'))
    lap.observe(frame(5100,position=[668,481,7]))
    assert [(e['kind'],e['target']) for e in lap.events if e['kind'] in {'stationary','moved'}] == [
        ('stationary',[667,481,7]),('moved',None)]
    stop = next(e for e in lap.events if e['kind'] == 'stationary')
    assert (stop['route_next'], stop['movement'], stop['combat']) == ([668,481,7], 'running', 'route')
    assert '(668, 481, 7)' in stop['detail']


def owner_for(path):
    gs=NS(position=(50,50,7),level=2,experience=110,stats_updated_at=time.time(),session_kills=0,
          creatures={},kill_log=[],senzu_usage=NS(total=0))
    state=NS(game_state=gs,passive_map_monitor=NS(atlas=NS(path=path)),settings={})
    combat=NS(policy_mode='lure',config={'lure_count':4},confirmed_count=2,lure_waiting=False,lure_stop_reason=None,
              phase='lure',target={'id':5},bank_members=set(),pursuit=NS(following={5,6}))
    return NS(state=state,route_draft=None,checkpoints=[],last_waypoint=0,combat=combat,manual_until=0,domains=NS(movement_state='running'),
              path=[[50,50,7],[51,50,7]],index=0,loop_start=0,laps=0,name='Saiyan',reason='Marsz',blockage=None,navigation_hold=None)


def test_live_report_closes_once_persists_and_recovers_after_panel_and_engine_restart(tmp_path):
    async def exercise():
        o=owner_for(tmp_path/'atlas.sqlite3')
        run=RunHistory(o,False)
        o.state.adaptive_cavebot=NS(reporter=run)
        run.sample(True)
        o.state.game_state.senzu_usage.total+=1
        o.state.game_state.kill_log.append(dict(cid=5,name='Bardock'))
        o.state.game_state.session_kills=1
        run.sample(True)
        run.interrupted([51,50,7],'position_interrupted','Brak świeżej pozycji i HP.')
        assert read_reports(o.state)['active']['events'][0]['detail'] == 'Brak świeżej pozycji i HP.'
        run.finish(True,'okrążenie')
        run.finish(False,'stop')
        await asyncio.gather(*run.writes)
        rows=read_reports(owner_for(tmp_path/'atlas.sqlite3').state)
        assert len(rows['reports']) == 1 and not rows['active']
        one=rows['reports'][0]
        assert (one['complete'],one['senzu'],one['observed_deaths']) == (True,1,1)
        assert 'events' not in one
        full=read_reports(o.state,one['id'])
        assert [(e['kind'],e['detail']) for e in full['events'] if e['kind']=='death'] == [('death','Bardock')]
        save_report(run.path,full)
        assert len(read_reports(o.state)['reports']) == 1
        assert read_reports(o.state,'unknown') is None
    asyncio.run(exercise())


def test_disappeared_and_inaccessible_targets_have_distinct_outcomes(tmp_path):
    o=owner_for(tmp_path/'atlas.sqlite3')
    run=RunHistory(o,True)
    run.sample(True)
    now=time.monotonic()
    run.tracked={5:now-2,6:now-2}
    gs=run.gs
    gs.creatures[6]=dict(x=53,y=50,z=7,health=95,source='dll',t=time.time(),name='Bardock')
    o.map_now=lambda:dict(cells=[dict(x=53,y=50,z=7,kind='clear',occupant_types={'monster':1})])
    run._lost_contacts(run.frame(now),now)
    assert {e['kind'] for e in run.lap.events} >= {'unreachable','contact_lost'}
    assert not any(e['kind']=='death' for e in run.lap.events)
    assert not run.lap.start_known
