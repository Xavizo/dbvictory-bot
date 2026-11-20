from types import SimpleNamespace

from farming_telemetry import experience_for_level
from hunt_routes import propose
from manual_hunts import DiscoveryWriter, ManualVisits, discoveries, store_visit
from map_atlas import MapAtlas
from passive_map import PassiveMapMonitor
from pet_tracking import PetTracker
from test_atlas_and_pause import cell, sample


def walk(submit):
    game=SimpleNamespace(level=50,experience=experience_for_level(50),creatures={},pets=PetTracker())
    visits=ManualVisits(game,submit)
    now=1000
    def step(x,y=40,z=7,xp=0,monsters=(),gap=1):
        nonlocal now
        now+=gap
        game.experience+=xp
        slots={}
        for cid,name,mx in monsters:
            slots[(mx,y,z,0)] = cid
            if name:
                game.creatures[cid]=dict(t=now,source='dll',name=name)
        generation=game.pets.observe_map([],now,slots)
        visits.observe(dict(center=[x,y,z],captured_at=now,pet_map_generation=generation))
    return game,visits,step


def test_lure_keeps_first_tile_and_only_brackets_hunting_sectors(tmp_path):
    writes=[]
    game,visits,step=walk(lambda v,final:writes.append(v))
    for x in range(8,33):
        step(x)
    step(33,monsters=[(1,None,36),(2,'Bills',34)])
    step(34,monsters=[(1,'Brolly',38),(2,'Bills',35)])
    game.pets._confirm(2,(35,40,7),'Bills')
    step(40,xp=200,monsters=[(1,'Brolly',40)])
    step(46)
    step(48,xp=200,monsters=[(1,'Brolly',48)])
    for x in (44,38,33):
        step(x)
    for x in range(32,6,-1):
        step(x)
    visits.finish()
    row=writes[-1]
    assert row['sightings']==[[(36,40,7),(32,40,7),'Brolly',1]]
    assert set(row['cells'])=={(32,40,7),(40,40,7),(48,40,7)}
    assert row['returned'] and row['gains']==2
    atlas=MapAtlas(tmp_path,'a'*64)
    for checkpoint in [*writes,row]:
        group=store_visit(atlas,checkpoint)
    assert group['visits']==1 and group['xp']==400 and group['sightings'][0]['count']==1
    # A later visit gets XP somewhere else after luring the same species
    # from the same room. Shared first encounters still match the spot.
    new=dict(row,id='second',anchors=[[56,40,7]],cells=[*row['cells'],(56,40,7)])
    merged=store_visit(atlas,new)
    assert merged['visits']==2 and merged['id']==group['id'] and merged['sightings'][0]['count']==2
    remote=dict(new,id='elsewhere',seed=(200,40,7),anchors=[[200,40,7]],cells=[[200,40,7]],
                sightings=[[(200,40,7),(200,40,7),'Brolly',1]])
    assert store_visit(atlas,remote)['id']!=group['id']
    assert len(discoveries(MapAtlas(tmp_path,atlas.scope)))==2


def test_travel_relogin_and_xp_baseline_do_not_create_fake_visits():
    writes=[]
    game,visits,step=walk(lambda v,final:writes.append(v))
    for x in range(8,220,2):
        step(x)
    step(50,xp=40000)
    step(51,xp=1000,gap=100)
    game.experience=0
    step(51)
    step(51,xp=experience_for_level(50)+57000)
    visits.finish()
    assert not writes


def test_single_kill_visits_and_simultaneous_evidence_can_retract_a_late_pet(tmp_path):
    atlas=MapAtlas(tmp_path,'a'*64)
    saved=[]
    game,visits,step=walk(lambda v,final:saved.append(store_visit(atlas,v)))
    for n in range(2):
        step(34)
        step(35,xp=100,monsters=[(3*n+1,'Wolf',36),(3*n+2,'Wolf',37)])
        if n==0:
            visits.flush()
            assert len(saved[-1]['witnesses'][0]['points'])==2
            game.pets._confirm(2,(37,40,7),'Wolf')
        visits.finish()
        if n==0:
            assert saved[-1]['witnesses']==[]
    assert saved[-1]['eligible'] and saved[-1]['visits']==2
    assert saved[-1]['witnesses'][0]['points']==[(36,40,7),(37,40,7)]


def test_auto_proposal_is_offline_separate_from_drafts_and_one_way_floor_is_reported(tmp_path):
    atlas=MapAtlas(tmp_path,'a'*64)
    atlas.observe(sample([cell(x,40,z) for x in range(24,52) for z in (7,8)]))
    writer=DiscoveryWriter()
    game,visits,step=walk(lambda v,final:writer.submit(atlas,v,final,[]))
    for x,gain in ((25,0),(26,100),(32,100),(40,100),(33,100),(26,100)):
        step(x,xp=gain)
    step(26,z=8)
    step(27,z=8,xp=100)
    visits.finish()
    writer.close()
    area,=discoveries(atlas)
    assert area['check']['missing']==[[24,40,8]] and area['check']['covered']<area['check']['total']
    assert atlas.drafts()==[]
    # Independent visit in a fully connected room reaches verified readiness.
    local=MapAtlas(tmp_path,'d'*64)
    local.observe(sample([cell(x,40,7) for x in range(24,54)]))
    writer=DiscoveryWriter()
    _,v,step=walk(lambda snapshot,final:writer.submit(local,snapshot,final,[]))
    for x in (25,26,32,40,48,40,32,26):
        step(x,xp=100)
    v.finish()
    writer.close()
    a,=discoveries(local)
    assert a['check']['covered']==a['check']['total']==4
    route=propose(local,[],dict(scope=local.scope,ground=a['id']))
    assert route['path'][0]==route['path'][-1] and local.drafts()==[]


def test_map_monitor_flushes_on_automation_and_never_uses_running_route_frames(tmp_path):
    atlas=MapAtlas(tmp_path,'a'*64)
    writes=[]
    game,_,_=walk(lambda *_:None)
    state=SimpleNamespace(connected=True,game_state=game,adaptive_cavebot=None)
    m=PassiveMapMonitor(state)
    m.atlas=atlas
    m._learn=lambda frame,asset:frame
    m._manual_play=True
    m._visit_writer=SimpleNamespace(submit=lambda atlas,v,f,records:writes.append(v))
    for i in range(6):
        if i==3:
            state.adaptive_cavebot=SimpleNamespace(status='running')
        game.experience+=100
        frame=sample([cell(25+i,40)])
        frame.update(captured_at=300+i,center=[25+i,40,7],pet_map_generation=game.pets.observe_map([],300+i,{}))
        m._publish(frame,{},game)
    assert writes and {v['gains'] for v in writes}=={2} and len({v['id'] for v in writes})==1
    assert m._visit is None


def test_a_fast_trip_to_a_distant_room_separates_visits_but_repeated_sector_edges_do_not():
    writes=[]
    _,visits,step=walk(lambda v,final:writes.append(v))
    for x in (24,25,31,32):
        step(x,xp=100)
    for _ in range(12):
        step(39)
        step(40)
    step(41,xp=100)
    for x in range(44,126,4):
        step(x)
    step(126,xp=100)
    step(127,xp=100)
    visits.finish()
    by_id={v['id']:v for v in writes}
    assert len(by_id)==2
    near,far=by_id.values()
    assert all(s[0]<=40 for s in near['cells'])
    assert set(far['cells'])=={(120,40,7)}
