"""Synthetic navigation only: no client, socket or DLL attachment."""
import asyncio
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from dbv.mcp_server import server
from dbv.brain.cavebot.adaptive_cavebot import AdaptiveCavebot,OFFSETS,detour,step_clear
from dbv.brain.navigation.map_atlas import MapAtlas
from dbv.brain.cavebot.cavebot_walking import WalkOutcome
from dbv.client_bridge.protocol import WALK_OPCODES,ClientOpcode,PacketReader
from test_automation_safety import state,proxy


def cell(x,y,z=7,kind='clear',item=351,navigation=None):
    return dict(x=x,y=y,z=z,kind=kind,items=[dict(id=item,stack=0,flags=[])],navigation=navigation or [],occupants=0,occupant_types={})


def setup(state,tmp_path,cells=None,points=None,edges=None,loop=False):
    cells=cells or [cell(x,y) for x in range(10,14) for y in range(10,13)]
    points=points or [[10,10,7],[13,10,7]]
    atlas=MapAtlas(tmp_path,'a'*64);atlas.observe(dict(status='ready',cells=cells))
    for i,e in enumerate(edges or []):e.setdefault('id',f'edge-{i}')
    links=[dict(from_index=i,id=e['id']) for i,(a,b) in enumerate(zip(points,points[1:])) for e in (edges or []) if e['origin']==a and e['exit']==b and (not e.get('object_change') or e.get('object_change',{}).get('behavior')=='use_enters')]
    draft=atlas.save_draft('synthetic route',points,loop=loop,links=links)
    gs=state.game_state;gs.position=tuple(points[0]);gs.hp=gs.max_hp=100;gs.level=200;gs.client_state_required=False
    gs.stats_updated_at=time.time()
    state.ready=True;state.game_proxy=proxy();state.game_proxy._client_packet_callbacks=[]
    monitor=SimpleNamespace(atlas=atlas,learner=SimpleNamespace(records={str(i):e for i,e in enumerate(edges or [])}))
    monitor.snapshot=lambda:dict(status='ready',stale=False,age_seconds=0,center=list(gs.position),cells=cells)
    state.passive_map_monitor=monitor
    controller=AdaptiveCavebot(state);state.adaptive_cavebot=controller
    controller.prepare(atlas.scope,draft,loop)
    return controller,cells,draft


def connect_fake_walk(state,controller):
    sent=[];directions={int(WALK_OPCODES[d]):delta for delta,d in OFFSETS.items()}
    async def inject(payload,guard=None):
        assert guard and guard()
        sent.append(payload);dx,dy=directions[payload[0]]
        x,y,z=state.game_state.position;state.game_state.position=(x+dx,y+dy,z)
        state.game_state.stats_updated_at=time.time()
        controller.last_command=0  # no wall-time pacing needed in synthetic server
    state.game_proxy.inject_to_server=inject
    return sent


def test_explicit_execution_acknowledges_each_position_then_completes(state,tmp_path):
    c,_,_=setup(state,tmp_path);sent=connect_fake_walk(state,c)
    asyncio.run(c.run())
    assert c.status=='complete' and c.laps==1 and state.game_state.position==(13,10,7)
    assert len(sent)==3 and not state.game_proxy._client_packet_callbacks


def test_doorway_cursor_survives_occupied_landing_and_two_rounds(state,tmp_path):
    origin, entry, far = [1573,561,7],[1572,561,7],[1571,561,7]
    cells=[cell(x,y) for x,y,z in (origin,entry,far)]
    c,_,_=setup(state,tmp_path,cells,[origin,far,origin])
    cells[1].update(items=[dict(id=5126,stack=1,flags=[])],
                    navigation=[dict(kind='door',door_state_hint='open')])
    edge=dict(origin=origin,entry=entry,exit=far,action='use_then_step',item_id=5125,open_item_id=5126)
    c.path=[origin,far,origin]
    c.edges=[edge,dict(edge,origin=far,exit=origin)]
    c.checkpoints=[dict(index=i,waypoint=i) for i in range(3)]
    c.loop=True
    connect_fake_walk(state,c)
    walk=state.game_proxy.inject_to_server
    visited=[]
    async def walk_then_occupy(payload,guard=None):
        await walk(payload,guard)
        pos=list(state.game_state.position)
        visited.append(pos)
        if len(visited)==1:
            cells[2]['occupants']=1
            asyncio.get_running_loop().call_later(.2,cells[2].update,{'occupants':0})
    state.game_proxy.inject_to_server=walk_then_occupy
    def report(method,*args):
        if c.laps==2:
            c.status='complete'
    c._observe_report=report
    asyncio.run(asyncio.wait_for(c.run(),4))
    assert visited==[entry,far,entry,origin]*2
    assert len(c.path)==5 and len(c.edges)==4


