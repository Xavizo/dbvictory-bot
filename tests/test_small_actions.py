"""Food and packet diagnostics decide the same things as before the rewrite."""
import struct

from actions import eat_food, packet_sniffer
from dbv.client_bridge.protocol import ClientOpcode, build_use_item_packet


def test_food_uses_the_hotkey_form_for_red_ham():
    assert eat_food.eat_packet() == build_use_item_packet(0xFFFF, 0, 0, 3583, 0, 0)


def test_packet_bytes_are_hidden_unless_capture_is_allowed():
    payload = struct.pack("<HHBHB", 100, 200, 7, 3583, 1)
    assert packet_sniffer.describe(ClientOpcode.USE_ITEM, payload, False) == [
        f"0x{int(ClientOpcode.USE_ITEM):02X} (USE_ITEM) [8 bytes] [redacted]"]
    shown = packet_sniffer.describe(ClientOpcode.USE_ITEM, payload, True)
    assert shown[1] == "  -> USE_ITEM pos=(100, 200, 7) item_id=3583 stack=1"
    assert packet_sniffer.describe(0xFE, b"", True) == ["0xFE (?) [0 bytes] (empty)"]
