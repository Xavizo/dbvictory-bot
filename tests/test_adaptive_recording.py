import copy
import time
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dbv.brain.cavebot import cavebot
from dbv.brain.cavebot.adaptive_recording import hunting_excerpt, sector, stays_near_trace, watch_hunt_evidence
from dbv.brain.navigation.map_atlas import MapAtlas


def recording(xs, rewards):
    trace = [{"pos": [x, 101, 7], "t": i} for i, x in enumerate(xs)]
    for i in rewards:
        trace[i]["xp_gain"] = 230
    return {"name": "hunt", "version": 2, "started_at": 100, "atlas_scope": "scope",
            "observed_path": trace, "waypoints": [], "position_source": "client"}


def readiness(raw, monitor=None):
    stop = Mock()
    stop.wait.side_effect = [False, True]
    progress = {"stage": None}
    watch_hunt_evidence(raw, monitor or SimpleNamespace(atlas=SimpleNamespace(scope="scope")), stop, progress)
    return progress["stage"]


def test_readiness_tracks_xp_then_return_even_without_a_kill_on_return():
    xs = [*range(100, 111), *range(109, 99, -1)]
    raw = recording(xs, [0, 10])
    assert readiness(recording([], [])) == 0
    assert readiness(recording(xs, [])) == 0
    assert readiness(recording(xs[:6], [0])) == 1
    assert readiness(recording(xs[:11], [0, 10])) == 2
    assert readiness(raw) == 3
    proposal, evidence = hunting_excerpt(raw, [])
    assert evidence["areas"] == 2
    assert proposal["observed_path"][-1]["pos"][0] in range(100, 105)


def test_readiness_never_joins_separate_trips_or_counts_edge_jitter_as_a_return():
    xs = [*range(100, 111), *range(109, 99, -1)]
    raw = recording(xs, [0, 10])
    raw["observed_path"][8]["break_before"] = True
    assert readiness(raw) == 1
    assert readiness(recording([104, 105]*10, range(20))) == 2
    other_atlas = SimpleNamespace(atlas=SimpleNamespace(scope="other"))
    assert readiness(raw, other_atlas) is None


def test_repeated_hunt_keeps_its_return_evidence_when_a_new_branch_is_explored():
    lap = [*range(100, 116), *range(114, 99, -1)]
    xs = lap + lap[1:]
    raw = recording(xs, [i for i,x in enumerate(xs) if x in (100, 110, 115)])
    assert readiness(raw) == 4
    _, summary = hunting_excerpt(raw, [])
    assert summary["revisited"] >= 2
    xs = lap + list(range(101, 205))
    one_way = recording(xs, [i for i,x in enumerate(xs) if x % 5 == 0])
    assert readiness(one_way) >= 3
    proposal, evidence = hunting_excerpt(one_way, [])
    required = {sector(p["pos"]) for p in one_way["observed_path"]}
    assert {sector(p["pos"]) for p in proposal["observed_path"]} == required
    assert evidence["sectors"] == len(required)


def test_long_walk_through_xp_rooms_and_different_ladder_approach_keep_a_real_circuit():
    # Long movement without another kill stays inside the hunting footprint.
    xs = [*range(100, 117), *([115, 114, 115, 116]*14), *range(115, 99, -1)]
    raw = recording(xs, [0, 10, 16, len(xs)-1])
    trace = raw["observed_path"]
    for p in trace[14:-8]:
        p["pos"][2] = 8
    edges = []
    for i in [14, len(xs)-8]:
        before, after = trace[i-1]["pos"], trace[i]["pos"]
        x,y,z = before
        edges.append({"id":f"ladder-{i}","action":"use","item_id":1948,"evidence":"observed",
                      "origin":[x+1,y+1,z],"entry":[x,y+1,z],"exit":after})
    proposal, _ = hunting_excerpt(raw, edges)
    assert {p["pos"][2] for p in proposal["observed_path"]} == {7,8}
    assert len(proposal["observed_path"]) < len(trace)
    assert {sector(p["pos"]) for p in proposal["observed_path"]} == {sector(p["pos"]) for p in trace}


