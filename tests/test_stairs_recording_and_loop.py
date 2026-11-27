"""Recorded door/ladder approaches and local closure; never connect to a game."""
import pytest
from dbv.brain.navigation.map_atlas import MapAtlas, recording_points, trace_links
from test_adaptive_cavebot import cell, setup
from test_automation_safety import state
from test_transition_learning import sample, tile, step
from dbv.brain.navigation.transition_learning import TransitionLearner


def test_recording_keeps_open_door_crossing_before_ladder(tmp_path):
    a,b,c,top=[10,10,7],[11,10,7],[12,10,7],[12,10,6]
    door=dict(id='door',origin=a,entry=b,exit=c,action='step',evidence='observed',reason='door_crossing',entry_items=[6253])
    ladder=dict(id='ladder',origin=c,entry=[12,9,7],exit=top,action='use',item_id=1948,evidence='observed')
    observed=[a,b,c,top];obs=[door,ladder]
    points,_=recording_points({'observed_path':[{'pos':p,'t':i} for i,p in enumerate(observed)]},obs,optimize=False)
    assert points==[a,c,top]
    links=trace_links(points,obs,observed)
    assert links==[dict(from_index=0,id='door'),dict(from_index=1,id='ladder')]
    atlas=MapAtlas(tmp_path,'b'*64)
    atlas.observe(dict(status='ready',cells=[cell(*a),cell(*b,item=6253,navigation=[dict(kind='door',door_state_hint='open')]),cell(*c),cell(12,9,7,item=1948),cell(*top)]))
    plan=atlas.plan(points,obs,links=links)
    assert plan['complete'] and len(plan['transitions'])==2
    assert not trace_links([a,c],obs,[a,b,[11,11,7],b,c])


@pytest.mark.parametrize('broken',['none','closed','wrong_chain','changed_item','no_door'])
def test_fast_door_crossing_requires_exact_chain_and_same_open_object(tmp_path,broken):
    learner=TransitionLearner(tmp_path,'c'*64)
    a,b,c=[10,10,7],[11,10,7],[12,10,7]
    before=tile(b,6253,nav='door');before['navigation'][0]['door_state_hint']='open'
    learner.observe(sample(a,[before]),now=0)
    learner.enqueue(step(a,b),now=.1);learner.enqueue(step(b if broken!='wrong_chain' else [11,11,7],c),now=.2)
    after=tile(b,6253,nav='door');after['navigation'][0]['door_state_hint']='open'
    if broken=='closed':after['kind']='blocked'
    if broken=='changed_item':after['items'][0]['id']=6252
    if broken=='no_door':after['navigation']=[]
    learner.observe(sample(c,[after]),now=.3,wall=100)
    rows=learner.snapshot()['records']
    assert bool(rows)==(broken=='none')
    if rows:assert rows[0]['origin']==a and rows[0]['exit']==c and rows[0]['reason']=='door_crossing'


def test_near_loop_closes_only_over_known_safe_same_floor_terrain(state,tmp_path):
    c,_,_=setup(state,tmp_path);atlas=state.passive_map_monitor.atlas
    points=[[10,10,7],[11,10,7]]
    draft=atlas.save_draft('near',points,loop=True)
    c.prepare(atlas.scope,draft,True)
    assert c.path==[[10,10,7],[11,10,7],[10,10,7]]
    assert state.game_proxy._inject_queue.empty()
    plan=atlas.plan(points,[],loop=True)
    assert plan['loop_closed'] and plan['points']==points and plan['legs'][-1]['closing']


def test_loop_never_uses_a_teleport_for_automatic_return(tmp_path):
    atlas=MapAtlas(tmp_path,'e'*64)
    cells=[cell(10,10),cell(11,10,navigation=[{'kind':'teleport'}]),cell(12,10)]
    atlas.observe(dict(status='ready',cells=cells))
    edge=dict(id='out',origin=[10,10,7],entry=[11,10,7],exit=[12,10,7],action='step',item_id=351,evidence='observed')
    plan=atlas.plan([[10,10,7],[12,10,7]],[edge],links=[dict(from_index=0,id='out')],loop=True)
    assert not plan['complete'] and not plan['loop_closed']
    assert len(plan['transitions'])==1 and plan['path'][-1]==[12,10,7]


@pytest.mark.parametrize('opened',[False,True])
def test_observed_normal_door_opening_survives_later_state_changes(tmp_path,opened):
    atlas=MapAtlas(tmp_path,'d'*64);a,b,c=[10,10,7],[11,10,7],[12,10,7]
    door=dict(id='cross',origin=a,entry=b,exit=c,action='step',evidence='observed',reason='door_crossing',entry_items=[6253])
    opening=dict(id='open',origin=a,entry=b,exit=a,action='use',evidence='observed',item_id=6252,
                 object_change=dict(kind='door',closed_id=6252,open_id=6253,behavior='open_then_step'))
    atlas.observe(dict(status='ready',cells=[cell(*a),cell(*b,item=6253 if opened else 6252,kind='clear' if opened else 'blocked',navigation=[dict(kind='door',door_state_hint='open' if opened else 'closed')]),cell(*c)]))
    plan=atlas.plan([a,c],[door,opening],links=[dict(from_index=0,id='cross')])
    assert plan['complete'];edge=plan['transitions'][0]
    assert edge['action']=='use_then_step' and edge['item_id']==6252 and edge['open_item_id']==6253
    if not opened:assert not atlas.plan([a,c],[door],links=[dict(from_index=0,id='cross')])['complete']
    # A pair of ordinary waypoints is enough for a known NORMAL door.
    automatic=atlas.plan([a,c],[opening])
    assert automatic['complete'] and automatic['transitions'][0]['action']=='use_then_step'
    assert automatic['transitions'][0]['item_id']==6252


def test_level_gate_never_inherits_automatic_normal_door_passage(tmp_path):
    atlas=MapAtlas(tmp_path,'f'*64);a,b,c=[10,10,7],[11,10,7],[12,10,7]
    opening=dict(id='open',origin=a,entry=b,exit=b,action='use',evidence='observed',item_id=6252,
                 object_change=dict(kind='level_gate',closed_id=6252,open_id=6253,behavior='use_enters'))
    atlas.observe(dict(status='ready',cells=[cell(*a),cell(*b,item=6252,kind='blocked',navigation=[dict(kind='level_gate')]),cell(*c)]))
    assert not atlas.plan([a,c],[opening])['complete']
