"""No game connection: transition permissions and bounded recovery."""
import asyncio
import time
import pytest
from dbv.brain.navigation.map_atlas import MapAtlas,trace_links,remap_links,validate_links
from test_adaptive_cavebot import cell,setup,connect_fake_walk
from test_automation_safety import state


@pytest.mark.parametrize('kind',['hole','stairs','teleport','door','level_gate'])
def test_ordinary_leg_avoids_special_tiles_even_if_cached_as_walkable(tmp_path,kind):
    atlas=MapAtlas(tmp_path,'f'*64)
    cells=[cell(x,y) for x in range(10,15) for y in range(10,13)]
    cells[6]['navigation']=[{'kind':kind}]
    hazard=[cells[6][k] for k in ('x','y','z')]
    atlas.observe(dict(status='ready',cells=cells))
    plan=atlas.plan([[10,10,7],[14,10,7]],[])
    assert plan['complete'] and hazard not in plan['path'] and not plan['transitions']


def test_same_floor_teleport_requires_its_exact_anchor_pair_and_id(tmp_path):
    a=MapAtlas(tmp_path,'f'*64)
    cells=[cell(10,10),cell(11,10,navigation=[{'kind':'teleport'}]),cell(20,20)]
    a.observe(dict(status='ready',cells=cells))
    edge=dict(id='tp',origin=[10,10,7],entry=[11,10,7],exit=[20,20,7],action='step',item_id=351,evidence='observed')
    points=[edge['origin'],edge['exit']]
    assert not a.plan(points,[edge])['complete']
    assert not a.plan(points,[edge],links=[dict(from_index=0,id='other')])['complete']
    plan=a.plan(points,[edge],links=[dict(from_index=0,id='tp')])
    assert plan['complete'] and len(plan['transitions'])==1
    d=a.save_draft('tp',points,links=plan['links'])
    assert MapAtlas(tmp_path,'f'*64).drafts()[0]['links']==plan['links']


def test_automatic_entry_without_navigation_hint_is_still_avoided(tmp_path):
    a=MapAtlas(tmp_path,'f'*64);cells=[cell(x,y) for x in range(10,14) for y in range(10,13)]
    a.observe(dict(status='ready',cells=cells))
    edge=dict(id='tp',origin=[10,10,7],entry=[11,10,7],exit=[13,10,7],action='step',item_id=351,evidence='observed')
    p=a.plan([edge['origin'],edge['exit']],[edge])
    assert p['complete'] and [11,10,7] not in p['path'] and not p['transitions']


def test_recorded_links_require_observed_crossing_and_optimization_keeps_authorization():
    points=[[10,10,7],[20,20,6],[21,20,6]]
    edge=dict(id='up',origin=points[0],entry=[10,9,7],exit=points[1],action='step',evidence='observed')
    links=trace_links(points,[edge],points)
    assert links==[dict(from_index=0,id='up')]
    assert not trace_links(points,[edge],points[1:])
    assert not trace_links(points,[edge,{**edge,'id':'ambiguous'}],points)
    assert remap_links(links,points,[[9,10,7],*points])==[dict(from_index=1,id='up')]
    assert not remap_links(links,points,[points[0],[19,20,6],points[2]])
    with pytest.raises(ValueError):validate_links([dict(from_index=True,id='up')],points)


def test_recorded_ladder_variants_with_loot_retain_exact_transition(tmp_path):
    a,b=[10,10,7],[10,11,6]
    ladder=dict(id='a',origin=a,entry=a,exit=b,action='use',item_id=1948,evidence='observed',entry_items=[408,1948])
    loot={**ladder,'id':'b','entry_items':[408,1948,8479,8141]}
    links=trace_links([a,b],[loot,ladder],[a,b])
    assert links==[{'from_index':0,'id':'a'}]
    assert trace_links([a,b],[ladder,loot],[a,b])==links
    atlas=MapAtlas(tmp_path,'f'*64)
    atlas.observe(dict(status='ready',cells=[{**cell(*a), 'items':[{'id':1948,'stack':1,'flags':[]}], 'navigation':[{'kind':'ladder'}]},cell(*b)]))
    assert atlas.plan([a,b],[ladder,loot],links=links)['complete']
    # Different objects/actions remain ambiguous, as does an unobserved hop.
    assert not trace_links([a,b],[ladder,{**loot,'item_id':9999}],[a,b])
    assert not trace_links([a,b],[ladder,{**loot,'action':'use_with'}],[a,b])
    assert not trace_links([a,b],[ladder,loot],[a,[11,10,7],b])
    conflicting={**loot,'id':'c','exit':[12,12,6]}
    assert not atlas.plan([a,b],[ladder,conflicting],links=links)['complete']


