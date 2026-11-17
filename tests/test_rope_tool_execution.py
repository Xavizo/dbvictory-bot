"""Recorded tool transitions with synthetic inventory and transport only."""
import asyncio
from types import SimpleNamespace
import pytest
from adaptive_cavebot import supported_transition, transition_tools
from test_adaptive_cavebot import cell, setup
from test_automation_safety import state
from test_dig_and_paged_atlas import fake_tools, edge as dig_edge


def edge(tool=8900):
    return dict(id='rope', origin=[10,10,9], entry=[11,10,9], exit=[11,11,8],
                action='use_with', item_id=386, tool_id=tool, evidence='observed', entry_items=[386])


def configured(state, tmp_path, tool=8900, on_spot=False):
    e=edge(tool)
    if on_spot:e['entry']=e['origin'][:]
    spot=cell(*e['entry'], item=386, navigation=[dict(kind='rope_spot')])
    if on_spot:spot.update(occupants=1,occupant_types={'self':1})
    cells=([cell(*e['origin'])] if not on_spot else [])+[spot,cell(*e['exit'])]
    c,cells,_=setup(state,tmp_path,cells,[e['origin'],e['exit']],[e])
    return c,spot,e


@pytest.mark.parametrize('recorded,available',[(8900,8900),(8900,3003),(3003,8899),(8899,8900)])
@pytest.mark.parametrize('on_spot',[False,True])
def test_rope_or_aio_uses_recorded_spot_once_and_acknowledges_exit(state,tmp_path,monkeypatch,recorded,available,on_spot):
    c,spot,e=configured(state,tmp_path,recorded,on_spot)
    fake_tools(monkeypatch,(available,));sent=[]
    async def inject(payload,guard=None):
        assert guard();sent.append(payload)
        assert payload[0]==0x83 and int.from_bytes(payload[6:8],'little')==available
        assert tuple(payload[9:14])==tuple(bytes([11 if not on_spot else 10,0,10,0,9]))
        state.game_state.position=tuple(e['exit'])
    state.game_proxy.inject_to_server=inject
    asyncio.run(c.run())
    assert c.status=='complete' and len(sent)==1 and state.game_state.position==tuple(e['exit'])


@pytest.mark.parametrize('failure',['missing_tool','only_shovel','wrong_object','occupied','no_ack','wrong_exit'])
def test_invalid_rope_conditions_pause_without_walk_or_repeat(state,tmp_path,monkeypatch,failure):
    c,spot,e=configured(state,tmp_path)
    fake_tools(monkeypatch,() if failure=='missing_tool' else (3457,) if failure=='only_shovel' else (8900,))
    sent=[]
    if failure=='wrong_object':spot['items'][0]['id']=999
    if failure=='occupied':spot.update(occupants=1,occupant_types={'monster':1})
    async def no_ack(*args,**kwargs):return False
    if failure=='no_ack':c.wait_position=no_ack
    async def inject(payload,guard=None):
        assert guard();sent.append(payload)
        if failure=='wrong_exit':state.game_state.position=(20,20,8)
    state.game_proxy.inject_to_server=inject
    asyncio.run(c.run())
    assert c.status=='paused' and len(sent)==(1 if failure in {'no_ack','wrong_exit'} else 0)
    assert all(p[0]==0x83 for p in sent)


@pytest.mark.parametrize('change',['object','occupant','tool','position','pause','disconnect'])
def test_rope_queue_rechecks_inventory_spot_and_lifetime(state,tmp_path,monkeypatch,change):
    import client_equipment
    c,spot,e=configured(state,tmp_path);c.status='running'
    fake_tools(monkeypatch,(8900,))
    inventory=dict(slots={},sources=[dict(container=1,index=0,item_id=8900)])
    monkeypatch.setattr(client_equipment,'EquipmentReader',lambda *args:SimpleNamespace(snapshot=lambda:inventory))
    async def inject(payload,guard=None):
        assert guard()
        if change=='object':spot['items'][0]['id']=999
        if change=='occupant':spot.update(occupants=1,occupant_types={'player':1})
        if change=='tool':inventory['sources'][0]['index']=1
        if change=='position':state.game_state.position=(9,10,9)
        if change=='pause':c.pause('manual')
        if change=='disconnect':state.game_proxy.logged_in=False
        assert not guard()
        raise ValueError('synthetic queue rejected')
    state.game_proxy.inject_to_server=inject
    with pytest.raises(ValueError,match='synthetic queue'):
        asyncio.run(c.transition(e,state.passive_map_monitor.snapshot()))


