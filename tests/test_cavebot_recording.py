"""Recorder regressions using synthetic packets; never attaches to the game."""
from collections import deque
import struct
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import cavebot
from protocol import ClientOpcode, PacketReader


def new_walk(directions, origin=(500, 900, 7), count=None):
    return struct.pack("<IIHHBBH", 3, 4, *origin, 1,
                       len(directions) if count is None else count) + bytes(directions)


def test_new_walk_preserves_order_turns_and_packet_origin():
    steps = cavebot._read_new_walk(PacketReader(new_walk([1, 1, 3, 8])), 2.5)
    assert [s["direction"] for s in steps] == ["east", "east", "north", "southeast"]
    assert [s["pos"] for s in steps] == [[501, 900, 7], [502, 900, 7], [502, 899, 7], [503, 900, 7]]
    assert steps[0]["player_pos"] == [500, 900, 7]
    assert steps[-1]["player_pos"] == [502, 899, 7]
    assert all(s["t"] == 2.5 for s in steps)


@pytest.mark.parametrize("payload", [
    b"", new_walk([1])[:-1], new_walk([1, 0]), new_walk([1, 9]),
    new_walk([]), new_walk([1], count=4097), new_walk([1], origin=(500, 900, 16)),
    new_walk([1], origin=(65534, 900, 7)),
])
def test_invalid_new_walk_does_not_record_partial_path(payload):
    assert cavebot._read_new_walk(PacketReader(payload), 1) == []


def test_recording_callback_saves_new_movement_and_only_current_events(monkeypatch):
    gs = SimpleNamespace(position=(100, 200, 7), server_events=deque([
        (90, "floor_change_up", {"pos": [10, 20, 6], "z": 6}),
        (99.99, "cancel_walk", {"pos": [10, 20, 6]}),
    ]))
    proxy = Mock()
    st = SimpleNamespace(recording_active=False, game_state=gs, game_proxy=proxy)
    monkeypatch.setattr(cavebot.time, "time", lambda: 100.0)
    saved = Mock()
    monkeypatch.setattr(cavebot, "save_recording", saved)
    assert cavebot.start_recording(st, "synthetic") is None
    cb = proxy.register_client_packet_callback.call_args.args[0]
    # Deliberately use a newer packet origin than gs.position.
    cb(ClientOpcode.NEW_WALK, PacketReader(new_walk([1, 3], origin=(102, 200, 7))))
    gs.server_events.append((100.2, "floor_change_up", {"pos": [103, 199, 6], "z": 6}))
    rec = cavebot.stop_recording(st)
    assert [wp["type"] for wp in rec["waypoints"]] == ["walk", "walk", "floor_change"]
    assert rec["waypoints"][0]["player_pos"] == [102, 200, 7]
    assert rec["waypoints"][-1]["pos"] == [103, 199, 6]
    assert all(wp["t"] >= 0 for wp in rec["waypoints"])
    assert cavebot.build_actions_map(rec)
    saved.assert_called_once_with(rec)
    proxy.unregister_client_packet_callback.assert_called_once_with(cb)
    assert st._recording_event_thread is None
    # A callback retained by a concurrent relay cannot append after stopping.
    cb(ClientOpcode.NEW_WALK, PacketReader(new_walk([7])))
    assert len(rec["waypoints"]) == 3


def test_legacy_keyboard_walk_is_still_recorded(monkeypatch):
    st = SimpleNamespace(recording_active=False, game_proxy=Mock(),
                         game_state=SimpleNamespace(position=(100, 200, 7), server_events=deque()))
    monkeypatch.setattr(cavebot, "save_recording", Mock())
    cavebot.start_recording(st, "legacy")
    st._recording_callback(ClientOpcode.WALK_SOUTHEAST, PacketReader(b""))
    rec = cavebot.stop_recording(st)
    assert rec["waypoints"][0]["pos"] == [101, 201, 7]


def test_recording_ending_on_stairs_keeps_exact_approach():
    waypoints = cavebot._read_new_walk(PacketReader(new_walk([1])), 1)
    waypoints.append({"type": "floor_change", "pos": [502, 899, 6], "z": 6, "t": 1.2})
    nodes = cavebot.build_actions_map({"waypoints": waypoints})
    assert nodes[-1]["target"] == [501, 900, 7]
    assert nodes[-1]["exact"] is True


def test_observed_trace_samples_only_fresh_positions_and_marks_gaps(monkeypatch):
    clock=[100.0]
    monkeypatch.setattr(cavebot.time,"monotonic",lambda:clock[0])
    monkeypatch.setattr(cavebot.time,"time",lambda:clock[0])
    gs=SimpleNamespace(client_state_required=True,client_snapshot={"monotonic":100,"position":[10,10,7]})
    st=SimpleNamespace(game_state=gs,connected=True,recording_observed_path=[],recording_start_time=100,recording_trace_limited=False)
    cavebot._sample_recording_position(st)
    clock[0]=100.1;cavebot._sample_recording_position(st)
    assert len(st.recording_observed_path)==1
    clock[0]=102;cavebot._sample_recording_position(st)
    assert len(st.recording_observed_path)==1
    gs.client_snapshot={"monotonic":102,"position":[10,9,6]}
    cavebot._sample_recording_position(st)
    assert st.recording_observed_path[-1]=={"pos":[10,9,6],"t":2,"break_before":True}
    st.connected=False;gs.client_snapshot={"monotonic":102,"position":[11,9,6]}
    cavebot._sample_recording_position(st)
    assert len(st.recording_observed_path)==2


@pytest.mark.parametrize("tail,target,stack",[(struct.pack('<HB',606,2),606,2),(bytes([3]),None,3)])
def test_tool_recording_keeps_target_stack_in_both_formats(monkeypatch,tail,target,stack):
    st=SimpleNamespace(recording_active=False,game_proxy=Mock(),game_state=SimpleNamespace(position=(100,200,7),server_events=deque()))
    monkeypatch.setattr(cavebot,"save_recording",Mock())
    cavebot.start_recording(st,"tool")
    data=struct.pack('<HHBHBHHB',65535,0,0,3457,0,100,199,7)+tail
    st._recording_callback(ClientOpcode.USE_ITEM_EX,PacketReader(data))
    rec=cavebot.stop_recording(st)
    assert rec["waypoints"][0]["to_stack_pos"]==stack
    assert rec["waypoints"][0]["target_item_id"]==target
