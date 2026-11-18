from datetime import datetime, timezone

import pytest

from map_atlas import MapAtlas, trace_links
from recorded_rope import recorded_rope_edges
from test_adaptive_cavebot import cell


def recording_and_row():
    origin, entry, dest = [10, 11, 8], [11, 11, 8], [11, 12, 7]
    use = dict(type="use_item_ex", player_pos=origin, to_x=11, to_y=11, to_z=8,
               item_id=8900, target_item_id=386, t=2.6)
    recording = dict(version=2, name="atlas_20240101000000", position_source="client",
                     waypoints=[use], observed_path=[dict(t=2, pos=origin), dict(t=2.8, pos=dest)])
    timestamp = datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() + 3
    row = dict(id="review", evidence="review", reason="sample_gap", action="use_with",
               origin=origin, entry=entry, exit=dest, tool_id=8900, item_id=386,
               count=1, entry_items=[386], first_seen=timestamp, last_seen=timestamp)
    return recording, row


def test_recorded_rope_survives_reopen_and_respects_conflicting_exit(tmp_path):
    rec, row = recording_and_row()
    atlas = MapAtlas(tmp_path, "a"*64)
    origin, entry, dest = (row[k] for k in ("origin", "entry", "exit"))
    atlas.observe(dict(status="ready", cells=[cell(*origin), cell(*entry, item=386), cell(*dest)]))
    rows = atlas.remember_recorded_ropes(rec, [row])
    assert len(rows) == 2 and row["evidence"] == "review"
    assert atlas.remember_recorded_ropes(rec, [row]) == rows
    link = trace_links([origin, dest], rows, [origin, dest])
    assert link == [dict(from_index=0, id=rows[1]["id"])]
    reopened = MapAtlas(tmp_path, "a"*64)
    assert reopened.plan([origin, dest], [row], links=link)["complete"]
    assert not reopened.plan([origin, dest], [row])["complete"]
    assert reopened.with_recorded_ropes(rows) == rows
    assert not reopened.plan([origin, dest], [dict(rows[1], id="other", exit=[12, 12, 7])], links=link)["complete"]


@pytest.mark.parametrize("fault", ["break", "delay", "other_use", "walk", "foreign_item",
                                    "wrong_exit", "different_session", "no_command", "outside_window"])
def test_recorded_rope_requires_unambiguous_evidence(fault):
    rec, row = recording_and_row()
    after, use = rec["observed_path"][-1], rec["waypoints"][0]
    if fault == "break": after["break_before"] = True
    if fault == "delay": after["t"] += 2
    if fault == "other_use": rec["waypoints"].append(dict(use))
    if fault == "walk": rec["waypoints"].append(dict(type="walk", t=2.7))
    if fault == "foreign_item": use["target_item_id"] += 1
    if fault == "wrong_exit": after["pos"] = [12, 12, 7]
    if fault == "different_session": rec["name"] = "atlas_20240102000000"
    if fault == "no_command": row["reason"] = "no_command"
    if fault == "outside_window": use["t"] = 2.9
    assert recorded_rope_edges(rec, [row]) == []


def test_scoped_recording_after_long_wait_confirms_marked_spot_without_learner_command(tmp_path):
    rec, row = recording_and_row()
    atlas = MapAtlas(tmp_path, "b"*64)
    rec.update(name="moja_trasa", atlas_scope=atlas.scope, started_at=row["first_seen"]-3,
               action_time_precision_ms=1)
    rec["observed_path"][0].update(t=0, last_t=19.92)
    rec["observed_path"][1]["t"]=20.1
    rec["waypoints"][0]["t"]=19.9
    origin, entry, dest = (row[k] for k in ("origin","entry","exit"))
    atlas.observe(dict(status="ready",cells=[cell(*origin),cell(*entry,item=386),cell(*dest)]))
    assert atlas.remember_recorded_ropes(rec, [])==[]
    atlas.observe(dict(status="ready",cells=[cell(*entry,item=386,navigation=[dict(kind="rope_spot",item_id=386)])]))
    with pytest.raises(ValueError,match="innego atlasu"):
        atlas.recording_route(dict(rec,atlas_scope="wrong"),[],False)
    points,_,links,_=atlas.recording_route(rec,[],False)
    assert points==[origin,dest] and len(links)==1
    assert atlas.plan(points,[],links=links)["complete"]


def test_trace_break_only_invalidates_affected_occurrence():
    from map_atlas import recording_points
    rec, row=recording_and_row()
    origin,dest=row["origin"],row["exit"]
    rec["observed_path"]=[dict(pos=p,t=i,break_before=i==3) for i,p in enumerate([origin,dest,origin,dest])]
    row.update(id="rope", evidence="observed")
    points,_,links=recording_points(rec,[row],include_links=True)
    assert points==[origin,dest,origin,dest]
    assert links==[dict(from_index=0,id="rope")]


def test_adjust_only_ordinary_avoid_anchor_and_keep_raw_trace(tmp_path):
    atlas=MapAtlas(tmp_path,'d'*64)
    a,b,c,d,e=[[10,10,7],[11,10,7],[12,10,7],[12,11,7],[12,12,7]]
    atlas.observe(dict(status='ready',cells=[cell(*a),cell(*b),cell(*c,kind='avoid'),cell(*d),cell(*e)]))
    rec=dict(observed_path=[dict(pos=p,t=i) for i,p in enumerate([a,b,c,d,e])])
    points,original,links,adjusted=atlas.recording_route(rec,[],False)
    assert adjusted and adjusted[0]['from']==c and adjusted[0]['to'] in [b,d]
    assert original==[a,b,c,d,e] and not atlas.plan(points,[],links=links)['avoid_tiles']


def test_adjacent_avoid_anchors_can_be_routed_around_together(tmp_path):
    atlas=MapAtlas(tmp_path,'c'*64)
    a,b,c,d,e=[[10,10,7],[11,10,7],[11,11,7],[12,11,7],[12,12,7]]
    tiles=[cell(x,y,kind='avoid' if [x,y,7] in (b,c,d) else 'clear')
           for x in range(9,14) for y in range(9,14)]
    atlas.observe(dict(status='ready',cells=tiles))
    rec=dict(observed_path=[dict(pos=p,t=i) for i,p in enumerate([a,b,c,d,e])])
    points,raw,links,changes=atlas.recording_route(rec,[],False)
    result=atlas.plan(points,[],links=links)
    assert [x['waypoint'] for x in changes]==[2,3,4]
    assert raw==[a,b,c,d,e] and result['complete'] and result['avoid_tiles']==[]