def test_repeated_hunting_rooms_exclude_town_and_choose_shorter_observed_pass(tmp_path):
    approach = list(range(70, 101))
    long_pass = [*range(101, 115), *range(115, 100, -1), 100]
    short_pass = [*range(101, 112), *range(110, 99, -1)]
    xs = approach + long_pass + short_pass + list(range(99, 60, -1))
    raw = recording(xs, [i for i, x in enumerate(xs) if x in (100, 110)])
    raw["waypoints"] = [{"type": "use_item", "t": 1}, {"type": "floor_change", "t": len(xs)-1}]
    before = copy.deepcopy(raw)
    proposal, summary = hunting_excerpt(raw, [])
    places = [p["pos"][0] for p in proposal["observed_path"]]
    assert min(places) == 100 and max(places) == 115
    assert summary["areas"] == 2 and summary["samples"] < len(xs)
    assert proposal["waypoints"] == [] and raw == before
    atlas = MapAtlas(tmp_path, "scope")
    atlas.observe({"cells": [{"x": x, "y": 101, "z": 7, "kind": "clear", "items": []}
                              for x in set(xs)], "status": "ready", "captured_at": time.time()})
    points, original, links, _ = atlas.recording_route(proposal, [], True)
    plan = atlas.plan(points, [], original, loop=True, links=links)
    assert plan["complete"] and all(100 <= p[0] <= 115 for p in plan["path"])
    assert stays_near_trace(plan["path"], proposal["observed_path"])
    assert not stays_near_trace([*plan["path"], [90, 101, 7]], proposal["observed_path"])
    assert not stays_near_trace([[100, 101, 6]], proposal["observed_path"])


def test_idle_crowded_town_one_way_walk_and_repeated_edge_jitter_are_not_hunts():
    for raw in [recording([100, 101, 102]*8, []),
                recording(list(range(100, 125)), [2, 9, 12, 18]),
                recording([104, 105]*10, range(20))]:
        with pytest.raises(ValueError):
            hunting_excerpt(raw, [])


def test_road_without_xp_cannot_connect_visits_through_town():
    xs = [*range(100, 150), *range(149, 99, -1)]
    with pytest.raises(ValueError):
        hunting_excerpt(recording(xs, [0, 8, len(xs)-9, len(xs)-1]), [])


def test_floor_crossings_need_observed_links_and_keep_interaction_times():
    raw = recording([100, 101, 102, 103, 104, 105, 106, 105, 104, 103, 102, 101, 100],
                    [0, 6, 12])
    trace = raw["observed_path"]
    for p in trace[3:10]:
        p["pos"][2] = 8
    raw["waypoints"] = [{"type":"use_item", "t":2.5, "player_pos":trace[2]["pos"]}]
    edges = [{"id":f"cross-{i}", "evidence":"observed", "action":"step", "origin":trace[i]["pos"], "entry":trace[i]["pos"],
              "exit":trace[i+1]["pos"]} for i in (2, 9)]
    with pytest.raises(ValueError):
        hunting_excerpt(raw, [])
    result, _ = hunting_excerpt(raw, edges)
    atlas = Mock(scope="scope")
    atlas.with_recorded_ropes.side_effect = list
    atlas.recording_step_observations.side_effect = list
    atlas.infer_recorded_ropes.return_value = []
    monitor = SimpleNamespace(atlas=atlas, learner=SimpleNamespace(scope="scope", records={}))
    assert readiness(raw, monitor) == 1
    atlas.infer_recorded_ropes.return_value = edges
    assert readiness(raw, monitor) == 3
    atlas.remember_recorded_ropes.assert_not_called()
    assert result["waypoints"] == raw["waypoints"]
    crossings = lambda path: [(a["pos"], b["pos"]) for a,b in zip(path,path[1:]) if a["pos"][2]!=b["pos"][2]]
    assert crossings(result["observed_path"]) == crossings(trace)
    trace[6]["break_before"] = True
    with pytest.raises(ValueError):
        hunting_excerpt(raw, edges)


def test_recorder_collects_actual_xp_once_even_while_standing_and_avoids_relogin_delta(monkeypatch):
    gs = SimpleNamespace(position=(100, 101, 7), server_events=deque(), experience=12000, level=10)
    st = SimpleNamespace(connected=True, recording_active=False, game_state=gs, game_proxy=Mock(),
                         passive_map_monitor=SimpleNamespace(atlas=SimpleNamespace(scope="scope")))
    save = Mock()
    monkeypatch.setattr(cavebot, "save_recording", save)
    assert cavebot.start_recording(st, "hunt", mode="adaptive") is None
    assert st.recording_hunt_progress == {"stage": 0}
    gs.experience += 200
    cavebot._sample_recording_position(st)
    cavebot._sample_recording_position(st)
    gs.position = (101, 101, 7)
    cavebot._sample_recording_position(st)
    gs.experience = 7593463832759511511
    cavebot._sample_recording_position(st)
    gs.experience = 12200
    cavebot._sample_recording_position(st)
    st.game_state = SimpleNamespace(position=gs.position, server_events=deque(), experience=99999, level=20)
    raw = cavebot.stop_recording(st)
    assert st.recording_hunt_progress is None
    save.assert_called_once_with(raw)
    assert raw["recording_mode"] == "adaptive" and raw["atlas_scope"] == "scope"
    trace = raw["observed_path"]
    assert sum(p.get("xp_gain", 0) for p in trace) == 200
    assert trace[0]["xp_gain"] == 200 and trace[-1]["break_before"]