def test_walkable_ground_under_loose_container_in_historical_and_live_maps():
    import json
    from dbv.brain.navigation.map_atlas import decode_tile
    from dbv.brain.navigation.passive_map import classify_tile
    items=[dict(id=408,stack=0,flags=['ground','not_moveable']),dict(id=2519,stack=1,flags=['container','not_pathable'])]
    assert classify_tile(items,False)[0]=='clear'
    assert classify_tile(items,True)[0]=='blocked'
    tile=dict(x=1571,y=561,z=7,kind='avoid',items=items)
    assert decode_tile((1571,561,7,json.dumps(tile),0))[1]['kind']=='clear'
    for flags in (['not_pathable'],['container','not_moveable','not_pathable'],['ground','not_pathable']):
        assert classify_tile([*items,dict(id=456,stack=2,flags=flags)],False)[0]=='avoid'
    assert classify_tile([*items,dict(id=1,stack=2,flags=['not_walkable'])],False)[0]=='blocked'


def test_default_loop_has_real_return_path_and_repeats_whole_route(state,tmp_path):
    c,_,draft=setup(state,tmp_path,points=[[10,10,7],[13,10,7],[10,10,7]])
    c.prepare('a'*64,draft)  # default: loop
    assert c.path[-1]==c.path[0]
    sent=connect_fake_walk(state,c);inject=state.game_proxy.inject_to_server
    async def bounded(payload,guard=None):
        await inject(payload,guard)
        if len(sent)==8:c.pause('bounded synthetic test')
    state.game_proxy.inject_to_server=bounded
    asyncio.run(c.run())
    assert len(sent)==8 and c.laps==1 and c.status=='paused'
    assert [p[0] for p in sent]==[0x66]*3+[0x68]*3+[0x66]*2


def test_redundant_return_is_skipped_but_labels_and_unique_rooms_are_kept(state,tmp_path):
    a,b,c,d=([x,10,7] for x in (10,11,12,13))
    controller,_,_=setup(state,tmp_path,points=[a,c,a,c,d])
    controller.route_draft['labels']=['','Sala','','','']
    assert not controller.skip_repeated_sweep()
    controller.route_draft['labels']=['']*5
    assert controller.skip_repeated_sweep()
    assert controller.path[controller.index:]==[a,b,c,d]
    assert not controller.skip_repeated_sweep()
    controller.index=0
    controller.edges=[dict(origin=b,exit=c)]
    assert not controller.skip_repeated_sweep()


def test_live_diagonal_ignores_side_occupants_but_respects_destination(state,tmp_path):
    c,cells,_=setup(state,tmp_path,points=[[10,10,7],[12,12,7]])
    next(v for v in cells if (v['x'],v['y'])==(11,10)).update(occupants=1,occupant_types={'monster':1})
    assert not step_clear(cells,[10,10,7],[11,10,7])
    assert step_clear(cells,[10,10,7],[11,11,7])
    assert detour(cells,[10,10,7],[12,12,7])[1]==[11,11,7]
    sent=connect_fake_walk(state,c);asyncio.run(c.run())
    assert sent[0][0]==0x6b and c.status=='complete'


def test_map_with_no_detour_pauses_without_attempting_use_or_movement(state,tmp_path):
    c,cells,_=setup(state,tmp_path,cells=[cell(10,10),cell(11,10)],points=[[10,10,7],[11,10,7]])
    cells[1]['kind']='blocked'
    asyncio.run(c.run())
    assert c.status=='paused' and 'obejścia' in c.reason and state.game_proxy._inject_queue.empty()


