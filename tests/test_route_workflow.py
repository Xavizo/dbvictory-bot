"""Recording -> optional optimization -> saved loop -> door/up/down execution.

Only synthetic state and acknowledged fake transport; never attaches to a game.
"""
import asyncio
import json
import time
import pytest
from types import SimpleNamespace

import cavebot
import dashboard_api as api
from adaptive_cavebot import AdaptiveCavebot,OFFSETS
from dashboard_security import DASHBOARD_ORIGIN,SESSION_TOKEN
from map_atlas import MapAtlas,recording_points,review_recorded_actions
from protocol import WALK_OPCODES,ClientOpcode
from test_adaptive_cavebot import cell,setup
from test_automation_safety import state,proxy
from test_dashboard_security import http_api


def test_record_review_reject_save_reopen_and_cross_door_up_down(http_api,state,tmp_path,monkeypatch):
    door=dict(origin=[11,10,7],entry=[12,10,7],exit=[13,10,7],action='use_then_step',item_id=6264,evidence='observed')
    up=dict(origin=[14,10,7],entry=[15,10,7],exit=[20,20,6],action='step',item_id=1956,evidence='observed')
    down=dict(origin=[21,21,6],entry=[21,22,6],exit=[14,11,7],action='step',item_id=1957,evidence='observed')
    edges=[door,up,down]
    for i,e in enumerate(edges):e["id"]=f"edge-{i}"
    cells=[cell(x,y) for x in range(10,16) for y in range(10,12)]+[cell(x,y,6) for x in range(20,23) for y in range(20,23)]
    for edge in edges:
        tile=next(t for t in cells if [t['x'],t['y'],t['z']]==edge['entry'])
        tile.update(kind='blocked',items=[dict(id=edge['item_id'],stack=1,flags=['not_walkable'])],navigation=[dict(kind='door' if edge is door else 'stairs',item_id=edge['item_id'],source='observed',door_state_hint='closed' if edge is door else 'unknown')])
    c,_,_=setup(state,tmp_path/'atlas',cells,[[10,10,7],[11,10,7]],edges)
    monkeypatch.setattr(api,'_state',state);monkeypatch.setattr(api,'_main_loop',object())
    monkeypatch.setattr(cavebot,'RECORDINGS_DIR',tmp_path/'recordings')
    request,_=http_api
    def post(path,body):
        status,_,data=request('POST',path,json.dumps(body),Origin=DASHBOARD_ORIGIN,Authorization=f'Bearer {SESSION_TOKEN}')
        result=json.loads(data);assert status==200,result
        return result
    post('/api/cavebot/record/start',{'name':'synthetic_doors_floors'})
    trace=[[10,10,7],[11,10,7],[12,10,7],[13,10,7],[14,10,7],[20,20,6],[21,20,6],[21,21,6],[14,11,7],[13,11,7],[12,11,7],[11,11,7],[10,11,7],[10,10,7]]
    for p in trace:
        state.game_state.position=tuple(p);cavebot._sample_recording_position(state)
    post('/api/cavebot/record/stop',{'discard':False})
    assert not state.recording_active and not state._action_tasks and state.game_proxy._inject_queue.empty()
    saved_recording=cavebot.load_recording('synthetic_doors_floors')
    assert [p['pos'] for p in saved_recording['observed_path']]==trace
    body={'scope':c.scope,'recording':'synthetic_doors_floors','loop':True}
    original=post('/api/map/plan',body)
    normalized=[p for i,p in enumerate(trace) if i!=2]
    assert original['complete'] and original['path']==normalized
    assert [t['action'] for t in original['transitions']]==['use_then_step','step','step']
    proposal=post('/api/map/plan',{**body,'optimize':True})
    assert proposal['complete'] and len(proposal['points'])<=len(original['points'])
    assert [t['action'] for t in proposal['transitions']]==['use_then_step','step','step']
    # Reject the proposal: persist the unchanged original, not the candidate.
    result=post('/api/map/drafts',{'scope':c.scope,'name':'Door + two floors','points':original['points'],'links':original['links'],'loop':True})
    draft_id=result['saved_id']
    atlas=MapAtlas(tmp_path/'atlas',c.scope)
    draft=next(d for d in atlas.drafts() if d['id']==draft_id)
    assert draft['points']==original['points'] and not draft['combat']['enabled']
    assert cavebot.load_recording('synthetic_doors_floors')==saved_recording
    reopened=post('/api/map/plan',{'scope':c.scope,'points':draft['points'],'links':draft['links'],'loop':True})
    assert reopened['path']==normalized
    # Exercise the exact stored route against a fake server for one lap.
    c.prepare(c.scope,draft_id,False);sent=[];visited=[list(state.game_state.position)]
    directions={int(WALK_OPCODES[d]):delta for delta,d in OFFSETS.items()}
    async def inject(payload,guard=None):
        assert guard();sent.append(payload)
        pos=list(state.game_state.position)
        if payload[0]==ClientOpcode.USE_ITEM:
            assert pos==door['origin']
            tile=next(t for t in cells if [t['x'],t['y'],t['z']]==door['entry'])
            tile['kind']='clear';tile['navigation'][0]['door_state_hint']='open'
        else:
            dx,dy=directions[payload[0]]
            edge=next((e for e in (up,down) if e['origin']==pos and e['entry']==[pos[0]+dx,pos[1]+dy,pos[2]]),None)
            state.game_state.position=tuple(edge['exit'] if edge else [pos[0]+dx,pos[1]+dy,pos[2]])
            visited.append(list(state.game_state.position))
        c.last_command=0;state.game_state.stats_updated_at=time.time()
    state.game_proxy.inject_to_server=inject
    asyncio.run(c.run())
    assert c.status=='complete' and state.game_state.position==(10,10,7)
    assert visited==trace
    assert sum(p[0]==ClientOpcode.USE_ITEM for p in sent)==1
    assert not any(p[0] in {ClientOpcode.ATTACK,ClientOpcode.SAY} for p in sent)


