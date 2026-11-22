from cavebot_history import save_report
from hunt_areas import save_area, sector
from hunt_routes import propose
from hunt_world import world_zones
from manual_hunts import store_visit
from map_atlas import MapAtlas
from test_atlas_and_pause import cell, sample
import pytest


def test_global_overlay_uses_saved_footprints_and_first_sightings(tmp_path):
    a = MapAtlas(tmp_path, 'a'*64)
    ident = a.save_draft('Sala', [[24,32,7],[25,32,7]], terrain={'ground':'world','sectors':[[24,32,7]]})
    save_area(a, dict(scope=a.scope,name='Schody',cells=[[24,32,8]],drafts=[ident]))
    store_visit(a, dict(id='visit',seed=[25,33,7],at=10,xp=100,gains=4,returned=True,
                        anchors=[[24,32,7]],cells=[[24,32,7]],sightings=[[[26,34,7],[24,32,7],'Wolf',1]]))
    previous = world_zones(a)
    path = a.path.with_name('cavebot-reports-'+a.path.name)
    report = dict(id='one',started_at=9,complete=True,visited_sectors=[[24,32,7],[32,32,7]],
                  events=[dict(kind='death',monster_position=[96,80,7])],
                  first_sightings=[dict(point=[27,34,7],observer_cell=[24,32,7],name='Wolf',count=2)])
    save_report(path, report)
    save_report(path, {**report,'id':'partial','complete':False,'visited_sectors':[[112,80,7]]})
    save_report(path, {**report,'id':'legacy','visited_sectors':[], 'first_sightings':[],
                       'events':[dict(position=[42,34,7],monster_position=[96,80,7])]})
    current = world_zones(a, previous['revision'])
    assert set(current['cells']) == {(24,32,7),(32,32,7),(40,32,7),(24,32,8)}
    assert current['habitats'] == [dict(position=(24,32,7),species=[('Wolf',3)])]
    assert world_zones(a,current['revision']) == {k:current[k] for k in ('scope','side','revision')}


def test_combined_selection_round_trips_without_changing_named_grounds(tmp_path):
    a = MapAtlas(tmp_path, 'b'*64)
    a.observe(sample([cell(x,32,7) for x in range(24,49)]))
    chosen = [[24,32,7],[32,32,7]]
    for index, c in enumerate(chosen):
        ident = a.save_draft(str(index), [c,[c[0]+1,c[1],c[2]]])
        save_area(a,dict(scope=a.scope,name='Sala '+str(index),cells=[c],drafts=[ident]))
    req = dict(scope=a.scope,ground='world',selected=chosen,compare=True)
    result = propose(a, [], req)
    assert {v['kind'] for v in result['comparison']['variants']} == {'short','respawn','full'}
    for option in result['comparison']['variants']:
        g=option['geometry']
        assert g['path'][0]==g['path'][-1]
        assert {sector(p) for p in g['path']} <= set(map(tuple,chosen))
    assert result['covered']==2
    ident=a.save_draft('Obie sale',result['points'],loop=True,links=result['links'],terrain={'ground':'world','sectors':chosen})
    reopened=MapAtlas(tmp_path,a.scope)
    draft=next(d for d in reopened.drafts() if d['id']==ident)
    assert draft['terrain']=={'ground':'world','sectors':chosen}
    assert reopened.plan(draft['points'],[],loop=True,links=draft['links'])['path']==result['path']
    with pytest.raises(ValueError):
        propose(a,[],{**req,'selected':[[48,32,7]]})
    with pytest.raises(ValueError):
        propose(a,[],{**req,'selected':[]})
