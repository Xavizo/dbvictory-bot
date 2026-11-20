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