@pytest.mark.parametrize('failure',['hp','snapshot','profile','recording','proxy'])
def test_guard_rejects_stale_or_unsafe_start_without_injection(state,tmp_path,failure):
    c,_,draft=setup(state,tmp_path)
    if failure=='hp':state.game_state.hp=0
    if failure=='snapshot':state.game_state.client_state_required=True;state.game_state.client_snapshot={'monotonic':time.monotonic()-2}
    if failure=='profile':state.passive_map_monitor.atlas.scope='different'
    if failure=='recording':state.recording_active=True
    if failure=='proxy':state.game_proxy.logged_in=False
    with pytest.raises(ValueError):c.prepare('a'*64,draft)
    assert state.game_proxy._inject_queue.empty()


def test_stale_map_cannot_authorize_a_command(state,tmp_path):
    c,_,_=setup(state,tmp_path);c.status='running'
    state.passive_map_monitor.snapshot=lambda:dict(status='ready',stale=False,age_seconds=3,center=[10,10,7],cells=[cell(11,10)])
    with pytest.raises(ValueError):asyncio.run(c.step([11,10,7]))
    assert state.game_proxy._inject_queue.empty()


def test_pause_invalidates_queued_command_even_after_immediate_resume(state,tmp_path):
    c,_,_=setup(state,tmp_path);c.status='running'
    asyncio.run(c.send(b'\x66'))
    _,_,guard=state.game_proxy._inject_queue.get_nowait()
    assert guard();c.pause('pause');c.status='running'
    assert not guard()


@pytest.mark.parametrize('opcode,payload',[(ClientOpcode.USE_ITEM,b'\x0a\x00\x09\x00\x07'),(ClientOpcode.CANCEL_ATTACK,b'')])
def test_manual_control_pauses_route_and_removes_callback(state,tmp_path,opcode,payload):
    c,_,_=setup(state,tmp_path)
    async def inject(packet,guard=None):
        for callback in list(state.game_proxy._client_packet_callbacks):callback(opcode,PacketReader(payload))
    state.game_proxy.inject_to_server=inject
    asyncio.run(c.run())
    assert c.status=='paused' and 'Ręczny' in c.reason and not state.game_proxy._client_packet_callbacks


def test_missing_ack_on_ordinary_step_rechecks_terrain_and_retries(state,tmp_path):
    c,_,_=setup(state,tmp_path,cells=[cell(10,10),cell(11,10)],points=[[10,10,7],[11,10,7]])
    confirmations=[]
    async def confirm(target,*a,**kw):
        confirmations.append(time.monotonic())
        if len(confirmations)==1:return WalkOutcome.TIMEOUT
        state.game_state.position=tuple(target)
        return WalkOutcome.ARRIVED
    c.wait_position=confirm
    asyncio.run(asyncio.wait_for(c.run(),1.9))
    assert c.status=='complete' and c.laps==1 and len(confirmations)==2
    assert confirmations[1]-confirmations[0]>=1
    assert state.game_proxy._inject_queue.qsize()==2


@pytest.mark.parametrize('finish',['arrive','late_arrival','death','stop','disconnect'])
def test_expired_hp_during_dispatched_step_waits_for_same_session(state,tmp_path,finish):
    c,_,_=setup(state,tmp_path,points=[[10,10,7],[11,10,7]])
    gs=state.game_state
    gs.client_state_required=True
    def publish(pos, hp=100, age=0):
        gs.client_snapshot=dict(position=pos,hp=hp,max_hp=100,monotonic=time.monotonic()-age)
    publish((10,10,7))
    sent=[]
    async def inject(payload,guard=None):
        assert guard and guard()
        sent.append(payload[0])
        publish((10,10,7),age=1)
    state.game_proxy.inject_to_server=inject
    if finish=='late_arrival':
        original=c.wait_position
        async def short_deadline(target,origin,timeout,intermediate,recover):
            return await original(target,origin,.05,intermediate,recover)
        c.wait_position=short_deadline
    async def simulate():
        run=asyncio.create_task(c.run())
        try:
            async with asyncio.timeout(1):
                while not sent:await asyncio.sleep(.01)
            await asyncio.sleep(.12)
            assert gs.hp==0 and c.status=='running' and sent==[0x66]
            if finish=='stop':c.stop(preserve_progress=True)
            if finish=='disconnect':state.ready=False
            publish((11,10,7),0 if finish=='death' else 100)
            await asyncio.wait_for(run,1)
        finally:
            run.cancel()
            await asyncio.gather(run,return_exceptions=True)
    asyncio.run(simulate())
    expected={'arrive':'complete','late_arrival':'complete','stop':'idle','death':'paused','disconnect':'paused'}
    assert sent==[0x66] and c.status==expected[finish] and c.laps==int(expected[finish]=='complete')
    assert not c.blocked and not c._failed_exits


