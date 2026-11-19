from copy import deepcopy
import time
from uuid import uuid4

import pytest

from cavebot_history import RunHistory, save_report
from hunt_areas import catalogue, save_area
from map_atlas import MapAtlas
from test_cavebot_reports import owner_for


def test_first_contact_keeps_monster_position_once_per_lap(tmp_path):
    o=owner_for(tmp_path/'atlas.sqlite3')
    gs=o.state.game_state
    gs.creatures[5]=dict(source='dll',t=time.time()-10,name='Vegeta',x=200,y=202,z=7)
    run=RunHistory(o,False)
    run.sample(True)
    assert not any(e['kind']=='first_contact' for e in run.lap.events)
    gs.creatures[5].update(t=time.time(),x=53,y=52)
    run.sample(True)
    gs.creatures[5]['x']=54
    run.sample(True)
    contacts=[e for e in run.lap.events if e['kind']=='first_contact']
    assert [(e['detail'],e['monster_position']) for e in contacts]==[('Vegeta',[53,52,7])]


def test_bounds_only_from_full_lap_and_metrics_follow_draft_identity(tmp_path):
    atlas=MapAtlas(tmp_path,'a'*64)
    a=atlas.save_draft('Ta sama nazwa',[[83,84,7],[88,84,7]])
    b=atlas.save_draft('Ta sama nazwa',[[83,84,7],[88,84,7]])
    draft=next(d for d in atlas.drafts() if d['id']==a)
    archive=atlas.path.with_name('cavebot-reports-'+atlas.path.name)
    owner=owner_for(atlas.path)
    owner.name=draft['name']
    owner.draft_id=a
    owner.route_draft=draft
    owner.loop_start=3
    run=RunHistory(owner,False)
    owner.state.game_state.position=(79,88,7)
    run.sample(True)
    assert run.lap is None
    owner.index=3
    for z in (7,8,9):
        owner.state.game_state.position=(670,407,z)
        run.sample(True)
    run.lap.event(run.frame(2),'death','Bardock',[673,407,9])
    s=run.lap.report(run.frame(3),True,'okrążenie')
    s.update(duration_ms=60000,xp=600,senzu=2,blocked_ms=1000)
    # Old stored reports have revision only and no footprint.
    old=deepcopy(s)
    old.pop('draft_id');old.pop('visited_sectors')
    old.update(id='legacy',started_at=s['started_at']-1,xp=None,senzu=None)
    save_report(archive,old)
    fragment=dict(s,id='partial',complete=False,visited_sectors=[(72,88,7)])
    save_report(archive,fragment)
    save_report(archive,s)
    orphan=dict(s,id='unverified',draft_id=None,revision='obsolete')
    save_report(archive,orphan)
    entries={d['id']:d for d in catalogue(atlas)['drafts']}
    assert entries[b]['laps']==0 and entries[a]['laps']==2
    assert (entries[a]['xp_hour'],entries[a]['senzu_hour'])==(36000,120)
    assert entries[a]['latest']['cells']==[(664,400,7),(664,400,8),(664,400,9)]
    assert entries[a]['latest']['observations']==[dict(kind='death',cell=(672,400,9),name='Bardock',count=1,at_monster=True)]
    data=dict(scope=atlas.scope,name='Spot',drafts=[a,b],cells=entries[a]['latest']['cells'],create_key=str(uuid4()))
    ident=save_area(atlas,data)
    assert save_area(atlas,data)==ident
    edit=dict(data,id=ident, version=1,cells=[[664,400,7],[672,400,8]])
    save_area(atlas,edit)
    with pytest.raises(ValueError,match='innym oknie'):
        save_area(atlas,edit)
    with pytest.raises(ValueError,match='atlas'):
        save_area(atlas,dict(data,scope='other'))
    with pytest.raises(ValueError,match='sektor'):
        save_area(atlas,dict(data,cells=[[673,400,7]]))
    atlas.save_draft('Zmieniona trasa',[[670,407,9],[671,407,9]],draft_id=a)
    reopened=catalogue(MapAtlas(tmp_path,atlas.scope))
    assert reopened['areas'][0]['cells']==edit['cells'] and set(reopened['areas'][0]['drafts'])=={a,b}
    assert next(d for d in reopened['drafts'] if d['id']==a)['laps']==2
    atlas.change_draft(a,'delete')
    assert next(d for d in catalogue(atlas)['drafts'] if d['id']==a)['archived']
    assert save_area(atlas,dict(edit,version=2,name='Spot po archiwizacji szkicu'))==ident
