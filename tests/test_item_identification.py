"""Item identification reports the ID of an item the player looks at."""
from dbv.brain.state.item_identification import ItemIdentification, status_message
from dbv.client_bridge.protocol import ClientOpcode, PacketReader, ServerOpcode, build_look_packet, build_walk_packet, Direction


def look(ident, packet):
    reader = PacketReader(packet)
    return ident.record_client_packet(reader.read_u8(), reader)


def test_reports_looked_items_newest_first():
    ident = ItemIdentification(clock=lambda: 50.0)
    assert look(ident, build_look_packet(100, 200, 7, 3031, 1)) == 3031
    assert look(ident, build_look_packet(0xFFFF, 64, 0, 5908, 0)) == 5908
    recent = ident.snapshot()["recent"]
    assert recent[0] == {"item_id": 5908, "where": "inventory", "position": None, "time": 50.0}
    assert recent[1] == {"item_id": 3031, "where": "map", "position": [100, 200, 7], "time": 50.0}


def test_ignores_creatures_other_packets_and_disabled_state():
    ident = ItemIdentification()
    assert look(ident, build_look_packet(100, 200, 7, 99, 1)) is None
    assert look(ident, build_walk_packet(Direction.NORTH)) is None
    ident.enabled = False
    assert look(ident, build_look_packet(100, 200, 7, 3031, 1)) is None
    assert ident.snapshot() == {"enabled": False, "recent": []}


def test_keeps_only_recent_items():
    ident = ItemIdentification()
    for item_id in range(1000, 1030):
        look(ident, build_look_packet(1, 1, 7, item_id, 0))
    recent = ident.snapshot()["recent"]
    assert len(recent) == 20 and recent[0]["item_id"] == 1029


def test_status_message_is_a_status_bar_text():
    reader = PacketReader(status_message(3031))
    assert reader.read_u8() == ServerOpcode.TEXT_MESSAGE
    assert reader.read_u8() == 26
    assert reader.read_string() == "Item Id: 3031"


def test_enabled_by_default_in_new_settings(tmp_path, monkeypatch):
    from dbv.mcp_server import server
    monkeypatch.setattr(server, "SETTINGS_FILE", tmp_path / "missing.json")
    assert server.BotState().item_identification.enabled
