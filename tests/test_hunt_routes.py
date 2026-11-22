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
