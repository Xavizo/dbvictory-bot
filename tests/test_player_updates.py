"""Regressions for DBV v5 initial state and delayed HP updates."""
import struct

import pytest
from dbv.brain.state import game_state as module
from dbv.brain.state.game_state import GameState, scan_packet


@pytest.fixture(autouse=True)
def no_debug_files(monkeypatch):
    monkeypatch.setattr(module, "_dump_stats_debug", lambda *args: None)


def stats(hp=100, capacity=492400, level=167, mana=76):
    return b"\xa0" + struct.pack("<IIIQHB IIBBBH", hp, 100, capacity,
                                  75543871, level, 48, mana, 100, 64, 0, 0, 2516)


def test_initial_stats_after_unparsed_map_are_accepted():
    state = GameState()
    scan_packet(b"\x00map-prefix" + stats(), state)
    assert (state.hp, state.max_hp, state.level, state.mana) == (100, 100, 167, 76)
    assert state.stats_updated_at > 0


def test_bonus_ki_does_not_hide_initial_health():
    state = GameState()
    scan_packet(login(x=136, y=586) + b"map-data" + stats(mana=119), state)
    assert (state.hp, state.max_hp, state.mana, state.max_mana) == (100, 100, 119, 100)
    assert state.stats_updated_at > 0


def test_bonus_ki_does_not_hide_bundled_damage():
    state = GameState()
    scan_packet(b"\x00prefix" + stats(hp=86, mana=119), state)
    assert (state.hp, state.mana) == (86, 119)


def test_bonus_ki_does_not_disable_sanity_bound():
    state = GameState()
    scan_packet(b"\x00prefix" + stats(mana=module.MAX_VALID_MANA + 1), state)
    assert state.max_hp == 0


def test_legacy_profile_can_reject_over_max_mana(monkeypatch):
    monkeypatch.setattr(module, "PF", {**module.PF, "player_stats": {**module.PF["player_stats"], "allow_mana_over_max": False}})
    state = GameState()
    scan_packet(b"\x00prefix" + stats(mana=119), state)
    assert state.max_hp == 0


def test_damage_and_healing_update_without_needing_another_packet():
    state = GameState()
    for hp in [100, 83, 67, 92, 0]:
        scan_packet(b"\x00prefix" + stats(hp=hp), state)
        assert state.hp == hp
        assert state.max_hp == 100


def test_newest_stats_in_a_bundled_packet_win():
    state = GameState()
    scan_packet(b"\x00prefix" + stats(hp=95) + b"noise" + stats(hp=71), state)
    assert state.hp == 71


def test_zero_capacity_does_not_hide_valid_health():
    state = GameState()
    scan_packet(b"\x00" + stats(hp=50, capacity=0), state)
    assert state.hp == 50


@pytest.mark.parametrize("data", [stats(capacity=10000001), stats(hp=101), stats(level=0), stats()[:-1]])
def test_invalid_or_truncated_fallback_does_not_mutate_stats(data):
    state = GameState()
    state.hp, state.max_hp = 60, 100
    scan_packet(b"\x00" + data, state)
    assert (state.hp, state.max_hp) == (60, 100)


def login(x=671, y=156, z=6, marker=0x4b):
    # player id, v5 header (18 bytes), map marker, then a packed position.
    return b"\x0a" + struct.pack("<I", 0x10000130) + bytes(18) + bytes([marker]) + struct.pack("<HHB", x, y, z)


def test_v5_login_initializes_both_position_sources():
    state = GameState()
    scan_packet(login(), state)
    assert state.position == state.packet_position == (671, 156, 6)
    assert state.player_id == 0x10000130
    assert state.last_map_time > 0


@pytest.mark.parametrize("data", [login(marker=0), login(z=255), login(x=0), login()[:-1]])
def test_bad_login_position_is_not_guessed(data):
    state = GameState()
    scan_packet(data, state)
    assert state.position == state.packet_position == (0, 0, 0)