def stair_edge(action='step'):
    return dict(origin=[10,10,7],entry=[10,9,7],exit=[20,20,6],action=action,item_id=1956,entry_items=[1956],evidence='observed')


def test_floor_change_needs_observed_direction_and_cannot_invent_return(state,tmp_path):
    edge=stair_edge();cells=[cell(10,10),cell(10,9,kind='blocked',item=1956),cell(20,20,6)]
    c,_,draft=setup(state,tmp_path,cells,[[10,10,7],[20,20,6]],[edge])
    with pytest.raises(ValueError,match='Brak znanego powrotu'):c.prepare('a'*64,draft,True)
    c.prepare('a'*64,draft,False)
    async def stairs(payload,guard=None):
        assert guard() and payload==b'\x65'
        state.game_state.position=(20,20,6)
    state.game_proxy.inject_to_server=stairs
    asyncio.run(c.run());assert c.status=='complete'


@pytest.mark.parametrize('failure',['item','occupied','level'])
def test_transition_revalidates_live_entrance_before_use(state,tmp_path,failure):
    edge=stair_edge('use');cells=[cell(10,10),cell(10,9,kind='blocked',item=1956),cell(20,20,6)]
    c,_,_=setup(state,tmp_path,cells,[[10,10,7],[20,20,6]],[edge])
    if failure=='item':cells[1]['items'][0]['id']=1234
    if failure=='occupied':cells[1].update(occupants=1,occupant_types={'player':1})
    if failure=='level':cells[1]['navigation']=[dict(kind='level_gate',required_level=320)]
    asyncio.run(c.run())
    assert c.status=='paused' and state.game_proxy._inject_queue.empty()


def test_rope_preview_is_available_but_executor_requires_supported_action(state,tmp_path):
    edge=stair_edge('use_with');cells=[cell(10,10),cell(10,9,item=1956),cell(20,20,6)]
    with pytest.raises(ValueError,match='narzędzia'):setup(state,tmp_path,cells,[[10,10,7],[20,20,6]],[edge])
    assert state.game_proxy._inject_queue.empty()


@pytest.mark.parametrize('already_open',[False,True])
def test_normal_door_is_opened_once_then_walked_through(state,tmp_path,already_open):
    edge=dict(origin=[10,10,7],entry=[11,10,7],exit=[12,10,7],action='use_then_step',item_id=6264,evidence='observed')
    door=cell(11,10,kind='blocked',item=6264,navigation=[dict(kind='door',door_state_hint='closed')])
    change={**edge,'object_change':{'closed_id':6264,'open_id':6265,'behavior':'open_then_step'}}
    if already_open:
        door['kind']='clear';door['items'][0]['id']=6265;door['navigation'][0]['door_state_hint']='open'
    c,_,_=setup(state,tmp_path,[cell(10,10),door,cell(12,10)],[[10,10,7],[12,10,7]],[edge,change]);sent=[]
    async def use_or_step(payload,guard=None):
        assert guard();sent.append(payload[0]);c.last_command=0
        if payload[0]==0x82:
            door['kind']='clear';door['items'][0]['id']=6265;door['navigation'][0]['door_state_hint']='open'
        else:
            assert payload[0]==0x66
            state.game_state.position=(state.game_state.position[0]+1,10,7)
    state.game_proxy.inject_to_server=use_or_step;asyncio.run(c.run())
    assert c.status=='complete' and sent==([0x66,0x66] if already_open else [0x82,0x66,0x66])


