"""Offline regression for dug holes, paged persistence and learned exits."""
import asyncio,json,sqlite3
from types import SimpleNamespace
import pytest
import map_atlas as atlas_module
from map_atlas import MapAtlas,navigation_edges,trace_links
from adaptive_cavebot import supported_transition
from test_adaptive_cavebot import cell,setup
from test_automation_safety import state
from test_live_object_learning import learner


def edge(tool=3457):
    return dict(id=str(tool),origin=[10,10,7],entry=[10,9,7],exit=[10,9,8],
                action='use_with_then_step',item_id=593,prepared_item_id=594,tool_id=tool,
                entry_items=[593],evidence='observed',direction='down',count=1)


def fake_tools(monkeypatch,ids=(3457,)):
    import client_equipment,client_player_state,passive_map_win32
    import time
    monkeypatch.setattr(passive_map_win32,'ReadOnlyClient',lambda:SimpleNamespace(close=lambda:None))
    monkeypatch.setattr(client_player_state,'ClientPlayerReader',lambda *args:None)
    monkeypatch.setattr(client_equipment,'EquipmentReader',lambda *args:SimpleNamespace(snapshot=lambda:dict(
        monotonic=time.monotonic(),slots={},sources=[dict(container=1,index=i,item_id=iid) for i,iid in enumerate(ids)])))


@pytest.mark.parametrize('opened',[False,True])
@pytest.mark.parametrize('tool',[3457,8900])
def test_dig_only_when_closed_then_wait_for_open_and_confirm_landing(state,tmp_path,monkeypatch,opened,tool):
    e=edge(tool);hole=cell(10,9,item=594 if opened else 593,navigation=[dict(kind='hole' if opened else 'covered_hole')])
    c,cells,_=setup(state,tmp_path,[cell(10,10),hole,cell(10,9,8)],[e['origin'],e['exit']],[e])
    fake_tools(monkeypatch,(tool,));sent=[]
    async def inject(payload,guard=None):
        assert guard();sent.append(payload);c.last_command=0
        if payload[0]==0x83:
            assert hole['items'][0]['id']==593
            hole['items'][0]['id']=594;hole['navigation']=[dict(kind='hole')]
        elif payload[0]==0x65:
            assert hole['items'][0]['id']==594;state.game_state.position=(10,9,8)
        else:raise AssertionError('Unexpected packet')
    state.game_proxy.inject_to_server=inject;asyncio.run(c.run())
    assert c.status=='complete' and [p[0] for p in sent]==([0x65] if opened else [0x83,0x65])
    if not opened:assert int.from_bytes(sent[0][6:8],'little')==tool


def test_alternative_tool_requires_matching_observed_preparation_and_exit(state,tmp_path,monkeypatch):
    a,b=edge(),edge(8900);hole=cell(10,9,item=593)
    # Choose the shovel link, but have only the independently observed AiO available.
    atlas=MapAtlas(tmp_path/'atlas','f'*64);atlas.observe(dict(status='ready',cells=[cell(10,10),hole,cell(10,9,8)]))
    plan=atlas.plan([a['origin'],a['exit']],[a,b],links=[dict(from_index=0,id=a['id'])])
    assert plan['transitions'][0]['tool_ids']==[3457,8900]
    assert len(trace_links([a['origin'],a['exit']],[a,b],[a['origin'],a['exit']]))==1
    other={**b,'exit':[20,20,8]}
    mapping=navigation_edges({(10,9,7):hole},[a,other])
    # Equivalent tool functions with conflicting landings cannot authorize use.
    assert not mapping.get((10,10,7))


@pytest.mark.parametrize('failure',['missing_tool','wrong_object','occupied','no_open_ack'])
def test_failed_dig_pauses_without_walking_or_repeated_use(state,tmp_path,monkeypatch,failure):
    e=edge();hole=cell(10,9,item=593,navigation=[dict(kind='covered_hole')])
    c,_,_=setup(state,tmp_path,[cell(10,10),hole,cell(10,9,8)],[e['origin'],e['exit']],[e])
    fake_tools(monkeypatch,() if failure=='missing_tool' else (3457,));sent=[]
    if failure=='wrong_object':hole['items'][0]['id']=999
    if failure=='occupied':hole['occupants']=1
    async def inject(payload,guard=None):assert guard();sent.append(payload)
    state.game_proxy.inject_to_server=inject;asyncio.run(c.run())
    assert c.status=='paused' and [p[0] for p in sent]==([0x83] if failure=='no_open_ack' else [])
    assert state.game_state.position==(10,10,7)


def test_open_state_is_marked_as_hole_and_closed_as_diggable(learner):
    r=edge();learner.records={r['id']:r}
    for iid,kind in [(593,'covered_hole'),(594,'hole')]:
        result=learner.annotate([cell(10,9,item=iid)])[0]
        assert any(f['item_id']==iid and f['kind']==kind for f in result['navigation'])
    assert supported_transition(r) and not supported_transition({**r,'tool_id':3003})


def test_atlas_retains_old_regions_after_memory_eviction_and_reopen(tmp_path,monkeypatch):
    monkeypatch.setattr(atlas_module,'CACHE_TILES',4)
    a=MapAtlas(tmp_path,'f'*64)
    a.observe(dict(status='ready',cells=[cell(x,10) for x in range(10,30)]))
    assert len(a.tiles)<=4 and a.total==20
    assert a.view(10,10,7)['cells'] and a.view(10,10,7)['limit'] is None
    loaded=MapAtlas(tmp_path,'f'*64)
    assert len(loaded.tiles)<=4 and loaded.total==20
    assert loaded.plan([[10,10,7],[29,10,7]],[])['complete']
    loaded.observe(dict(status='ready',cells=[cell(10,10,kind='blocked')]))
    assert loaded.total==20 and loaded.view(10,10,7)['cells'][0]['kind']=='blocked'


def test_previous_100000_tile_boundary_no_longer_discards_new_fields(tmp_path):
    a=MapAtlas(tmp_path,'f'*64)
    with sqlite3.connect(a.path) as db:
        db.executemany('INSERT INTO tiles VALUES (?,?,?,?,?)',
            ((100+i%400,100+i//400,7,json.dumps(cell(100+i%400,100+i//400)),0) for i in range(100000)))
    a=MapAtlas(tmp_path,'f'*64)
    assert a.total==100000 and len(a.tiles)<=4096
    e=edge();a.observe(dict(status='ready',cells=[cell(10,10),cell(10,9,item=593),cell(10,9,8)]))
    assert a.total==100003 and a.view(10,9,7)['cells']
    assert a.route_transitions([e],a.view(10,9,7)['cells'])[0]['exit']==[10,9,8]
    assert MapAtlas(tmp_path,'f'*64).total==100003


def test_wrong_profile_never_reads_tiles_through_paged_fallback(tmp_path):
    a=MapAtlas(tmp_path,'f'*64);a.observe(dict(status='ready',cells=[cell(10,10)]))
    with sqlite3.connect(a.path) as db:db.execute('UPDATE meta SET scope=?',('wrong',))
    bad=MapAtlas(tmp_path,'f'*64)
    assert bad.error and not bad.view(10,10,7)['cells'] and not bad.route_transitions([],[])
    with pytest.raises(ValueError):bad.plan([[10,10,7],[11,10,7]],[])