def test_login_position_then_server_movement():
    state = GameState()
    scan_packet(login(), state)
    scan_packet(bytes([0x66]), state)
    assert state.position == state.packet_position == (672, 156, 6)


def test_legacy_profile_is_still_supported(monkeypatch):
    profile = {**module.PF, "login_or_pending": {"header_before_position": 4, "map_description_marker": 0x64}}
    monkeypatch.setattr(module, "PF", profile)
    state = GameState()
    packet = b"\x0a" + struct.pack("<I", 0x10000130) + bytes(3) + b"\x64" + struct.pack("<HHB", 1000, 2000, 7)
    scan_packet(packet, state)
    assert state.position == (1000, 2000, 7)


def creature_move(old=(83, 90, 7), new=(82, 90, 7), duration=100):
    return b"\x6d" + struct.pack("<HHBBHHBH", *old, 1, *new, duration)


def test_new_walking_duration_cannot_become_map_opcode():
    state = GameState()
    scan_packet(login(x=80, y=95), state)
    for duration in [100, 101, 102, 103, 104, 2660]:
        scan_packet(creature_move(duration=duration) + stats(hp=87), state)
        assert state.position == state.packet_position == (80, 95, 6)
        assert state.hp == 87


@pytest.mark.parametrize("prefix", [b"", b"\x1d", b"\x1e"])
def test_new_walking_move_then_map_slice(prefix):
    state = GameState()
    scan_packet(login(x=80, y=95, z=7), state)
    scan_packet(prefix + creature_move(old=(80, 95, 7), new=(81, 95, 7))
                + b"\x66tiles" + stats(hp=73), state)
    assert state.position == state.packet_position == (81, 95, 7)
    assert state.hp == 73


def test_legacy_slice_seeds_before_increment():
    state = GameState()
    state.position = (80, 95, 7)
    scan_packet(b"\x66", state)
    assert state.position == state.packet_position == (81, 95, 7)


@pytest.mark.parametrize("new,strip", [
    ((80, 94, 7), 0x65), ((81, 95, 7), 0x66), ((80, 96, 7), 0x67), ((79, 95, 7), 0x68),
    ((81, 94, 7), 0x65), ((81, 96, 7), 0x67), ((79, 96, 7), 0x67), ((79, 94, 7), 0x65),
])
def test_move_corrects_stale_position_in_all_directions(new, strip):
    state = GameState()
    state.position = state.packet_position = (37376, 386, 0)
    scan_packet(creature_move(old=(80, 95, 7), new=new) + bytes([strip]) + b"tiles", state)
    assert state.position == state.packet_position == new


@pytest.mark.parametrize("length", [1, 6, 11, 12, 13])
def test_truncated_move_does_not_move_player(length):
    state = GameState()
    state.position = state.packet_position = (80, 95, 7)
    scan_packet(creature_move()[:length], state)
    assert state.position == state.packet_position == (80, 95, 7)


def test_floor_description_accepts_own_floor_only(monkeypatch):
    monkeypatch.setitem(module.PF, "floor_description", {"opcode": 0x4b})
    state = GameState()
    for floor in [7, 8]:
        scan_packet(b"\x4b" + struct.pack("<HHBB", 80, 95, 8, floor), state)
        expected = (0, 0, 0) if floor == 7 else (80, 95, 8)
        assert state.position == state.packet_position == expected


def test_move_then_floor_map_updates_floor_without_waiting_for_dll():
    state = GameState()
    scan_packet(login(x=80, y=95, z=7), state)
    # The first described floor need not be the player's new floor.
    scan_packet(creature_move(old=(80, 95, 7), new=(81, 96, 8))
                + b"\x4b" + struct.pack("<HHBB", 81, 96, 8, 7) + b"tiles", state)
    assert state.position == state.packet_position == (81, 96, 8)


@pytest.mark.parametrize("payload", [b"", struct.pack("<HHB", 80, 95, 255), struct.pack("<HHB", 0, 95, 7)])
def test_invalid_map_description_does_not_change_position(payload):
    state = GameState()
    state.position = state.packet_position = (80, 95, 7)
    scan_packet(b"\x64" + payload, state)
    assert state.position == state.packet_position == (80, 95, 7)


