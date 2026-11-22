import pytest

from hunt_areas import save_area, sector
from hunt_routes import propose
from map_atlas import MapAtlas
from test_atlas_and_pause import cell, sample


def fixture(tmp_path):
    atlas = MapAtlas(tmp_path,'c'*64)
    # The first sketch point is on the approach from town, outside the ground.
    town, entrance = [8,32,7], [27,32,7]
    draft = atlas.save_draft('East',[town,entrance])
    atlas.observe(sample([cell(x,32,z) for z in (7,8,9) for x in range(8,48)]))
    cells = [[sx,32,z] for z in (7,8,9) for sx in (24,32,40)]
    area = save_area(atlas,dict(scope=atlas.scope,name='East',cells=cells,drafts=[draft]))
    def stairs(a,b):
        x = 34 if a<b else 36
        return dict(id=f'{a}-{b}',origin=[x,32,a],entry=[x,31,a],exit=[x,32,b],
                    action='use',evidence='observed',item_id=1956,entry_items=[1956])
    atlas.observe(sample([cell(x,31,z,item=1956) for x in (34,36) for z in (7,8,9)]))
    return atlas,dict(scope=atlas.scope,ground=area),[stairs(7,8),stairs(8,7),stairs(8,9),stairs(9,8)]


def test_proposal_covers_three_floors_and_saves_as_separate_playable_geometry(tmp_path):
    atlas, request, edges = fixture(tmp_path)
    original = atlas.drafts()
    route = propose(atlas,edges,request)
    assert atlas.drafts()==original
    assert route['covered']==route['total']==9 and route['missing']==[]
    assert route['path'][0]==route['path'][-1]==[27,32,7]
    assert all(p[0]>=24 for p in route['path'])
    assert route['transitions']>=4 and route['sightings']==0
    assert route['points']!=original[0]['points']
    ident = atlas.save_draft('Nowa pętla',route['points'],loop=True,links=route['links'])
    saved = next(d for d in atlas.drafts() if d['id']==ident)
    replay = atlas.plan(saved['points'],edges,loop=True,links=saved['links'])
    assert replay['complete'] and replay['loop_closed'] and replay['path']==route['path']
    approach = propose(atlas,edges,dict(request,approach=[8,32,9],entrance=route['entrance']))
    assert approach['path'][0]==[8,32,9] and approach['path'][-1]==route['path'][0]
    assert approach['transitions']==2


def test_one_way_exit_does_not_claim_coverage_or_implicit_return(tmp_path):
    atlas, request, edges = fixture(tmp_path)
    route = propose(atlas,edges[:-1],request)
    assert route['covered']==6 and {p[2] for p in route['missing']}=={9}
    assert all(p[2]!=9 for p in route['path'])
    with pytest.raises(ValueError,match='dojścia'):
        propose(atlas,edges[:-1],dict(request,approach=[34,32,9],entrance=route['entrance']))
    with pytest.raises(ValueError,match='atlas'):
        propose(atlas,edges,dict(request,scope='other'))


def test_sector_selection_preserves_entrance_and_only_uses_remaining_stairs(tmp_path):
    atlas, request, edges=fixture(tmp_path)
    original=propose(atlas,edges,request)
    request.update(excluded=[[32,32,7]],loop_start=original['entrance'])
    route=propose(atlas,edges,request)
    assert route['covered']==1 and route['total']==8
    assert route['missing'] and route['entrance']==original['entrance']
    assert {sector(p) for p in route['path']}=={(24,32,7)}
    for z,dest in ((7,8),(8,7)):
        atlas.observe(sample([cell(28,31,z,item=1956)]))
        edges.append(dict(id=f'alt-{z}',origin=[28,32,z],entry=[28,31,z],exit=[28,32,dest],
                          action='use',evidence='observed',item_id=1956,entry_items=[1956]))
    route=propose(atlas,edges,request)
    assert route['covered']==7 and route['transitions']>=4
    assert (32,32,7) not in set(map(sector,route['path']))
    with pytest.raises(ValueError,match='tylko sektory'):
        propose(atlas,edges,dict(request,excluded=[[8,32,7]]))


def test_optimization_keeps_unique_exploration_and_cuts_a_repeated_return(tmp_path):
    from hunt_routes import shorter_draft
    atlas=MapAtlas(tmp_path,'e'*64)
    atlas.observe(sample([cell(x,32,7) for x in range(24,47)]))
    points=[[x,32,7] for x in (24,32,24,32,46,24)]
    plan=atlas.plan(points,[],loop=True)
    r=shorter_draft(atlas,[],plan,['']*len(points))
    assert len(r['path'])<len(plan['path']) and set(map(tuple,r['path']))==set(map(tuple,plan['path']))
    assert r['loop_closed'] and r['points'][0]==r['points'][-1]==points[0]
    assert shorter_draft(atlas,[],plan,['keep']*len(points)) is plan


