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