def test_recorded_hole_from_another_side_and_saved_links_survive_new_approach(tmp_path):
    origin,entry,dest=[10,11,6],[10,10,6],[10,10,7]
    row=dict(id='hole',origin=[9,10,6],entry=entry,exit=dest,action='step',entry_items=[432],evidence='observed')
    atlas=MapAtlas(tmp_path,'f'*64)
    atlas.observe(dict(status='ready',cells=[cell(*origin),cell(*entry,item=432,navigation=[dict(kind='hole')]),cell(*dest),cell(9,10,6)]))
    points=[origin,dest];links=trace_links(points,[row],points)
    assert links==[dict(from_index=0,id='hole')]
    assert not atlas.plan(points,[row])['complete'],'ordinary legs must not cross floors'
    plan=atlas.plan(points,[row],links=links)
    assert plan['complete'] and plan['transitions'][0]['origin']==origin
    saved=atlas.save_draft('another approach',points,links=links)
    row['origin']=[11,10,6]  # Later passive observation retains the same ID.
    draft=next(d for d in MapAtlas(tmp_path,'f'*64).drafts() if d['id']==saved)
    assert atlas.plan(draft['points'],[row],links=draft['links'])['complete']
    assert not trace_links(points,[row],[origin,[9,11,6],dest])
    assert not trace_links([[12,10,6],dest],[row],[[12,10,6],dest])
    conflict={**row,'id':'conflict','exit':[20,20,7]}
    assert not trace_links(points,[row,conflict],points)
    assert not atlas.plan(points,[row,conflict],links=links)['complete']


@pytest.mark.parametrize('reachable',[True,False])
def test_stairs_enter_from_current_side_even_if_recorded_side_is_blocked(tmp_path,reachable):
    anchor,entry,approach,landing=[1039,1300,4],[1038,1300,4],[1038,1299,4],[1037,1300,3]
    onward=[1036,1300,3]
    cells=[cell(*p) for p in (anchor,landing,onward)]
    cells.extend([cell(*entry,kind='blocked',item=5259,navigation=[{'kind':'stairs'}]),
                  cell(*approach,kind='clear' if reachable else 'blocked')])
    atlas=MapAtlas(tmp_path,'f'*64)
    atlas.observe(dict(status='ready',cells=cells))
    row=dict(id='up',origin=approach,entry=entry,exit=landing,action='step',entry_items=[5259],evidence='observed')
    points=[anchor,landing,onward]
    plan=atlas.plan(points,[row],links=[dict(from_index=0,id='up')])
    assert plan['complete'] and plan['points']==points
    assert plan['path']==[anchor,landing,onward]
    assert [(leg['start'],leg['end']) for leg in plan['legs']]==[(0,1),(1,2)]
    action,=plan['transitions']
    assert (action['origin'],action['entry'],action['exit'])==(anchor,entry,landing)


@pytest.mark.parametrize('kind,dz', [('stairs', -1), ('passage', 1), ('hole', 1), ('descent', 1)])
def test_each_adjacent_side_enters_immediately(tmp_path, kind, dz):
    entry, recorded, landing = [20,20,7], [20,19,7], [21,20,7+dz]
    sides = [[20+dx,20+dy,7] for dx in (-1,0,1) for dy in (-1,0,1) if dx or dy]
    atlas = MapAtlas(tmp_path, 'b'*64)
    atlas.observe(dict(status='ready', cells=[cell(*p) for p in [*sides,landing]] +
                       [cell(*entry,kind='blocked',item=413,navigation=[dict(kind=kind)])]))
    edge = dict(id='floor', origin=recorded, entry=entry, exit=landing, action='step',
                entry_items=[413], evidence='observed')
    for side in sides:
        plan = atlas.plan([side,landing],[edge],links=[dict(from_index=0,id='floor')])
        assert plan['complete'] and plan['path'] == [side,landing]
        assert plan['transitions'][0]['entry'] == entry


