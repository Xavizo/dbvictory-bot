"""Recorded use-enters evidence is a traversal, not merely an appearance hint."""
import asyncio
import pytest
from dbv.brain.navigation.map_atlas import MapAtlas,navigation_edges,recording_points
from dbv.brain.navigation.transition_learning import gate_entry_record
from test_adaptive_cavebot import cell,setup,connect_fake_walk
from test_automation_safety import state,proxy
from test_live_object_learning import learner,open_door,door_tile
from dbv.client_bridge.protocol import ClientOpcode


def evidence(**extra):
    return dict(id='gate',origin=[10,10,7],entry=[11,10,7],exit=[11,10,7],action='use',
                evidence='observed',item_id=6264,entry_items=[6264,6265],tool_id=None,count=4,
                direction='same_floor',object_change=dict(kind='passage',closed_id=6264,open_id=6265,behavior='use_enters'),**extra)


def gate(opened=False):
    row=cell(11,10,kind='clear' if opened else 'blocked',item=6265 if opened else 6264,
             navigation=[dict(kind='door',door_state_hint='open' if opened else 'closed')])
    row['items'][0]['flags']=[] if opened else ['not_walkable']
    return row


@pytest.mark.parametrize('opened',[False,True])
def test_existing_gate_observations_appear_in_editor_and_plan_through_entry(tmp_path,opened):
    atlas=MapAtlas(tmp_path,'d'*64)
    cells=[cell(10,10),gate(opened),cell(12,10)]
    atlas.observe(dict(status='ready',cells=cells))
    edges=atlas.route_transitions([evidence()],cells)
    assert len(edges)==1 and edges[0]['origin']==[10,10,7] and edges[0]['exit']==[11,10,7]
    assert not atlas.plan([[10,10,7],[12,10,7]],[evidence()])['complete']
    plan=atlas.plan([[10,10,7],[11,10,7],[12,10,7]],[evidence()],links=[dict(from_index=0,id='gate')])
    assert plan['complete'] and plan['path']==[[10,10,7],[11,10,7],[12,10,7]]
    assert plan['transitions'][0]['action']=='use' and plan['transitions'][0]['open_item_id']==6265
    # Learning one direction does not invent the opposite approach.
    assert not atlas.plan([[12,10,7],[10,10,7]],[evidence()])['complete']


@pytest.mark.parametrize('behavior',['open_then_step','manual_close','closes_after_exit','stays_open'])
def test_appearance_or_exit_state_is_not_an_automatic_use_edge(behavior):
    row=evidence();row['object_change']['behavior']=behavior
    assert gate_entry_record(row) is None
    assert not any(navigation_edges({(11,10,7):gate()},[row]).values())


def test_conflicting_pair_or_unmatched_tile_does_not_enable_a_gate():
    row=evidence();other=evidence();other['object_change']={**row['object_change'],'open_id':7777}
    assert not any(navigation_edges({(11,10,7):gate()},[row,other]).values())
    assert not any(navigation_edges({(11,10,7):cell(11,10,item=8888)},[row]).values())
    row['evidence']='review';assert gate_entry_record(row) is None


def test_optimization_preserves_observed_use_enter_waypoints():
    path=[[x,10,7] for x in range(9,14)]
    recording=dict(observed_path=[dict(pos=p) for p in path])
    points,_=recording_points(recording,[evidence()],optimize=True)
    assert [10,10,7] in points and [11,10,7] in points


def test_old_generic_passage_gets_open_state_and_a_live_transition_without_guessing_type(learner):
    open_door(learner,entered=True,kind=None)
    for opened in (False,True):
        tile=learner.annotate([door_tile(opened,kind=None)])[0]
        assert tile['navigation'][0]['kind']=='passage'
        assert tile['navigation'][0]['door_state_hint']==('open' if opened else 'closed')
        assert len(tile['transitions'])==1 and tile['transitions'][0]['exit']==[100,99,7]


@pytest.mark.parametrize('opened',[False,True])
@pytest.mark.parametrize('kind',['door','passage'])
def test_execution_uses_closed_gate_once_but_never_closes_open_gate(state,tmp_path,opened,kind):
    c,cells,_=setup(state,tmp_path,cells=[cell(10,10),gate(opened),cell(12,10)],points=[[10,10,7],[11,10,7],[12,10,7]],edges=[evidence()])
    c.status='running';sent=[]
    cells[1]['navigation'][0].update(kind=kind,behavior_hint='use_enters')
    async def inject(payload,guard=None):
        assert guard();sent.append(payload);c.last_command=0
        if payload[0] in (ClientOpcode.USE_ITEM,ClientOpcode.WALK_EAST):
            cells[1].update(gate(True));state.game_state.position=(11,10,7)
    state.game_proxy.inject_to_server=inject
    edge={**evidence(),'open_item_id':6265}
    asyncio.run(c.transition(edge,state.passive_map_monitor.snapshot()))
    assert [p[0] for p in sent]==[ClientOpcode.WALK_EAST if opened else ClientOpcode.USE_ITEM]
    assert state.game_state.position==(11,10,7)
