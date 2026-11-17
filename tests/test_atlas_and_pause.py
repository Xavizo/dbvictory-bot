import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import mcp_server as server
from map_atlas import MapAtlas,recording_points
from map_annotations import validate_note
from passive_map import PassiveMapMonitor
from test_automation_safety import state,proxy


def cell(x,y,z=7,kind="clear",item=351):
    return {"x":x,"y":y,"z":z,"kind":kind,"items":[{"id":item,"stack":0,"flags":[]}],"navigation":[]}


def sample(cells):return {"status":"ready","cells":cells,"captured_at":time.time(),"center":[10,10,7]}


def test_recent_map_survives_read_interruption_without_becoming_editable():
    m=PassiveMapMonitor(SimpleNamespace(connected=True));m._result=sample([cell(10,10)])
    m._read_interrupted("updating")
    view=m.snapshot()
    assert view["cells"] and view["stale"] and not view["annotation_editable"] and view["status"]=="refreshing"
    m._result["captured_at"]-=3;m._read_interrupted("unavailable")
    assert m.snapshot()["cells"]==[]
    m._result=sample([cell(10,10)]);m.reset_learning_session()
    assert m.snapshot()["cells"]==[]


def test_atlas_persists_only_tiles_and_scope(tmp_path):
    atlas=MapAtlas(tmp_path,"a"*64)
    c={**cell(10,10),"occupants":2,"occupant_types":{"player":2},"name":"private"}
    atlas.observe(sample([c]));loaded=MapAtlas(tmp_path,"a"*64)
    view=loaded.view(10,10,7)
    assert view["total"]==1 and "occupants" not in view["cells"][0] and "name" not in view["cells"][0]
    atlas.observe({"status":"refreshing","cells":[cell(10,10,kind="blocked")]})
    assert atlas.view(10,10,7)["cells"][0]["kind"]=="clear"
    assert MapAtlas(tmp_path,"b"*64).view(10,10,7)["total"]==0


def test_preview_detours_diagonally_without_entering_blocked_destination(tmp_path):
    atlas=MapAtlas(tmp_path,"a"*64)
    atlas.observe(sample([cell(x,y,kind="blocked" if (x,y)==(11,10) else "clear") for x in range(10,13) for y in range(10,13)]))
    result=atlas.plan([[10,10,7],[12,10,7]],[])
    assert result["complete"] and result['path']==[[10,10,7],[11,11,7],[12,10,7]]
    assert result["preview_only"]
    assert not atlas.plan([[10,10,7],[13,10,7]],[])["complete"]


def test_preview_uses_only_observed_directed_floor_edge(tmp_path):
    atlas=MapAtlas(tmp_path,"a"*64)
    tiles=[cell(10,10),cell(10,9,item=9410),cell(20,20,6),cell(21,20,6)]
    tiles[1]["navigation"]=[{"kind":"rope_spot","item_id":9410,"source":"confirmed_id"}]
    atlas.observe(sample(tiles))
    row={"id":"rope","origin":[10,10,7],"entry":[10,9,7],"exit":[20,20,6],"action":"use_with","item_id":9410,"tool_id":8899,"evidence":"observed"}
    result=atlas.plan([[10,10,7],[20,20,6],[21,20,6]],[row],links=[dict(from_index=0,id="rope")])
    assert result["complete"] and result["transitions"][0]["tool_id"]==8899
    assert not atlas.plan([[20,20,6],[10,10,7]],[row])["complete"]
    assert not atlas.plan([[10,10,7],[21,20,6]],[{**row,"evidence":"review"}])["complete"]
    assert not atlas.plan([[10,10,7],[21,20,6]],[row,{**row,"exit":[21,20,6]}])["complete"]
    atlas.observe(sample([cell(10,9,item=999)]))
    assert not atlas.plan([[10,10,7],[21,20,6]],[row])["complete"]


def test_recording_simplification_keeps_floor_and_interaction_anchors():
    rec={"start_position":[10,10,7],"waypoints":[
        *[{"type":"walk","pos":[x,10,7]} for x in range(11,16)],
        {"type":"use_item_ex","x":65535,"to_x":15,"item_id":8899,"player_pos":[15,10,7],"pos":[15,10,7]},
        {"type":"floor_change","pos":[20,20,6]},
        {"type":"walk","pos":[21,20,6]}]}
    points,original=recording_points(rec)
    assert points==[[10,10,7],[15,10,7],[20,20,6],[21,20,6]]
    assert len(original)>len(points)