def test_stair_prefers_recorded_side_only_on_equal_cost(tmp_path):
    start, join, entry, landing = [14,10,7], [11,9,7], [10,10,7], [9,10,6]
    recorded, other = [11,11,7], [11,9,7]
    atlas = MapAtlas(tmp_path, 'b'*64)
    atlas.observe(dict(status='ready', cells=[cell(*p) for p in
                       (start,[13,10,7],[12,10,7],recorded,other,landing)] +
                       [cell(*entry,kind='blocked',item=5259,navigation=[dict(kind='stairs')])]))
    edge = dict(id='up',origin=recorded,entry=entry,exit=landing,action='step',entry_items=[5259],evidence='observed')
    def route():
        return atlas.plan([start,join,landing],[edge],links=[dict(from_index=1,id='up')])
    assert route()['path'][-2] == recorded
    atlas.observe(dict(status='ready',cells=[cell(11,10,7)]))
    plan = route()
    assert plan['path'][-2] == [11,10,7] and plan['points'][1] == join
    assert plan['legs'][0]['end'] == plan['legs'][1]['start']
    atlas.observe(dict(status='ready',cells=[cell(11,10,7,kind='blocked'),cell(*recorded,kind='blocked')]))
    assert route()['path'][-2] == other


def test_specific_use_from_recorded_approach_is_not_lost_when_latest_origin_changes(tmp_path):
    from dbv.brain.navigation.map_atlas import recording_points
    a,b=[10,10,7],[10,11,6]
    row=dict(id='ladder',origin=[9,10,7],entry=a,exit=b,action='use',item_id=1948,evidence='observed')
    rec=dict(observed_path=[dict(pos=p) for p in [[11,10,7],a,b,[11,11,6]]])
    points,_=recording_points(rec,[row],optimize=True)
    links=trace_links(points,[row],[r['pos'] for r in rec['observed_path']])
    assert points==[[11,10,7],a,b,[11,11,6]] and links==[dict(from_index=1,id='ladder')]


def test_recovery_rejoins_locally_without_sending_during_planning(state,tmp_path):
    c,_,_=setup(state,tmp_path);c.status='running';state.game_state.position=(10,11,7)
    assert c.recover_local(state.passive_map_monitor.snapshot())
    assert c.path[0]==[10,11,7] and c.recovery_count==1 and state.game_proxy._inject_queue.empty()
    sent=connect_fake_walk(state,c);asyncio.run(c.run())
    assert c.status=='complete' and sent and state.game_state.position==(13,10,7)


@pytest.mark.parametrize('position',[(10,10,6),(30,30,7)])
def test_recovery_never_guesses_another_floor_or_distant_return(state,tmp_path,position):
    c,_,_=setup(state,tmp_path);c.status='running';state.game_state.position=position
    with pytest.raises(ValueError,match='Recovery'):c.recover_local(state.passive_map_monitor.snapshot())
    assert state.game_proxy._inject_queue.empty()


def test_recovery_respects_pause_retry_limit_and_never_skips_transition(state,tmp_path):
    c,_,_=setup(state,tmp_path);state.game_state.position=(10,11,7)
    c.pause('manual');assert not c.recover_local(state.passive_map_monitor.snapshot())
    c.status='running';c.recovery_attempts.extend([time.monotonic()]*2)
    assert c.recover_local(state.passive_map_monitor.snapshot())
    c.recovery_next_at=0;state.game_state.position=(10,10,7)
    c.recovery_attempts.clear();c.edges=[dict(origin=c.path[0],exit=c.path[1])]
    sample=state.passive_map_monitor.snapshot()
    sample['cells']=[r for r in sample['cells'] if [r['x'],r['y'],r['z']]!=c.path[0]]
    assert not c.recover_local(sample) and c.status=='running'