def test_sightings_guide_visits_without_following_a_lured_monster_or_crossing_a_wall(tmp_path,monkeypatch):
    atlas = MapAtlas(tmp_path,'b'*64)
    atlas.observe(sample([cell(x,y) for x in range(24,40) for y in range(32,40) if (x,y)!=(39,39)]))
    draft = atlas.save_draft('Sala',[[24,32,7],[25,32,7]])
    def info(_):
        return dict(areas=[],drafts=[dict(id=draft,archived=False,
            latest=dict(cells=[[24,32,7],[32,32,7]],observations=[dict(kind='death',point=[32,32,7])]),
            sightings=[dict(point=[39,39,7],observer_cell=[32,32,7],count=3),
                       dict(point=[33,32,7],observer_cell=[96,32,7],count=100)])])
    monkeypatch.setattr('hunt_routes.catalogue',info)
    route = propose(atlas,[],dict(scope=atlas.scope,ground='draft:'+draft))
    assert route['sightings']==3 and route['covered']==2
    assert [39,39,7] not in route['path']
    assert any(x>=38 and y>=38 for x,y,z in route['path'])
    assert all(sector(p) in {(24,32,7),(32,32,7)} for p in route['path'])


def test_manual_loop_uses_largest_round_trip_despite_seed_and_one_way_exit(tmp_path):
    from hunt_routes import manual_circuit
    atlas=MapAtlas(tmp_path,'a'*64)
    # First XP is on 6. From there a one-way drop reaches the main floor.
    atlas.observe(sample([cell(x,32,6) for x in range(24,31)] +
                         [cell(x,32,7) for x in range(24,60)] + [cell(26,31,6,item=1956)]))
    area=dict(seed=[25,32,6],cells=[[24,32,6]]+[[x,32,7] for x in range(24,60,8)],
              sightings=[dict(point=[40,32,7],observer_cell=[40,32,7],count=3)])
    edge=dict(id='drop',origin=[26,32,6],entry=[26,31,6],exit=[26,32,7],
              action='use',evidence='observed',item_id=1956,entry_items=[1956])
    route=manual_circuit(atlas,[edge],area)
    assert route['covered']==5 and route['total']==6 and route['missing']==[(24,32,6)]
    assert route['path'][0]==route['path'][-1]==[40,32,7]
    assert route['transitions']==0


def test_selected_terrain_persists_across_reopen_and_rejects_outside_interactions(tmp_path):
    atlas, req, edges = fixture(tmp_path)
    chosen = [[32,32,z] for z in (7,8,9)]
    limited = propose(atlas, edges, dict(req, selected=chosen))
    # The ladder can be clicked from the chosen room, but its tile is outside it.
    assert limited['transitions'] == 0
    assert limited['covered'] == 1 and len(limited['missing']) == 2
    inside = [dict(e, entry=[e['origin'][0],33,e['origin'][2]]) for e in edges]
    atlas.observe(sample([cell(*e['entry'],item=1956) for e in inside]))
    route = propose(atlas, inside, dict(req, selected=chosen))
    assert route['covered'] == 3 and route['transitions'] == 4
    ident = atlas.save_draft('Trzy piętra', route['points'], links=route['links'],
                             terrain=dict(ground=req['ground'], sectors=chosen))
    reopened = MapAtlas(tmp_path, atlas.scope)
    row = next(r for r in reopened.drafts() if r['id'] == ident)
    replay = reopened.plan(row['points'], inside, links=row['links'], loop=True)
    sectors = set(map(tuple, chosen))
    assert replay['complete'] and replay['loop_closed']
    assert replay['path'] == route['path']
    assert {sector(p) for p in replay['path']} == sectors
    assert all(sector(t[k]) in sectors for t in replay['transitions'] for k in ('entry','origin','exit'))
    reopened.save_draft('Po zmianie nazwy', row['points'], ident, links=row['links'])
    saved = next(r for r in reopened.drafts() if r['id'] == ident)
    assert saved['terrain'] == dict(ground=req['ground'], sectors=chosen)
    # The sketch can generate a fresh loop even before its first lap.
    assert propose(reopened, inside, dict(req, ground='draft:'+ident, selected=chosen))['covered'] == 3
    for invalid in ([], [[33,32,7]], [[0,0,7]]):
        with pytest.raises(ValueError):
            propose(reopened, inside, dict(req, selected=invalid))