def test_long_recording_is_rejected_instead_of_silently_truncated():
    with pytest.raises(ValueError,match="za długie"):
        recording_points({"waypoints":[{"type":"walk","pos":[10,10,7]}]*20001})


@pytest.mark.parametrize("kind,activation",[("stairs","rope"),("door","shovel"),("teleport","aio"),("rope_spot","shovel"),("covered_hole","rope")])
def test_inappropriate_map_tools_rejected(kind,activation):
    with pytest.raises(ValueError):validate_note({"position":[10,10,7],"item_id":9410,"kind":kind,"activation_hint":activation,"required_level":None,"note":""})


@pytest.mark.parametrize("kind,default",[("stairs","step"),("door","open_if_closed"),("rope_spot","rope"),("covered_hole","shovel")])
def test_map_defaults_are_semantic(kind,default):
    n={"position":[10,10,7],"item_id":9410,"kind":kind,"activation_hint":None,"required_level":None,"note":""}
    assert validate_note(n)["activation_hint"]==default


def test_pause_keeps_selection_cancels_injections_and_resumes_only_selection(state,monkeypatch):
    async def exercise():
        state.game_proxy=proxy();state.ready=True
        state.settings["actions"]={"eat_food":{"enabled":True},"speed_up":{"enabled":False},"cavebot":{"enabled":False},"cavebot2":{"enabled":False}}
        task=asyncio.create_task(asyncio.sleep(100));internal=asyncio.create_task(asyncio.sleep(100))
        state._action_tasks={"eat_food":task,"dll_bridge":internal}
        await state.game_proxy.inject_to_server(b"queued")
        await server._async_pause_automation()
        assert state.automation_paused and state.automation_pause_kind=="manual"
        assert state.settings["actions"]["eat_food"]["enabled"] and task.cancelled() and not internal.done()
        assert state.game_proxy._inject_queue.empty()
        calls=[];monkeypatch.setattr(server,"_start_action",lambda name:calls.append(name))
        await server._async_resume_automation()
        assert calls==["eat_food"] and not state.automation_paused
        assert not state.settings["actions"]["cavebot"]["enabled"]
        internal.cancel();await asyncio.gather(internal,return_exceptions=True)
    asyncio.run(exercise())


def test_stop_after_pause_erases_resume_selection(state):
    async def exercise():
        state.settings["actions"]={"eat_food":{"enabled":True}}
        await server._async_pause_automation();await server._async_emergency_stop()
        assert not state.settings["actions"]["eat_food"]["enabled"]
        with pytest.raises(ValueError,match="Emergency STOP"):await server._async_resume_automation()
        await server._async_unlock_automation()
        assert not state._action_tasks
    asyncio.run(exercise())


def test_resume_failure_stays_paused_and_does_not_reset_cooldowns(state,monkeypatch):
    async def exercise():
        state.ready=True;state.game_proxy=proxy();state.settings["actions"]={"eat_food":{"enabled":True}}
        state.potion_scheduler.last_any=123
        await server._async_pause_automation()
        monkeypatch.setattr(server,"_start_action",lambda name:"synthetic launch failure")
        with pytest.raises(ValueError,match="paused again"):await server._async_resume_automation()
        assert state.automation_paused and state.potion_scheduler.last_any==123
    asyncio.run(exercise())


