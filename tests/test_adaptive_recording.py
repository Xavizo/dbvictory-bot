import copy
import time
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import cavebot
from adaptive_recording import hunting_excerpt, stays_near_trace
from map_atlas import MapAtlas


def recording(xs, rewards):
    trace = [{"pos": [x, 101, 7], "t": i} for i, x in enumerate(xs)]
    for i in rewards:
        trace[i]["xp_gain"] = 230
    return {"name": "hunt", "version": 2, "started_at": 100, "atlas_scope": "scope",
            "observed_path": trace, "waypoints": [], "position_source": "client"}


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
    assert 100 in places and 110 in places and min(places) >= 100 and max(places) <= 111
    assert summary["areas"] == 2 and summary["samples"] < len(xs)
    assert proposal["waypoints"] == [] and raw == before
    atlas = MapAtlas(tmp_path, "scope")
    atlas.observe({"cells": [{"x": x, "y": 101, "z": 7, "kind": "clear", "items": []}
                              for x in set(xs)], "status": "ready", "captured_at": time.time()})
    points, original, links, _ = atlas.recording_route(proposal, [], True)
    plan = atlas.plan(points, [], original, loop=True, links=links)
    assert plan["complete"] and all(100 <= p[0] <= 111 for p in plan["path"])
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
    edges = [{"evidence":"observed", "origin":trace[i]["pos"], "entry":trace[i]["pos"],
              "exit":trace[i+1]["pos"]} for i in (2, 9)]
    with pytest.raises(ValueError):
        hunting_excerpt(raw, [])
    result, _ = hunting_excerpt(raw, edges)
    assert result["waypoints"] == raw["waypoints"] and result["observed_path"] == trace
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
    save.assert_called_once_with(raw)
    assert raw["recording_mode"] == "adaptive" and raw["atlas_scope"] == "scope"
    trace = raw["observed_path"]
    assert sum(p.get("xp_gain", 0) for p in trace) == 200
    assert trace[0]["xp_gain"] == 200 and trace[-1]["break_before"]