def test_connector_is_a_preview_until_accepted_and_survives_reload(tmp_path):
    atlas, request, edges = fixture(tmp_path)
    selected = [[32,32,z] for z in (7,8,9)]
    original = atlas.drafts()
    base = propose(atlas, edges, dict(request, selected=selected))
    suggestion = base['connector']
    assert (base['covered'], base['total'], base['transitions'], base['via']) == (1, 3, 0, [])
    assert sorted(suggestion['sectors']) == [(32,24,z) for z in (7,8,9)]
    assert suggestion['covered'] == 3
    assert {t['id'] for t in suggestion['passages']} == {e['id'] for e in edges}
    assert atlas.drafts() == original
    route = propose(atlas, edges, dict(request, selected=selected, via=suggestion['sectors'],
                                       loop_start=base['entrance']))
    assert route['covered'] == route['total'] == 3 and 'connector' not in route
    assert route['path'][0] == route['path'][-1] == list(base['entrance'])
    assert len(route['passages']) == route['transitions'] == 4
    assert sorted(route['selected']) == list(map(tuple, selected))
    assert sorted(route['via']) == suggestion['sectors']
    terrain = dict(ground=request['ground'], sectors=selected, via=list(map(list, route['via'])))
    ident = atlas.save_draft('Łącznik pięter',route['points'], links=route['links'], terrain=terrain)
    reopened = MapAtlas(tmp_path, atlas.scope)
    saved = next(d for d in reopened.drafts() if d['id']==ident)
    assert saved['terrain'] == terrain
    replay = reopened.plan(saved['points'], edges, links=saved['links'], loop=True)
    assert replay['complete'] and replay['loop_closed'] and replay['path'] == route['path']
    permitted = set(route['selected']) | set(route['via'])
    assert all(sector(t[k]) in permitted for t in replay['transitions'] for k in ('entry','origin','exit'))
    assert all(sector(p) in permitted for p in replay['path'])
    assert 'connector' not in propose(atlas, edges[:1], dict(request, selected=selected))
    for invalid in ([[320,320,8]], 'wrong', None):
        with pytest.raises(ValueError):
            propose(atlas, edges, dict(request, selected=selected, via=invalid))


def test_same_floor_connector_through_skipped_sector_is_optional(tmp_path):
    atlas, request, _ = fixture(tmp_path)
    selected = [[24,32,7],[40,32,7]]
    base = propose(atlas, [], dict(request, selected=selected))
    assert base['covered'] == 1 and base['connector']['sectors'] == [(32,32,7)]
    assert base['connector']['passages'] == []
    route = propose(atlas, [], dict(request, selected=selected, via=base['connector']['sectors']))
    assert route['covered'] == route['total'] == 2 and route['transitions'] == 0

def test_compare_only_inside_selection_and_preserves_saved_geometry(tmp_path):
    atlas, req, edges = fixture(tmp_path)
    sel = [[32,32,7], [40,32,7], [32,32,8]]
    result = propose(atlas,edges,dict(req,selected=sel,compare=True))
    options = result['comparison']
    assert options['visits']==0 and options['respawn_seconds']==60
    assert [v['kind'] for v in options['variants']]==['short','respawn','full']
    for opt in options['variants']:
        g=opt['geometry']
        assert g['path'][0]==g['path'][-1] and len(g['path'])>2
        assert g['covered']==len(set(map(sector,g['path'])))
        assert set(map(sector,g['path']))<=set(map(tuple,sel))
        assert opt['metrics']['xp_hour'] is None
        replay=atlas.plan(g['points'],edges,loop=True,links=g['links'])
        assert replay['loop_closed'] and replay['path']==g['path']
    assert options['variants'][-1]['geometry']['path']==result['path']
    assert result['missing']==[(32,32,8)]
    with pytest.raises(ValueError,match='wybierz sektory'):
        propose(atlas,edges,dict(req,compare=True))