def test_unoptimized_wiggle_keeps_every_turn_and_backtrack(tmp_path):
    path=[[10,10,7],[11,10,7],[11,11,7],[12,11,7],[12,10,7],[13,10,7],[12,10,7],[11,10,7],[10,10,7]]
    points,original=recording_points({'observed_path':[{'pos':p} for p in path]},optimize=False)
    atlas=MapAtlas(tmp_path,'c'*64)
    atlas.observe({'status':'ready','cells':[cell(x,y) for x in range(10,14) for y in range(10,12)]})
    assert atlas.plan(points,[])['path']==path
    assert len(points)<len(path) and original==path


def test_two_editor_windows_cannot_silently_overwrite_a_newer_sketch(tmp_path,monkeypatch):
    atlas=MapAtlas(tmp_path,'d'*64);points=[[10,10,7],[11,10,7]]
    draft=atlas.save_draft('one',points);before=atlas.drafts()[0]['updated']
    monkeypatch.setattr('map_atlas.time.time',lambda:before+1)
    atlas.save_draft('newer',points,draft,False,expected_updated=before)
    with pytest.raises(ValueError,match='innym oknie'):
        atlas.save_draft('stale',[[10,10,7],[12,10,7]],draft,True,expected_updated=before)
    assert atlas.drafts()[0]['name']=='newer'


def test_can_edit_tags_but_not_delete_running_route(http_api, state, tmp_path, monkeypatch):
    controller, _, draft = setup(state, tmp_path)
    controller.status = 'running'
    monkeypatch.setattr(api, '_state', state)
    request, _ = http_api
    headers = {'Origin':DASHBOARD_ORIGIN, 'Authorization':f'Bearer {SESSION_TOKEN}'}
    def post(**data):
        return request('POST','/api/map/drafts', json.dumps({'id':draft, 'scope':controller.scope, **data}), **headers)
    tags = [{'text':'Waste 25k','color':'rose'}]
    status, _, raw = post(operation='set_tags', tags=tags, previous=[])
    assert status == 200 and json.loads(raw)['drafts'][0]['tags'] == tags
    assert post(operation='delete')[0] == 400
    assert post(operation='set_tags', tags=[{'text':'', 'color':'rose'}], previous=tags)[0] == 400
    assert state.passive_map_monitor.atlas.drafts()[0]['tags'] == tags