def test_observed_walk_through_open_door_has_two_steps_not_a_teleport(state,tmp_path):
    edge=dict(origin=[10,10,7],entry=[11,10,7],exit=[12,10,7],action='step',item_id=None,entry_items=[6265],evidence='observed')
    door=cell(11,10,item=6265,navigation=[dict(kind='door',door_state_hint='open')])
    c,_,_=setup(state,tmp_path,[cell(10,10),door,cell(12,10)],[[10,10,7],[12,10,7]],[edge])
    sent=connect_fake_walk(state,c);asyncio.run(c.run())
    assert c.status=='complete' and len(sent)==2


def test_explicit_control_rejoins_known_same_floor_path_and_retains_stop_progress(state,tmp_path):
    async def exercise():
        c,_,draft=setup(state,tmp_path)
        assert not state._action_tasks and state.game_proxy._inject_queue.empty()
        result=await server._async_adaptive_control('start',dict(scope='a'*64,draft_id=draft,loop=False,min_hp=30))
        assert result['total']==4 and 'adaptive_cavebot' in state._action_tasks
        await asyncio.sleep(0)  # allow runner to register and reach its first await
        await server._async_adaptive_control('pause')
        assert state.adaptive_cavebot.status=='paused'
        state.game_state.position=(10,11,7)
        result=await server._async_adaptive_control('resume')
        assert result['position']==[10,11,7] and result['waypoint']['current']==1
        await server._async_adaptive_control('stop')
        assert state.adaptive_cavebot.path and state.adaptive_cavebot.snapshot()['resumable']
        assert not state.settings['actions']['adaptive_cavebot']['enabled']
    asyncio.run(exercise())


def test_adaptive_http_control_requires_token_origin_and_valid_command(state,monkeypatch):
    from http.client import HTTPConnection
    from http.server import HTTPServer
    import sys,threading,json
    from dbv.mcp_server import dashboard_api as api
    from dbv.mcp_server.dashboard_security import SESSION_TOKEN,DASHBOARD_ORIGIN
    async def exercise():
        monkeypatch.setattr(api,'_state',state);monkeypatch.setattr(api,'_main_loop',asyncio.get_running_loop())
        monkeypatch.setattr(sys.modules['__main__'],'_async_adaptive_control',server._async_adaptive_control,raising=False)
        http=HTTPServer(('127.0.0.1',0),api._Handler);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        def request(body,token=True,origin=DASHBOARD_ORIGIN):
            conn=HTTPConnection('127.0.0.1',http.server_port,timeout=3)
            try:
                headers={'Origin':origin}
                if token:headers['Authorization']='Bearer '+SESSION_TOKEN
                conn.request('POST','/api/adaptive/control',json.dumps(body),headers)
                response=conn.getresponse();response.read();return response.status
            finally:conn.close()
        try:
            assert await asyncio.to_thread(request,{'command':'stop'},False)==401
            assert await asyncio.to_thread(request,{'command':'stop'},True,'https://evil.example')==403
            assert await asyncio.to_thread(request,{'command':'invalid'})==400
            assert await asyncio.to_thread(request,[])==400
            assert await asyncio.to_thread(request,{'command':'start'})==400
            assert await asyncio.to_thread(request,{'command':'stop'})==200
            assert state.game_proxy is None and not state._action_tasks
        finally:
            await asyncio.to_thread(http.shutdown);http.server_close();thread.join(timeout=2)
    asyncio.run(exercise())


def test_invalid_guard_does_not_break_proxy_injection_worker():
    async def exercise():
        p=proxy()
        await p.inject_to_server(b'blocked',guard=lambda:False)
        def broken():raise ValueError('synthetic guard failure')
        await p.inject_to_server(b'broken',guard=broken)
        await p.inject_to_server(b'valid')
        task=asyncio.create_task(p._process_inject_queue())
        await asyncio.sleep(.01);task.cancel();await asyncio.gather(task,return_exceptions=True)
        assert [call.args[0] for call in p.server_writer.write.call_args_list]==[b'valid']
    asyncio.run(exercise())


