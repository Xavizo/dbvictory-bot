"""Recorded movement regressions. Synthetic atlas/transport only, never a client."""
import asyncio
import json
import time
import pytest
from dbv.mcp_server import dashboard_api as api
from dbv.brain.navigation.map_atlas import MapAtlas,trace_links,recording_points
from dbv.brain.cavebot.adaptive_cavebot import step_clear,detour
from dbv.client_bridge.protocol import ClientOpcode,PacketReader
from dbv.mcp_server.dashboard_security import DASHBOARD_ORIGIN,SESSION_TOKEN
from test_adaptive_cavebot import cell,setup,connect_fake_walk
from test_automation_safety import state
from test_dashboard_security import http_api
from test_v2_combat import armed,monster,configure_rules


@pytest.mark.parametrize('opened',[False,True])
def test_recorded_open_and_dug_hole_are_one_conditional_transition(tmp_path,opened):
    a,entry,b=[10,11,7],[10,10,7],[10,10,8]
    dig=dict(id='dig',origin=a,entry=entry,exit=b,action='use_with_then_step',item_id=593,prepared_item_id=594,tool_id=3457,evidence='observed')
    aio={**dig,'id':'aio','tool_id':8900}
    step=dict(id='step',origin=a,entry=entry,exit=b,action='step',entry_items=[594],evidence='observed')
    atlas=MapAtlas(tmp_path,'f'*64)
    atlas.observe(dict(status='ready',cells=[cell(*a),cell(*entry,item=594 if opened else 593,kind='blocked',navigation=[dict(kind='hole')]),cell(*b)]))
    links=trace_links([a,b],[dig,step,aio],[a,b])
    assert links==[dict(from_index=0,id='aio')]
    plan=atlas.plan([a,b],[dig,step,aio],links=links)
    assert plan['complete'] and plan['transitions'][0]['tool_ids']==[3457,8900]
    assert not trace_links([a,b],[dig,{**step,'entry_items':[999]}],[a,b])
    assert not trace_links([a,b],[dig,{**aio,'prepared_item_id':999},step],[a,b])


def test_diagonal_recording_plans_saves_and_executes_between_blocked_sides(state,tmp_path):
    points=[[10,10,7],[11,11,7]]
    cells=[cell(*points[0]),cell(11,10,kind='blocked'),cell(10,11,kind='blocked'),cell(*points[1])]
    c,_,draft=setup(state,tmp_path,cells,points)
    p=state.passive_map_monitor.atlas.plan(points,[],loop=True)
    assert p['complete'] and p['loop_closed'] and p['path']==[points[0],points[1],points[0]]
    assert step_clear(cells,*points) and detour(cells,*points)==points
    sent=connect_fake_walk(state,c);asyncio.run(c.run())
    assert c.status=='complete' and sent[0][0]==0x6b
    for danger in ['blocked','unknown','occupied','hole']:
        changed=[dict(t) for t in cells];target=changed[-1]
        if danger=='unknown':changed.pop()
        elif danger=='occupied':target.update(occupants=1,occupant_types={'player':1})
        elif danger=='hole':target['navigation']=[dict(kind='hole')]
        else:target['kind']='blocked'
        assert not step_clear(changed,*points) and detour(changed,*points) is None


def test_return_is_planned_without_two_square_limit_and_start_uses_nearest_waypoint(state,tmp_path):
    cells=[cell(x,10) for x in range(10,21)];points=[[10,10,7],[15,10,7],[20,10,7]]
    c,_,draft=setup(state,tmp_path,cells,points)
    atlas=state.passive_map_monitor.atlas
    p=atlas.plan(points,[],loop=True)
    assert p['complete'] and p['loop_closed'] and p['return_mode']=='direct' and p['path'][-1]==points[0]
    state.game_state.position=(16,10,7);c.prepare(c.scope,draft,True)
    assert c.path[:3]==[[16,10,7],[15,10,7],[16,10,7]] and c.path[-1]==[15,10,7] and c.loop_start==1
    assert atlas.drafts()[0]['points']==points and state.game_proxy._inject_queue.empty()


def test_nearest_waypoint_must_be_reachable_without_inventing_a_teleport(state,tmp_path):
    a,b=[13,10,7],[20,10,7]
    edge=dict(id='tp',origin=a,entry=a,exit=b,action='use',item_id=351,evidence='observed')
    cells=[cell(*a),cell(*b),cell(10,10),cell(10,11),*[cell(x,12) for x in range(10,21)],cell(20,11)]
    c,_,draft=setup(state,tmp_path,cells,[a,b],edges=[edge])
    state.game_state.position=(10,10,7);c.prepare(c.scope,draft,False)
    assert c.path[-1]==b and a not in c.path and c.reason.endswith('2.')


def test_diagonal_cycle_can_join_middle_waypoint_and_repeat(state,tmp_path):
    points=[[10,10,7],[12,12,7],[14,14,7]]
    cells=[cell(x,x) for x in range(10,15)]
    c,_,draft=setup(state,tmp_path,cells,points)
    state.game_state.position=(12,12,7);c.prepare(c.scope,draft,True)
    assert c.path[0]==points[1] and c.path[-1]==points[1]
    sent=connect_fake_walk(state,c);inject=state.game_proxy.inject_to_server
    async def stop_after_lap(payload,guard=None):
        await inject(payload,guard)
        if len(sent)==8:c.pause('test: one full cycle')
    state.game_proxy.inject_to_server=stop_after_lap
    asyncio.run(c.run())
    assert state.game_state.position==(12,12,7) and len(sent)==8
    assert [p[0] for p in sent]==[0x6b,0x6b,0x6d,0x6d,0x6d,0x6d,0x6b,0x6b]