def test_atlas_api_is_authenticated_scoped_and_preview_only(state,monkeypatch,tmp_path):
    from http.client import HTTPConnection
    from http.server import HTTPServer
    import threading
    import dashboard_api as api
    from dashboard_security import SESSION_TOKEN,DASHBOARD_ORIGIN
    atlas=MapAtlas(tmp_path,"a"*64)
    atlas.observe(sample([cell(10,10),cell(11,10)]))
    state.passive_map_monitor=SimpleNamespace(atlas=atlas,learner=None)
    monkeypatch.setattr(api,"_state",state)
    http=HTTPServer(("127.0.0.1",0),api._Handler)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    def request(method,path,body=None,authenticated=True,origin=DASHBOARD_ORIGIN):
        conn=HTTPConnection("127.0.0.1",http.server_port,timeout=3)
        try:
            headers={"Origin":origin}
            if authenticated:headers["Authorization"]=f"Bearer {SESSION_TOKEN}"
            conn.request(method,path,json.dumps(body) if body is not None else None,headers)
            res=conn.getresponse();return res.status,json.loads(res.read())
        finally:conn.close()
    try:
        assert request("GET","/api/map/atlas?x=10&y=10&z=7",authenticated=False)[0]==401
        assert request("GET","/api/map/atlas?x=10&y=10&z=7",origin="https://evil.example")[0]==403
        status,data=request("GET","/api/map/atlas?x=10&y=10&z=7")
        assert status==200 and data["total"]==2
        plan={"scope":atlas.scope,"points":[[10,10,7],[11,10,7]]}
        assert request("POST","/api/map/plan",{**plan,"scope":"old"})[0]==400
        status,data=request("POST","/api/map/plan",plan)
        assert status==200 and data["complete"] and data["preview_only"]
        draft={**plan,"name":"Two floors"}
        assert request("POST","/api/map/drafts",draft,authenticated=False)[0]==401
        assert request("POST","/api/map/drafts",{**draft,"scope":"other"})[0]==400
        assert request("POST","/api/map/drafts",draft,origin="https://evil.example")[0]==403
        status,data=request("POST","/api/map/drafts",draft)
        assert status==200 and data["drafts"][0]["points"]==plan["points"]
        assert request("GET","/api/map/drafts")[1]["drafts"][0]["name"]=="Two floors"
        assert not state._action_tasks and not state.playback_active and state.game_proxy is None
    finally:
        http.shutdown();http.server_close();thread.join(timeout=2)


def test_atlas_retains_object_evidence_and_updates_open_door(tmp_path):
    atlas=MapAtlas(tmp_path,"a"*64)
    feature={"kind":"level_gate","item_id":6264,"stack":1,"source":"observed","required_level":320,
             "door_state_hint":"closed","behavior_hint":"use_enters","observation_count":2,"direction_hint":"up"}
    links=[{"id":"test","exit":[10,9,6],"action":"use","direction":"up","count":2}]
    atlas.observe(sample([{**cell(10,9,kind="blocked",item=6264),"navigation":[feature],"transitions":links}]))
    saved=MapAtlas(tmp_path,"a"*64).view(10,10,7)["cells"][0]
    assert saved["navigation"]==[feature] and saved["transitions"]==links
    atlas.observe(sample([{**cell(10,9,item=6265),"navigation":[{**feature,"item_id":6265,"door_state_hint":"open"}]}]))
    assert atlas.view(10,10,7)["cells"][0]["navigation"][0]["door_state_hint"]=="open"


def test_avoid_is_costly_but_not_a_blocked_start(tmp_path):
    a=MapAtlas(tmp_path,"a"*64)
    a.observe(sample([cell(x,y,kind="avoid" if y==10 else "clear") for x in range(10,14) for y in range(10,12)]))
    p=a.plan([[10,10,7],[13,10,7]],[])
    assert p["complete"] and len(p["avoid_tiles"])==2 and p["requires_action_review"]
    a.observe(sample([cell(13,10,kind="blocked")]))
    assert not a.plan([[10,10,7],[13,10,7]],[])["complete"]


def test_legacy_stair_request_is_repaired_only_with_matching_observed_edge(tmp_path):
    rec={"start_position":[10,10,7],"waypoints":[
        {"type":"walk","player_pos":[10,10,7],"pos":[10,9,7]},
        {"type":"floor_change","pos":[10,8,6]},
        {"type":"walk","player_pos":[10,8,6],"pos":[11,8,6]}]}
    edge={"id":"stairs","origin":[10,10,7],"entry":[10,9,7],"exit":[10,8,6],"entry_items":[1956],"item_id":None,"action":"step","evidence":"observed"}
    a=MapAtlas(tmp_path,"a"*64);a.observe(sample([cell(10,10,kind="avoid"),cell(10,9,kind="blocked",item=1956),cell(10,8,6),cell(11,8,6)]))
    points,original=recording_points(rec,[edge]);result=a.plan(points,[edge],original)
    assert not result["complete"]  # Legacy recordings need explicit confirmation.
    result=a.plan(points,[edge],original,links=[dict(from_index=0,id="stairs")])
    assert points==[[10,10,7],[10,8,6],[11,8,6]] and result["complete"] and len(result["transitions"])==1
    assert [10,9,7] in recording_points(rec,[{**edge,"evidence":"review"}])[0]