def test_generic_v2_toggle_cannot_launch_navigation(state,monkeypatch):
    assert asyncio.run(server._async_toggle_action('adaptive_cavebot',True)).startswith('Error:')
    assert 'Cavebot' in asyncio.run(server._async_restart_action('adaptive_cavebot'))
    state.settings['actions']={n:{'enabled':True} for n in ('adaptive_cavebot','eat_food')}
    calls=[];monkeypatch.setattr(server,'_start_action',lambda name:calls.append(name))
    server._start_all_enabled_actions()
    assert set(calls)=={'dll_bridge','eat_food'}
    assert not state.settings['actions']['adaptive_cavebot']['enabled']


def test_emergency_invalidates_v2_while_manual_pause_can_resume(state,tmp_path):
    async def exercise():
        c,_,_=setup(state,tmp_path);state.settings['actions']['adaptive_cavebot']={'enabled':True}
        await server._async_pause_automation()
        assert c.status=='paused' and c.path
        await server._async_emergency_stop()
        assert c.status=='idle' and not c.path
        await server._async_unlock_automation()
        assert not state._action_tasks and not state.settings['actions']['adaptive_cavebot']['enabled']
    asyncio.run(exercise())


def test_existing_draft_migrates_without_losing_points_and_persists_loop(tmp_path):
    path=tmp_path/('map_atlas-'+('a'*16)+'.sqlite3')
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE drafts (id TEXT PRIMARY KEY,name TEXT,points TEXT,updated REAL)')
        db.execute('INSERT INTO drafts VALUES (?,?,?,?)',('old','Old','[[10,10,7],[20,20,6]]',0))
    atlas=MapAtlas(tmp_path,'a'*64);d=atlas.drafts()[0]
    assert d['id']=='old' and d['loop'] is True
    atlas.save_draft(d['name'],d['points'],d['id'],False)
    assert MapAtlas(tmp_path,'a'*64).drafts()[0]['loop'] is False


def test_autowalk_cannot_compete_with_active_v2(state):
    async def exercise():
        task=asyncio.create_task(asyncio.sleep(100));state._action_tasks['adaptive_cavebot']=task
        try:
            assert 'Zatrzymaj' in await server.autowalk('n')
            assert state._auto_task is None
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
    asyncio.run(exercise())


def test_acknowledged_walking_does_not_inherit_item_use_delay(state,tmp_path,monkeypatch):
    from dbv.brain.cavebot import adaptive_cavebot as module
    c,_,_=setup(state,tmp_path)
    clock=[100.0];sleeps=[];positions=[]
    monkeypatch.setattr(module,'time',SimpleNamespace(monotonic=lambda:clock[0],time=time.time))
    async def sleep(seconds):sleeps.append(seconds);clock[0]+=seconds
    monkeypatch.setattr(module,'asyncio',SimpleNamespace(sleep=sleep,CancelledError=asyncio.CancelledError))
    async def ack(payload,guard=None):
        assert guard()
        positions.append(state.game_state.position)
        x,y,z=state.game_state.position;state.game_state.position=(x+1,y,z)
        clock[0]+=.025  # fresh client position after an acknowledged step
    state.game_proxy.inject_to_server=ack
    asyncio.run(c.run())
    assert c.status=='complete' and positions==[(10,10,7),(11,10,7),(12,10,7)]
    assert sum(sleeps)<.1  # old fixed pacing added >350 ms to these three steps
    c.status='running';c.last_command=clock[0]
    asyncio.run(c.send(b'\x82'))
    assert sleeps[-1]==pytest.approx(.2)  # interaction pacing is preserved