def test_one_gathering_point_with_delayed_xp_and_alternating_side_rooms():
    xs = [*range(100,116),*range(114,99,-1),*range(99,84,-1),*range(86,101)]
    raw = recording(xs, [0,30,60])
    proposal, summary = hunting_excerpt(raw,[])
    assert summary["sectors"] == len({x//5 for x in xs}) and summary["total_areas"] == 1
    assert {sector(p["pos"]) for p in proposal["observed_path"]} == {sector(p["pos"]) for p in raw["observed_path"]}
    assert readiness(raw) == 4


def test_separate_unconnected_hunts_do_not_produce_partial_full_coverage():
    lap = [*range(100,116),*range(114,99,-1)]
    raw = recording(lap+[x+100 for x in lap], [0,10,30,31,41,61])
    raw["observed_path"][31]["break_before"] = True
    with pytest.raises(ValueError, match="rozłączne"):
        hunting_excerpt(raw,[])


def test_passive_encounters_keep_origin_and_distinguish_target_aoe_and_disappearance():
    from dbv.brain.hunts.hunt_encounters import HuntEncounters
    from unittest.mock import patch
    gs = SimpleNamespace(position=(100,101,7), creatures={1:{}}, session_kills=0, kill_log=[], attack_target_id=11)
    recorder = HuntEncounters()
    monitor = SimpleNamespace(snapshot=lambda:{})
    visible = [dict(id=i,name="Brolly",position=[100+i,102,7]) for i in (11,12,13)]
    with patch("dbv.brain.hunts.hunt_encounters.eligible_monsters",return_value=visible):
        recorder.sample(gs,monitor,0,100,0,False)
    gs.position=(120,101,7)
    gs.creatures={}
    gs.session_kills=3
    gs.kill_log=[dict(cid=i,t=101,name="Brolly",x=120,y=102,z=7) for i in (11,12,99)]
    recorder.sample(gs,monitor,8,101,0,False)
    recorder.sample(gs,monitor,9,101.5,400,False)
    assert [e["outcome"] for e in recorder.events] == ["target_xp","nearby_xp","seen"]
    assert recorder.events[0]["first_pos"] == [111,102,7]
    assert recorder.events[0]["death_pos"] == [120,102,7]
    assert recorder.events[0]["player_pos"] == [100,101,7]
    assert "death_pos" not in recorder.events[2]
    recorder.sample(gs,monitor,10,102,100,True)
    assert len(recorder.events)==3 and not recorder.active


def test_equally_near_branches_prefer_recent_hunted_encounter_and_keep_both():
    from dbv.brain.cavebot.adaptive_recording import _covering_circuit, _replay_edges
    a, left, up = [100,100,7],[99,100,7],[100,99,7]
    trace = [{"t":i,"pos":pos} for i,pos in enumerate((a,left,a,up,a))]
    edges = _covering_circuit(trace,0,4,{sector(left):1,sector(up):3},set(),set())
    samples,_ = _replay_edges({"observed_path":trace},edges)
    assert [p["pos"] for p in samples] == [a,up,a,left,a]


def test_step_variants_match_cached_entrance_and_reject_conflicting_exits(tmp_path):
    atlas = MapAtlas(tmp_path,"scope")
    entry = [100,100,7]
    atlas.observe(dict(status="ready",captured_at=time.time(),cells=[
        dict(x=100,y=100,z=7,kind="clear",items=[dict(id=476,flags=[],stack=0)])]))
    common = dict(evidence="observed",action="step",reason="matched_command",
                  entry=entry,origin=[99,100,7],exit=[100,100,8])
    variants = [dict(common,id=str(item),entry_items=[item]) for item in (475,476)]
    assert atlas.recording_step_observations(variants) == [variants[1]]
    conflict = [*variants,dict(common,id="elsewhere",entry_items=[475],exit=[101,100,8])]
    assert atlas.recording_step_observations(conflict) == conflict
    trace = {"observed_path":[{"pos":common["origin"]},{"pos":common["exit"]}]}
    pair = (tuple(common["origin"]),tuple(common["exit"]))
    assert atlas.unavailable_recorded_crossings(trace,[variants[0]]) == {pair}
    assert not atlas.unavailable_recorded_crossings(trace,[variants[1]])