def test_observed_recording_ignores_unexecuted_commands_and_preserves_both_floors():
    rec={"start_position":[10,10,7],"observed_path":[{"pos":[10,10,7],"t":0},{"pos":[11,10,7],"t":1},{"pos":[11,9,6],"t":2},{"pos":[12,9,6],"t":3}],
         "waypoints":[{"type":"walk","pos":[999,999,7]},{"type":"floor_change","pos":[999,999,5]}]}
    points,original=recording_points(rec)
    assert original==points==[[10,10,7],[11,10,7],[11,9,6],[12,9,6]]
    with pytest.raises(ValueError,match="wystarczającej"):
        recording_points({**rec,"observed_path":[{"pos":[10,10,7]}]})


def test_drafts_persist_multiple_floors_with_profile_isolation(tmp_path):
    a=MapAtlas(tmp_path,"a"*64);points=[[10,10,7],[20,20,6],[10,11,7]]
    draft=a.save_draft("Schody",points)
    loaded=MapAtlas(tmp_path,"a"*64)
    assert loaded.drafts()[0]["points"]==points
    loaded.save_draft("Schody 2",points[:2],draft)
    assert len(a.drafts())==1 and a.drafts()[0]["name"]=="Schody 2"
    other=MapAtlas(tmp_path,"b"*64)
    assert other.drafts()==[]
    with pytest.raises(ValueError):other.save_draft("Wrong",points,draft)
    with pytest.raises(ValueError):a.save_draft("Invalid",[[1,2,17],[3,4,7]])


def test_stair_editor_uses_approach_exit_and_preserves_valid_prefix(tmp_path):
    a=MapAtlas(tmp_path,'a'*64)
    stair=cell(12,9,kind='blocked',item=1956)
    a.observe(sample([cell(x,10) for x in range(10,13)]+[stair,cell(12,8,6),cell(13,8,6)]))
    edge=dict(id='stairs',origin=[12,10,7],entry=[12,9,7],exit=[12,8,6],action='step',item_id=1956,entry_items=[1956],evidence='observed',count=2)
    broken=a.plan([[10,10,7],[12,10,7],[12,9,7]],[edge])
    assert not broken['complete'] and broken['path']==[[10,10,7],[11,10,7],[12,10,7]]
    assert broken['gaps'][0]['segment']==1 and len(broken['legs'])==1
    options=a.route_transitions([edge],[stair])
    assert len(options)==1 and options[0]['origin']==[12,10,7] and options[0]['exit']==[12,8,6]
    fixed=a.plan([[10,10,7],options[0]['origin'],options[0]['exit'],[13,8,6]],[edge],links=[dict(from_index=1,id='stairs')])
    assert fixed['complete'] and len(fixed['transitions'])==1
    assert [12,9,7] not in fixed['path'] and [12,8,6] in fixed['path']


@pytest.mark.parametrize('invalid',['unobserved','wrong_item','ambiguous','other_view'])
def test_editor_does_not_offer_unverified_stair_destinations(tmp_path,invalid):
    a=MapAtlas(tmp_path,'a'*64);stair=cell(10,9,kind='blocked',item=1956)
    a.observe(sample([cell(10,10),stair,cell(10,8,6),cell(11,8,6)]))
    row=dict(id='test',origin=[10,10,7],entry=[10,9,7],exit=[10,8,6],action='step',item_id=1956,entry_items=[1956],evidence='observed',count=1)
    rows=[row];visible=[stair]
    if invalid=='unobserved':row['evidence']='review'
    if invalid=='wrong_item':row['item_id']=9999
    if invalid=='ambiguous':rows.append({**row,'exit':[11,8,6]})
    if invalid=='other_view':visible=[cell(20,20)]
    assert not a.route_transitions(rows,visible)
