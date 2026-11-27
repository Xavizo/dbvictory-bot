"""Food, combat and packet diagnostics decide the same things as before the rewrite."""
import struct
from types import SimpleNamespace

from actions import auto_combat, eat_food, packet_sniffer
from dbv.client_bridge.protocol import ClientOpcode, build_use_item_packet

NOW = 500.0


def gs(creatures=(), target=None, mana=50, max_mana=100):
    return SimpleNamespace(position=(10, 10, 7), creatures=dict(creatures), attack_target=target,
                           mana=mana, max_mana=max_mana)


def monster(x, y, health=100, z=7, seen=NOW):
    return {"x": x, "y": y, "z": z, "health": health, "t": seen}


def test_food_uses_the_hotkey_form_for_red_ham():
    assert eat_food.eat_packet() == build_use_item_packet(0xFFFF, 0, 0, 3583, 0, 0)


def test_two_adjacent_monsters_trigger_the_area_spell():
    two = {0x40000001: monster(11, 10), 0x40000002: monster(9, 9)}
    assert auto_combat.choose(gs(two), None, NOW) == ("say", "Energy Grenade")
    far_or_other_floor = {0x40000001: monster(11, 10), 0x40000002: monster(12, 10),
                          0x40000003: monster(10, 11, z=6), 0x40000004: monster(9, 10, seen=NOW - 61)}
    assert auto_combat.choose(gs(far_or_other_floor), None, NOW) is None


def test_target_gets_the_spell_or_the_rune_by_mana():
    one = {0x40000001: monster(13, 10)}
    assert auto_combat.choose(gs(one, target=0x40000001), None, NOW) == ("say", "Darkness Blaster")
    assert auto_combat.choose(gs(one, target=0x40000001, mana=4), None, NOW) == ("rune", 0x40000001)
    assert auto_combat.choose(gs(one, mana=4), 0x40000001, NOW) == ("rune", 0x40000001)  # clicked target
    assert auto_combat.choose(gs(target=0x40000009, max_mana=0), None, NOW) == ("say", "Darkness Blaster")


def test_dead_target_is_left_alone():
    assert auto_combat.choose(gs({0x40000001: monster(13, 10, health=0)}, target=0x40000001), None, NOW) is None


def test_packet_bytes_are_hidden_unless_capture_is_allowed():
    payload = struct.pack("<HHBHB", 100, 200, 7, 3583, 1)
    assert packet_sniffer.describe(ClientOpcode.USE_ITEM, payload, False) == [
        f"0x{int(ClientOpcode.USE_ITEM):02X} (USE_ITEM) [8 bytes] [redacted]"]
    shown = packet_sniffer.describe(ClientOpcode.USE_ITEM, payload, True)
    assert shown[1] == "  -> USE_ITEM pos=(100, 200, 7) item_id=3583 stack=1"
    assert packet_sniffer.describe(0xFE, b"", True) == ["0xFE (?) [0 bytes] (empty)"]