@pytest.mark.parametrize('patch',[dict(tool_id=3457),dict(item_id=None),dict(item_id=0),dict(exit=[11,11,9]),dict(exit=[11,11,10]),dict(origin=[8,10,9]),dict(action='fly_up')])
def test_tools_never_authorize_unobserved_mechanisms_or_wrong_family(patch):
    assert not supported_transition({**edge(),**patch})
    assert not transition_tools({**edge(),**patch})


def test_equivalent_tools_are_selected_by_function():
    assert transition_tools(edge())==[8900,3003,8899]
    assert transition_tools(dig_edge(8900))==[8900,3457,8899]
    assert not transition_tools({**dig_edge(),'tool_id':3003})


def test_dig_can_substitute_separate_shovel_for_recorded_aio(state,tmp_path,monkeypatch):
    e=dig_edge(8900);hole=cell(*e['entry'],item=e['item_id'])
    c,_,_=setup(state,tmp_path,[cell(*e['origin']),hole,cell(*e['exit'])],[e['origin'],e['exit']],[e])
    fake_tools(monkeypatch,(3457,));sent=[]
    async def inject(payload,guard=None):
        assert guard();sent.append(payload);c.last_command=0
        if payload[0]==0x83:
            assert int.from_bytes(payload[6:8],'little')==3457
            hole['items'][0]['id']=e['prepared_item_id']
        elif payload[0]==0x65:state.game_state.position=tuple(e['exit'])
        else:raise AssertionError('unexpected command')
    state.game_proxy.inject_to_server=inject
    asyncio.run(c.run())
    assert c.status=='complete' and [p[0] for p in sent]==[0x83,0x65]


@pytest.mark.parametrize('kind',['rope','dig'])
def test_saved_tool_link_survives_learning_a_different_approach(tmp_path,kind):
    from map_atlas import MapAtlas,trace_links,review_recorded_actions
    e=edge() if kind=='rope' else dig_edge(8900)
    a,b=e['origin'],e['exit'];latest={**e,'origin':[e['entry'][0]+1,e['entry'][1],e['entry'][2]]}
    other={**latest,'id':'separate','tool_id':3003 if kind=='rope' else 3457}
    atlas=MapAtlas(tmp_path,'c'*64)
    atlas.observe(dict(status='ready',cells=[cell(*a),cell(*e['entry'],item=e['item_id']),cell(*b),cell(*latest['origin'])]))
    links=[dict(from_index=0,id=e['id'])]
    plan=atlas.plan([a,b],[latest,other],links=links)
    assert plan['complete'] and plan['transitions'][0]['origin']==a
    assert sorted(plan['transitions'][0]['tool_ids'])==([3003,8900] if kind=='rope' else [8900])
    assert len(trace_links([a,b],[latest,other],[a,b]))==1
    rec=dict(observed_path=[dict(pos=a,t=1),dict(pos=b,t=2)],waypoints=[dict(type='use_item_ex',
        item_id=e['tool_id'],target_item_id=e['item_id'],to_x=e['entry'][0],to_y=e['entry'][1],to_z=e['entry'][2],player_pos=a,t=1.5)])
    assert review_recorded_actions(rec,plan['transitions'])[0]['matched_transition']==0
    conflict={**other,'exit':[30,30,b[2]]}
    assert not atlas.plan([a,b],[latest,conflict],links=links)['complete']
    assert not trace_links([[a[0]+10,a[1],a[2]],b],[latest,other],[[a[0]+10,a[1],a[2]],b])


def test_saved_gate_link_keeps_approach_after_later_use_from_another_side(tmp_path):
    from map_atlas import MapAtlas,trace_links
    from test_gate_entry_planning import evidence,gate
    a,b=[10,10,7],[11,10,7];row=evidence();row['origin']=[12,10,7]
    atlas=MapAtlas(tmp_path,'d'*64)
    atlas.observe(dict(status='ready',cells=[cell(*a),gate(),cell(12,10)]))
    points=[a,b,[12,10,7]];links=[dict(from_index=0,id='gate')]
    plan=atlas.plan(points,[row],links=links)
    assert plan['complete'] and plan['transitions'][0]['origin']==a
    assert trace_links(points,[row],points)==links
    assert not atlas.plan(points,[row])['complete']  # No implicit gate traversal.
    conflict={**row,'object_change':{**row['object_change'],'open_id':7777}}
    assert not atlas.plan(points,[row,conflict],links=links)['complete']
    appearance={**row,'object_change':{**row['object_change'],'behavior':'stays_open'}}
    assert not atlas.plan(points,[appearance],links=links)['complete']