def test_reverse_route_requires_independently_observed_return_transitions(tmp_path):
    a,b,c=[10,10,7],[20,20,6],[30,30,5]
    def edge(i,origin,dest):return dict(id=i,origin=origin,entry=origin,exit=dest,action='use',item_id=1948,evidence='observed')
    rows=[edge('up1',a,b),edge('up2',b,c),edge('down2',c,b),edge('down1',b,a)]
    for row in rows[2:]:row.update(entry=[row['origin'][0]+1,row['origin'][1],row['origin'][2]],action='step',item_id=432)
    atlas=MapAtlas(tmp_path,'d'*64)
    atlas.observe(dict(status='ready',cells=[cell(*p,item=1948,navigation=[dict(kind='ladder')]) for p in (a,b,c)]+[cell(*r['entry'],item=432,navigation=[dict(kind='hole')]) for r in rows[2:]]))
    links=[dict(from_index=0,id='up1'),dict(from_index=1,id='up2')]
    p=atlas.plan([a,b,c],rows,links=links,loop=True)
    assert p['complete'] and p['loop_closed'] and p['return_mode']=='reverse'
    assert p['path']==[a,b,c,b,a] and [r['id'] for r in p['transitions']]==['up1','up2','down2','down1']
    p=atlas.plan([a,b,c],rows[:-1],links=links,loop=True)
    assert not p['complete'] and not p['loop_closed'] and p['return_mode']=='unavailable'
    assert p['path']==[a,b,c] and 'osobno zaobserwowany' in p['return_error']


def test_optimization_reports_concrete_gap_and_never_changes_saved_draft(http_api,state,tmp_path,monkeypatch):
    c,cells,draft=setup(state,tmp_path,cells=[cell(10,10),cell(11,10)],points=[[10,10,7],[11,10,7]])
    atlas=state.passive_map_monitor.atlas;before=atlas.drafts()
    atlas.observe(dict(status='ready',cells=[cell(11,10,kind='blocked')]))
    monkeypatch.setattr(api,'_state',state);request,_=http_api
    status,_,raw=request('POST','/api/map/plan',json.dumps(dict(scope=c.scope,points=before[0]['points'],optimize=True)),Origin=DASHBOARD_ORIGIN,Authorization=f'Bearer {SESSION_TOKEN}')
    p=json.loads(raw)
    assert status==200 and p['optimization_blocked'] and not p['complete']
    assert p['gaps'][0]['to']==[11,10,7] and 'zablokowane' in p['gaps'][0]['reason']
    assert atlas.drafts()==before and state.game_proxy._inject_queue.empty()


def test_lure_blocker_temporarily_overrides_priority_and_resumes_after_clear(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure',lure_distance=3)
    configure_rules(c,[dict(pattern='Wolf Boss',priority=100),dict(pattern='Wolf',priority=10)])
    blocker,br,bt=monster(state,cells,x=11,y=10,cid=0x40000001)
    boss,sr,st=monster(state,cells,name='Wolf Boss',x=12,y=11,cid=0x40000002)
    c.status='running';sent=[]
    async def inject(payload,guard=None):assert guard();sent.append(payload);c.last_command=0
    state.game_proxy.inject_to_server=inject
    async def scenario():
        assert await c.combat.tick(state.passive_map_monitor.snapshot())
        assert c.combat.sent_target==blocker and c.combat.phase=='clear_path' and c.combat.battle_started is None
        br['health']=0;bt.update(occupants=0,occupant_types={});sr['t']=time.time()
        assert not await c.combat.tick(state.passive_map_monitor.snapshot())
        assert c.combat.sent_target==boss and c.combat.phase=='lure' and c.combat.blocker_id==0
    asyncio.run(scenario())
    assert all(p[0]==ClientOpcode.ATTACK for p in sent) and state.game_state.position==(10,10,7)


def test_moving_blocker_invalidates_queued_attack_and_restores_lure(state,tmp_path):
    c,cells=armed(state,tmp_path,mode='lure');c.status='running'
    cid,row,tile=monster(state,cells,x=11,y=10)
    captured=[]
    async def queued(payload,guard=None):assert guard();captured.append(guard);c.last_command=0
    state.game_proxy.inject_to_server=queued
    async def scenario():
        assert await c.combat.tick(state.passive_map_monitor.snapshot())
        tile.update(occupants=0,occupant_types={});row.update(y=11,t=time.time())
        next(t for t in cells if t['x']==11 and t['y']==11).update(occupants=1,occupant_types={'monster':1})
        assert not captured[0]()
        assert not await c.combat.tick(state.passive_map_monitor.snapshot())
        assert c.combat.phase=='lure' and c.combat.blocker_id==0
    asyncio.run(scenario())


@pytest.mark.parametrize('excluded',['ignore','npc','player','stale','manual','disabled'])
def test_blocker_never_overrides_target_exclusions_or_manual_choice(state,tmp_path,excluded):
    c,cells=armed(state,tmp_path,mode='lure');c.status='running'
    cid,row,tile=monster(state,cells,x=11,y=10)
    if excluded=='ignore':configure_rules(c,[dict(pattern='Wolf',action='ignore'),dict(pattern='Other',action='attack')])
    elif excluded in {'npc','player'}:tile['occupant_types']={excluded:1}
    elif excluded=='stale':row['t']-=2
    elif excluded=='manual':c.combat.accept_manual_target(0x40000002)
    else:c.combat.config['enabled']=False
    asyncio.run(c.combat.tick(state.passive_map_monitor.snapshot()))
    assert state.game_proxy._inject_queue.empty() and c.combat.phase!='clear_path'