def test_map_like_byte_in_unknown_payload_does_not_change_position():
    state = GameState()
    state.position = state.packet_position = (80, 95, 7)
    scan_packet(b"\x00unknown\x64" + struct.pack("<HHB", 123, 456, 7), state)
    assert state.position == state.packet_position == (80, 95, 7)


def floors(center, described):
    # Synthetic tile bodies; no captured game payloads are kept in tests.
    return b"".join(b"\x4b" + struct.pack("<HHBB", *center, floor) + b"opaque tiles"
                    for floor in described)


@pytest.mark.parametrize("center,described", [((78, 91, 6), range(7, -1, -1)),
                                           ((78, 93, 8), range(6, 11))])
def test_stair_refresh_updates_xyz_without_another_step(center, described):
    state = GameState()
    scan_packet(login(x=78, y=93, z=7), state)
    scan_packet(creature_move(old=(78, 93, 7), new=(78, 92, 7)) + b"\x65opaque strip"
                + floors(center, described) + stats(hp=82), state)
    assert state.position == state.packet_position == center
    assert state.hp == 82
    events = [evt for evt in state.server_events if evt[1].startswith("floor_change")]
    assert len(events) == 1
    assert events[0][1] == ("floor_change_up" if center[2] < 7 else "floor_change_down")
    assert events[0][2] == {"pos": list(center), "z": center[2]}
    # A repeated floor description must not produce another stair event.
    scan_packet(floors(center, [center[2]]), state)
    assert len([evt for evt in state.server_events if evt[1].startswith("floor_change")]) == 1


@pytest.mark.parametrize("center,described", [
    ((78, 91, 6), [6]), ((78, 91, 6), [7, 6]), ((78, 91, 6), [7, 6, 7]),
    ((78, 91, 6), [6, 6, 6]), ((78, 91, 6), [2, 3, 4]),
    ((78, 91, 6), [7, 6, 3]), ((700, 900, 6), [7, 6, 5]),
    ((78, 91, 3), [4, 3, 2]), ((78, 91, 255), [7, 6, 5]),
])
def test_unconfirmed_floor_patterns_cannot_change_position(center, described):
    state = GameState()
    scan_packet(login(x=78, y=93, z=7), state)
    scan_packet(creature_move(old=(78, 93, 7), new=(78, 92, 7)) + b"\x65opaque strip"
                + floors(center, described), state)
    assert state.position == state.packet_position == (78, 92, 7)


def test_floor_patterns_outside_a_map_context_are_ignored():
    state = GameState()
    state.position = state.packet_position = (78, 92, 7)
    scan_packet(b"\x00unknown" + floors((78, 91, 6), range(7, -1, -1)), state)
    assert state.position == state.packet_position == (78, 92, 7)


def test_bundled_stats_after_a_known_stats_message_are_not_stale():
    state = GameState()
    scan_packet(stats(hp=100) + login(x=78, y=93) + stats(hp=71), state)
    assert state.hp == 71


def test_dll_percentage_cannot_overwrite_exact_packet_health():
    from actions import dll_bridge as action
    state = GameState()
    state.player_id = 0x10000130
    state.hp, state.max_hp = 57, 123
    state.position = state.packet_position = (671, 156, 6)
    snapshot = [{"id": state.player_id, "name": "Hero", "hp": 100, "x": 670, "y": 156, "z": 6},
                {"id": 0x40000001, "name": "Saibaman", "hp": 80, "x": 672, "y": 157, "z": 6}]
    name, monsters = action.nearby_monsters(snapshot, {state.player_id}, state.position, 1000.0)
    action._merge(state, monsters, 1000.0)
    assert name == "Hero" and state.player_id not in state.creatures
    # The DLL frame is shifted onto the client's own coordinates.
    assert state.creatures[0x40000001]["x"] == 673
    assert (state.hp, state.max_hp) == (57, 123)
    assert state.position == state.packet_position == (671, 156, 6)