def test_spawn_model_uses_observed_group_and_carries_respawn_between_laps():
    from hunt_comparison import HuntYield
    cells={(0,0,7),(8,0,7)}
    marks=[dict(point=[1,1,7],observer_cell=[0,0,7],count=200)]
    together=[dict(observer_cell=[0,0,7],points=[[1,1,7],[2,1,7],[3,1,7]])]
    m=HuntYield(marks,together,cells)
    assert m.slots[(0,0,7)]==3
    m.add_visit({(0,0,7):1,(80,80,7):20},1_000_000)
    assert m.visits==0
    m.add_visit({(0,0,7):10},1000)
    tiny=m.estimate([[1,1,7],[2,1,7],[1,1,7]],set())
    assert tiny['encounters_hour'] == tiny['xp_hour'] == 0 and tiny['kept_visible'] == 3
    outside = [[x,1,7] for x in range(1,14)]
    extended = m.estimate(outside+outside[-2::-1],set())
    assert extended['kept_visible'] == 0 and 100 < extended['encounters_hour'] < 190
    assert abs(extended['xp_hour']-100*extended['encounters_hour']) <= 6
    assert 0 in m.visible_slots((9,9,7)) and 0 not in m.visible_slots((10,9,7))
    assert not m.visible_slots((1,1,8))
    edge = [[x,1,7] for x in range(1,11)]
    partial = m.estimate(edge+edge[-2::-1],set())
    assert partial['kept_visible'] == 2 and 0 < partial['encounters_hour'] < extended['encounters_hour']
    lower = m.estimate([[1,1,7],[1,1,8],[1,1,7]],{((1,1,7),(1,1,8)),((1,1,8),(1,1,7))})
    assert lower['kept_visible'] == 0 and lower['encounters_hour'] > 0
    no_packs=m.estimate([[9,1,7],[10,1,7],[9,1,7]],set())
    assert no_packs['xp_hour'] is None

def test_restock_evidence_needs_local_kill_departure_and_new_close_contact():
    from hunt_comparison import observed_returns
    spot = (0,0,7)
    def event(kind, second, pos, creature=None):
        return dict(kind=kind, ms=second*1000, position=pos, monster_position=creature, detail='Mutant')
    seen = event('first_contact',0,[3,4,7],[4,4,7])
    killed = event('death',1,[4,4,7],[4,4,7])
    away = event('waypoint',9,[13,4,7])
    again = event('first_contact',43,[3,4,7],[5,4,7])
    assert observed_returns([seen,killed,away,again],{spot:1})=={spot:42}
    assert not observed_returns([seen,killed,again],{spot:1})
    assert not observed_returns([seen,killed,event('waypoint',9,[12,4,7]),again],{spot:1})
    assert not observed_returns([seen,event('death',1,[12,4,7],[12,4,7]),away,again],{spot:1})


def test_comparison_uses_parallel_corridor_and_keeps_dead_end_when_forced(tmp_path):
    from hunt_corridors import repeat_count
    from hunt_routes import shorter_draft
    atlas = MapAtlas(tmp_path, '6'*64)
    upper = [(x,35,7) for x in range(24,57)]
    lower = [(x,36,7) for x in range(25,56)]
    points = [list(upper[0]),list(upper[-1]),list(upper[0])]
    atlas.observe(sample([cell(*p) for p in upper]))
    forced = atlas.plan(points,[],loop=True)
    labels = ['Start','Koniec korytarza','Powrót']
    assert shorter_draft(atlas,[],forced,labels) is forced
    atlas.observe(sample([cell(*p) for p in lower]))
    initial = atlas.plan(points,[],loop=True)
    alternative = shorter_draft(atlas,[],initial,labels)
    assert repeat_count(alternative['path']) < repeat_count(initial['path'])
    assert all(p in alternative['points'] for p in points)
    assert alternative['points'][0] == alternative['points'][-1] == points[0]
    assert alternative['transitions'] == [] and alternative['loop_closed']
    assert len(alternative['path']) <= len(initial['path'])+(len(initial['path'])-1)//25
    assert atlas.plan(alternative['points'],[],links=alternative['links'],loop=True)['path'] == alternative['path']


def test_corridor_variation_keeps_both_ends_of_each_action():
    from hunt_corridors import patch, separate_corridors
    path = [(x,35,7) for x in range(25,48)]
    clear = set(path+[(x,36,7) for x in range(26,47)])
    upstairs = (47,35,6)
    walk = [(p,None) for p in path]+[(upstairs,'ladder'),(path[-1],'down')]
    walk.extend((p,None) for p in path[-2::-1])
    changed, remap = separate_corridors(walk, clear.__contains__,pinned={0,5},coverage=patch)
    assert changed[remap[5]] == walk[5]
    assert [(changed[i-1][0],p,action) for i,(p,action) in enumerate(changed) if action] == [
        (path[-1],upstairs,'ladder'),(upstairs,path[-1],'down')]