def test_save_running_sketch_isolated_until_next_start(http_api, state, tmp_path, monkeypatch):
    controller, _, draft_id = setup(state, tmp_path)
    controller.status = 'running'
    before = controller.waypoint_progress()
    original = [point.copy() for point in controller.path]
    monkeypatch.setattr(api, '_state', state)
    request, _ = http_api
    headers = {'Origin': DASHBOARD_ORIGIN, 'Authorization': f'Bearer {SESSION_TOKEN}'}
    data = dict(scope=controller.scope, id=draft_id, name='Edited route', loop=False,
                points=[[10,10,7], [10,12,7]])
    def save(**changes):
        return request('POST', '/api/map/drafts', json.dumps({**data, **changes}), **headers)

    assert save()[0] == 400
    assert save(expected_updated=before['draft_updated'] - 1)[0] == 400
    status, _, body = save(expected_updated=before['draft_updated'])
    assert status == 200
    stored = json.loads(body)['drafts'][0]
    assert stored['navigation_revision'] != before['navigation_revision']
    assert stored['points'] == data['points']
    assert controller.status == 'running' and controller.path == original
    assert controller.waypoint_progress() == before
    controller.pause('test')
    resumed = asyncio.run(controller.resume_candidate())
    assert resumed.path == original and resumed.waypoint_progress() == before
    controller.stop(preserve_progress=True)
    fresh = AdaptiveCavebot(state)
    asyncio.run(fresh.prepare_async(controller.scope, draft_id, False))
    assert fresh.path[-1] == data['points'][-1]
    assert fresh.waypoint_progress()['navigation_revision'] == stored['navigation_revision']
    assert state.game_proxy._inject_queue.empty()


def test_draft_tags_survive_geometry_edits_and_conflicting_windows(tmp_path):
    atlas = MapAtlas(tmp_path, 'e'*64)
    points = [[10,10,7], [11,10,7]]
    tags = [{'text': '12M XP/h', 'color': 'mint'}]
    draft = atlas.save_draft('Tsuful', points, tags=tags)
    before = atlas.drafts()[0]
    updated = [*tags, {'text': 'Lv 390', 'color': 'blue'}]
    atlas.set_tags(draft, updated, tags)
    with pytest.raises(ValueError, match='innym oknie'):
        atlas.set_tags(draft, [], tags)
    assert atlas.drafts()[0] == {**before, 'tags': updated}
    atlas.save_draft('Tsuful 10', points, draft, expected_updated=before['updated'])
    atlas.change_draft(draft,'delete')
    atlas.change_draft(draft,'restore')
    reopened = MapAtlas(tmp_path, 'e'*64)
    assert reopened.drafts()[0]['tags'] == updated
    reopened.set_tags(draft, [], updated)
    assert reopened.drafts()[0]['tags'] == []


def test_labels_survive_reopen_combat_changes_and_delete_restore(tmp_path):
    atlas=MapAtlas(tmp_path,'e'*64);points=[[10,10,7],[11,10,7],[12,10,7]]
    draft=atlas.save_draft('Labels',points,labels=[' Start ','Brama 320','Powrót'])
    atlas.change_draft(draft,'configure',combat={'enabled':False,'rules':[]},loop=False)
    atlas.change_draft(draft,'delete');atlas.change_draft(draft,'restore')
    row=MapAtlas(tmp_path,'e'*64).drafts()[0]
    assert row['labels']==['Start','Brama 320','Powrót'] and not row['loop']


@pytest.mark.parametrize('labels',[['too few'],['a','b',12],['a','b','x'*61],['a','b','new\nline']])
def test_invalid_labels_do_not_overwrite_saved_draft(tmp_path,labels):
    atlas=MapAtlas(tmp_path,'f'*64);points=[[10,10,7],[11,10,7],[12,10,7]]
    draft=atlas.save_draft('Keep',points,labels=['A','B','C'])
    with pytest.raises(ValueError,match='Etykiety'):
        atlas.save_draft('Bad',points,draft,labels=labels)
    assert atlas.drafts()[0]['labels']==['A','B','C']


def test_optional_optimization_keeps_named_stops(http_api,state,tmp_path,monkeypatch):
    c,_,_=setup(state,tmp_path,[cell(x,10) for x in range(10,18)],[[10,10,7],[17,10,7]])
    monkeypatch.setattr(api,'_state',state)
    request,_=http_api
    points=[[10,10,7],[13,10,7],[17,10,7]]
    status,_,raw=request('POST','/api/map/plan',json.dumps({'scope':c.scope,'points':points,'labels':['','Door',''],'optimize':True}),Origin=DASHBOARD_ORIGIN,Authorization=f'Bearer {SESSION_TOKEN}')
    result=json.loads(raw)
    assert status==200 and result['complete'] and [13,10,7] in result['points']