def test_automatic_loop_return_is_visible_in_preview_before_execution(state,tmp_path):
    c,_,draft=setup(state,tmp_path)
    points=[[10,10,7],[13,10,7]]
    atlas=state.passive_map_monitor.atlas
    preview=atlas.plan(points,[],loop=True)
    assert preview['complete'] and preview['loop_closed'] and preview['return_mode']=='direct'
    assert preview['path'][-1]==points[0] and len(preview['path'])==7
    assert preview['legs'][-1]['returning'] and preview['return_start']==3
    c.prepare(atlas.scope,draft,True)
    assert c.path==preview['path']
    closed=atlas.plan(points+[points[0]],[],loop=True)
    assert closed['loop_closed'] and closed['path'][-1]==points[0]
    assert closed['legs'][1]==dict(from_index=1,to_index=2,start=3,end=6)
    assert state.game_proxy._inject_queue.empty()


def test_live_blocked_ordinary_waypoint_is_skipped_by_local_detour(state,tmp_path):
    c,cells,_=setup(state,tmp_path,points=[[10,10,7],[11,10,7],[13,10,7]])
    next(v for v in cells if (v['x'],v['y'])==(11,10)).update(occupants=1,occupant_types={'player':1})
    sent=connect_fake_walk(state,c);asyncio.run(c.run())
    assert c.status=='complete' and state.game_state.position==(13,10,7)
    assert [11,10,7] not in c.path and sent[0]==b'\x6b'


def test_occupied_final_waypoint_waits_instead_of_claiming_completion(state,tmp_path):
    c,cells,_=setup(state,tmp_path)
    next(v for v in cells if (v['x'],v['y'])==(13,10)).update(occupants=1,occupant_types={'monster':1})
    sent=connect_fake_walk(state,c)
    async def scenario():
        task=asyncio.create_task(c.run())
        await asyncio.sleep(.25)
        assert c.status=='running' and c.blockage['occupants']=={'monster':1}
        assert state.game_state.position!=(13,10,7) and c.laps==0
        next(v for v in cells if (v['x'],v['y'])==(13,10)).update(occupants=0,occupant_types={})
        await asyncio.wait_for(task,1)
        assert c.status=='complete' and c.laps==1
    asyncio.run(scenario())


def test_distant_start_plans_approach_once_then_loops_only_main_route(state,tmp_path,monkeypatch):
    c,_,draft=setup(state,tmp_path,cells=[cell(x,y) for x in range(10,19) for y in range(10,13)],points=[[16,10,7],[18,10,7],[16,10,7]])
    state.game_state.position=(10,10,7)
    c.prepare('a'*64,draft,True)
    assert c.loop_start==6 and c.path[c.loop_start]==[16,10,7]
    assert c.snapshot()['approach_remaining']==6
    # This test counts exact steps. Bounded path variations have separate coverage.
    monkeypatch.setattr(c,'vary_leg',lambda sample:False)
    sent=connect_fake_walk(state,c);inject=state.game_proxy.inject_to_server
    positions=[]
    async def bounded(payload,guard=None):
        await inject(payload,guard);positions.append(state.game_state.position)
        if len(sent)==15:c.pause('bounded test')
    state.game_proxy.inject_to_server=bounded;asyncio.run(c.run())
    assert c.laps==2 and all(p[0]>=16 for p in positions[5:])


def test_approach_does_not_choose_stairs_without_an_explicit_route_link(state,tmp_path):
    edge=stair_edge();cells=[cell(10,10),cell(10,9,kind='blocked',item=1956),cell(20,20,6),cell(21,20,6)]
    c,_,draft=setup(state,tmp_path,cells,[[20,20,6],[21,20,6]],[edge])
    state.game_state.position=(10,10,7)
    with pytest.raises(ValueError,match='Brak znanego dojścia'):c.prepare('a'*64,draft,False)
    assert state.game_proxy._inject_queue.empty()


def test_approach_rejects_unknown_path_and_stale_local_map(state,tmp_path):
    c,_,draft=setup(state,tmp_path)
    state.game_state.position=(50,50,7)
    with pytest.raises(ValueError,match='Brak znanego dojścia'):c.prepare('a'*64,draft,False)
    state.game_state.position=(10,10,7)
    old=state.passive_map_monitor.snapshot
    state.passive_map_monitor.snapshot=lambda:{**old(),'stale':True}
    with pytest.raises(ValueError,match='świeżej mapy'):c.prepare('a'*64,draft,False)
    assert state.game_proxy._inject_queue.empty()