def test_recorded_ladder_import_is_unsaved_until_explicit_save(http_api,state,tmp_path,monkeypatch):
    a,b=[10,10,7],[10,11,6]
    up=dict(id='a',origin=a,entry=a,exit=b,action='use',item_id=1948,evidence='observed',entry_items=[1948])
    duplicate={**up,'id':'b','entry_items':[1948,8479]}
    down=dict(id='down',origin=b,entry=[11,11,6],exit=[11,10,7],action='step',item_id=432,evidence='observed')
    cells=[{**cell(*a),'items':[dict(id=1948,stack=1,flags=[])],'navigation':[dict(kind='ladder')]},cell(*b),{**cell(11,11,6),'items':[dict(id=432,stack=0,flags=[])],'navigation':[dict(kind='hole')]},cell(11,10,7)]
    c,_,_=setup(state,tmp_path/'atlas',cells,[a,[11,10,7]])
    state.passive_map_monitor.learner.records={r['id']:r for r in [up,duplicate,down]}
    monkeypatch.setattr(api,'_state',state)
    recordings=tmp_path/'recordings';recordings.mkdir()
    monkeypatch.setattr(cavebot,'RECORDINGS_DIR',recordings)
    use=dict(type='use_item',item_id=1948,x=10,y=10,z=7,player_pos=a,t=1)
    recording=dict(name='ladder_loot',version=2,created_at='2026-01-01T00:00:00+00:00',start_position=a,
                   observed_path=[dict(pos=a,t=0),dict(pos=b,t=2),dict(pos=[11,10,7],t=3)],waypoints=[use])
    (recordings/'ladder_loot.json').write_text(json.dumps(recording))
    request,_=http_api
    def post(path,body):
        status,_,raw=request('POST',path,json.dumps(body),Origin=DASHBOARD_ORIGIN,Authorization=f'Bearer {SESSION_TOKEN}')
        data=json.loads(raw);assert status==200,data;return data
    before=state.passive_map_monitor.atlas.drafts()
    plan=post('/api/map/plan',dict(scope=c.scope,recording='ladder_loot',loop=True))
    assert plan['complete'] and plan['loop_closed'] and len(plan['links'])==2
    assert not plan['requires_action_review'] and plan['unmatched_recorded_actions']==0
    assert plan['recorded_actions'][0]['matched_transition']==0
    assert state.passive_map_monitor.atlas.drafts()==before
    saved=post('/api/map/drafts',dict(scope=c.scope,name='Chosen route',points=plan['points'],links=plan['links'],loop=True))
    row=next(d for d in saved['drafts'] if d['id']==saved['saved_id'])
    assert row['points']==plan['points'] and row['links']==plan['links']
    assert not state._action_tasks and state.game_proxy._inject_queue.empty()
    # An extra unknown use must still be reviewed, even in a complete route.
    recording['waypoints'].append({**use,'item_id':9999,'t':1.5})
    (recordings/'ladder_loot.json').write_text(json.dumps(recording))
    plan=post('/api/map/plan',dict(scope=c.scope,recording='ladder_loot',loop=True))
    assert plan['complete'] and plan['requires_action_review'] and plan['unmatched_recorded_actions']==1


@pytest.mark.parametrize('change',['time','break','target','item','origin','request_only'])
def test_recorded_world_use_needs_matching_acknowledged_crossing(change):
    a,b=[10,10,7],[10,11,6]
    edge=dict(origin=a,entry=a,exit=b,action='use',item_id=1948)
    use=dict(type='use_item',x=10,y=10,z=7,item_id=1948,player_pos=a,t=1)
    trace=[dict(pos=a,t=0),dict(pos=b,t=2)]
    assert review_recorded_actions(dict(waypoints=[use],observed_path=trace),[edge])[0]['matched_transition']==0
    if change=='time':use['t']=3
    if change=='break':trace[1]['break_before']=True
    if change=='target':use['x']=11
    if change=='item':use['item_id']=9999
    if change=='origin':use['player_pos']=[11,10,7]
    if change=='request_only':trace=[]
    assert review_recorded_actions(dict(waypoints=[use],observed_path=trace),[edge])[0]['matched_transition'] is None
