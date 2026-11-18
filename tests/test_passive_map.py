"""Synthetic map fixtures: no process reads, attachment or game commands."""
import struct
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import passive_map as pm
from map_navigation import navigation_features, load_confirmed_types, load_location_notes, ASSET_SHA256


class Memory:
    def __init__(self):
        self.regions = {}

    def put(self, addr, data):
        self.regions[addr] = bytearray(data)

    def read(self, addr, size):
        for start, data in self.regions.items():
            if start <= addr and addr + size <= start + len(data):
                return bytes(data[addr-start:addr-start+size])
        raise pm.MapReadError("unreadable synthetic memory")


@pytest.fixture
def world():
    mem = Memory()
    base, head, node, tile, things, item, types, definition, attrs = (
        0x400000, 0x10000, 0x20000, 0x30000, 0x31000, 0x32000, 0x40000, 0x60000, 0x61000)
    mem.put(base + pm.MAP_CENTER_RVA, struct.pack("<III", 100, 100, 7))
    mem.put(base + pm.MAP_BLOCKS_RVA + 7*8, struct.pack("<II", head, 1))
    header = bytearray(16)
    struct.pack_into("<III", header, 0, node, node, node)
    header[13] = 1
    mem.put(head, header)
    block = bytearray(20+8192)
    struct.pack_into("<5I", block, 0, head, head, head, 0, 3*2048+3)
    struct.pack_into("<I", block, 20+(4*32+4)*8, tile)
    mem.put(node, block)
    raw = bytearray(84)
    struct.pack_into("<I", raw, 0, base + pm.TILE_VTABLE_RVA)
    struct.pack_into("<III", raw, 44, things, things+8, things+8)
    struct.pack_into("<III", raw, 56, 100, 100, 7)
    struct.pack_into("<H", raw, 80, 150)
    mem.put(tile, raw)
    mem.put(things, struct.pack("<II", item, 0))
    raw = bytearray(56)
    struct.pack_into("<I", raw, 0, base + pm.ITEM_VTABLE_RVA)
    struct.pack_into("<H", raw, 0x35, 4560)
    mem.put(item, raw)
    mem.put(base + pm.ITEM_TYPES_RVA, struct.pack("<III", types, types+10162*8, types+10162*8))
    raw = bytearray(10162*8)
    struct.pack_into("<I", raw, 4560*8, definition)
    mem.put(types, raw)
    raw = bytearray(44)
    struct.pack_into("<I", raw, 0, base + pm.TYPE_VTABLE_RVA)
    struct.pack_into("<H", raw, 26, 4560)
    struct.pack_into("<III", raw, 32, attrs, attrs+31*4, attrs+31*4)
    mem.put(definition, raw)
    raw = bytearray(31*4)
    struct.pack_into("<I", raw, 0, 0x70000)  # ground attribute present
    mem.put(attrs, raw)
    return SimpleNamespace(mem=mem, reader=pm.MapReader(mem.read, base), base=base,
                           head=head, node=node, tile=tile, item=item, definition=definition, attrs=attrs)


def center_cell(snapshot):
    return next(c for c in snapshot["cells"] if (c["x"], c["y"]) == (100,100))


def test_snapshot_uses_verified_item_definition_and_known_stairs(world):
    snapshot = world.reader.snapshot()
    assert len(snapshot["cells"]) == 252
    assert snapshot["center"] == [100,100,7]
    cell = center_cell(snapshot)
    assert cell["kind"] == "clear" and cell["stairs"] == "confirmed_id"
    assert cell["items"] == [{"id":4560, "flags":["ground"], "stack":0}]
    assert snapshot["counts"] == {"unknown":251,"clear":1}


def test_updated_profile_decodes_relocated_map_without_old_confirmations(world):
    from client_profiles import LEGACY, UPDATED
    mem = world.mem
    for field in ("map_center", "item_types"):
        mem.regions[world.base+getattr(UPDATED,field)] = mem.regions.pop(world.base+getattr(LEGACY,field))
    mem.regions[world.base+UPDATED.map_blocks+7*8] = mem.regions.pop(world.base+LEGACY.map_blocks+7*8)
    for address, field in ((world.tile,"tile_vtable"),(world.item,"item_vtable"),(world.definition,"type_vtable")):
        struct.pack_into("<I",mem.regions[address],0,world.base+getattr(UPDATED,field))
    reader=pm.MapReader(mem.read,world.base,allow_confirmed=False,profile=UPDATED)
    snapshot=reader.snapshot()
    assert snapshot["center"]==[100,100,7]
    cell=center_cell(snapshot)
    assert cell["kind"]=="clear" and cell["items"][0]["id"]==4560
    assert cell["stairs"] is None  # Old build's ID confirmations do not transfer silently.
    assert not reader.navigation_types and not reader.location_notes


@pytest.mark.parametrize("rva,kind",[(0x8B6974,"player"),(0x8B6A50,"self"),(0x8BCCB0,"monster"),(0x8BCD70,"npc"),(0x8B63E4,"unknown")])
@pytest.mark.parametrize("cid", [0x10000042, 0])
def test_creature_kind_uses_verified_runtime_class_not_id_or_name(world,rva,kind,cid):
    raw=bytearray(56)
    struct.pack_into("<I",raw,0,world.base+rva)
    struct.pack_into("<I",raw,0x34,cid)
    world.mem.put(0x75000,raw)
    table=bytearray(52)
    struct.pack_into("<I",table,8*4,world.base+pm.TRUE_GETTER_RVA)
    world.mem.put(world.base+rva,table)
    world.mem.put(0x31000,struct.pack("<4I",world.item,0,0x75000,0))
    struct.pack_into("<III",world.mem.regions[world.tile],44,0x31000,0x31010,0x31010)
    cell=center_cell(world.reader.snapshot())
    assert cell["occupants"]==1 and cell["occupant_types"]=={kind:1}
    assert world.reader.monster_slots == ({(100,100,7,1): cid or ("visible", 0x75000)} if kind=="monster" else {})
    assert cell["alert_players"]==([{ "id":cid}] if kind=="player" and cid else [])
    assert "monster_slots" not in cell and str(0x75000) not in json.dumps(cell)
    assert cell["kind"]=="clear"  # Presence does not guess collision or hostility.


@pytest.mark.parametrize("pos,item",[((100,100,7),4560),((100,100,6),4560),((100,100,7),6264)])
def test_gate_level_note_requires_exact_position_and_item(world,pos,item):
    world.reader.location_notes={pos:{"kind":"level_gate","item_id":item,"required_level":320,"requirement_source":"user_report"}}
    cell=center_cell(world.reader.snapshot())
    notes=[f for f in cell["navigation"] if "required_level" in f]
    if pos==(100,100,7) and item==4560:
        assert len(notes)==1 and notes[0]["required_level"]==320 and notes[0]["source"]=="confirmed_location"
    else:
        assert notes==[]


def test_location_notes_validate_and_reload(tmp_path):
    path=tmp_path/"catalogue.json"
    entry={"kind":"level_gate","item_id":6264,"required_level":320}
    doc={"version":1,"asset_sha256":ASSET_SHA256,"types":{},"locations":{"986,609,7":entry}}
    path.write_text(json.dumps(doc))
    assert load_location_notes(path)[(986,609,7)]=={**entry,"requirement_source":"user_report"}
    entry["required_level"]=True
    path.write_text(json.dumps(doc))
    assert load_location_notes(path)=={}


@pytest.mark.parametrize("flag,kind", [(12,"blocked"),(15,"avoid")])
def test_loaded_properties_determine_obstacles(world, flag, kind):
    struct.pack_into("<I", world.mem.regions[world.attrs], flag*4, 0x70000)
    assert center_cell(world.reader.snapshot())["kind"] == kind


def test_server_blocking_takes_precedence_over_clear_item_flags(world):
    world.mem.regions[world.tile][82] = 1
    cell = center_cell(world.reader.snapshot())
    assert cell["kind"] == "blocked" and cell["server_blocking"] == 1


def test_ground_id_is_not_misclassified_as_stairs():
    assert pm.classify_tile([{"id":4561,"flags":["ground"]}],0) == ("clear",None)
    assert pm.classify_tile([{"id":8467,"flags":["container","force_use"]}],0) == ("no_ground",None)


@pytest.mark.parametrize("help_code,kind", [(1100,"ladder"),(1101,"descent"),
    (1102,"rope_spot"),(1104,"door"),(1105,"door"),(1106,"stairs"),(1110,"covered_hole")])
def test_help_groups_recognize_variants_without_item_whitelist(help_code, kind):
    items = [{"id":19000,"flags":[],"stack":2,"lens_help":help_code}]
    features = navigation_features(items)
    assert len(features) == 1
    assert features[0]["kind"] == kind and features[0]["source"] == "client_hint"
    assert features[0]["item_id"] == 19000 and features[0]["stack"] == 2


@pytest.mark.parametrize("item_id,kind", [(1956,"stairs"),(4560,"stairs"),(14606,"hole"),
                                          (11652,"teleport"),(11941,"teleport")])
def test_transition_annotation_preserves_blocking_and_ground_identity(item_id, kind):
    items = [{"id":4515,"stack":0,"flags":["ground"]},
             {"id":item_id,"stack":1,"flags":["not_pathable"]}]
    assert pm.classify_tile(items,1)[0] == "blocked"
    features = navigation_features(items)
    assert features == [{"kind":kind,"item_id":item_id,"stack":1,"source":"confirmed_id"}]


def test_door_hints_do_not_invent_level_or_closing_policy():
    closed = navigation_features([{"id":18000,"flags":["not_walkable"],"lens_help":1105}])[0]
    opened = navigation_features([{"id":18001,"flags":["on_top"],"lens_help":1104}])[0]
    assert closed["door_state_hint"] == "closed" and closed["lock_hint"] is True
    assert opened["door_state_hint"] == "open"
    assert closed["kind"] == "door"  # A lock does not imply a level gate.
    assert closed["closing_policy"] == opened["closing_policy"] == "unknown"
    assert "required_level" not in closed and "destination" not in closed


def test_unrecognized_help_code_or_blocking_does_not_invent_transition():
    assert navigation_features([{"id":1957,"flags":["not_moveable"]}]) == []
    assert navigation_features([{"id":9999,"flags":["not_pathable"],"lens_help":8022}]) == []


def test_help_attribute_reads_only_validated_u16_payload(world):
    ptr = 0x72000
    struct.pack_into("<I",world.mem.regions[world.attrs],29*4,ptr)
    world.mem.put(ptr,struct.pack("<IH",world.base+pm.U16_ATTRIBUTE_VTABLE_RVA,1100))
    # A confirmed DBV type takes precedence over a conflicting generic help code.
    cell = center_cell(world.reader.snapshot())
    assert cell["items"][0]["lens_help"] == 1100
    assert cell["navigation"][0]["kind"] == "stairs"
    assert cell["navigation"][0]["source"] == "confirmed_id"
    struct.pack_into("<I",world.mem.regions[ptr],0,world.base+1)
    assert "lens_help" not in center_cell(world.reader.snapshot())["items"][0]


def test_unreadable_optional_help_keeps_collision_data(world):
    struct.pack_into("<I",world.mem.regions[world.attrs],29*4,0x72000)
    cell = center_cell(world.reader.snapshot())
    assert cell["kind"] == "clear" and "lens_help" not in cell["items"][0]


def test_possible_passage_is_uncertain_and_not_a_guessed_hole():
    item = {"id":19000,"flags":["not_moveable","not_pathable"],"minimap_color":210}
    feature = navigation_features([item])[0]
    assert feature["kind"] == "passage" and feature["source"] == "client_hint"
    assert "direction_hint" not in feature and "activation_hint" not in feature
    assert navigation_features([{**item,"flags":[*item["flags"],"not_walkable"]}]) == []
    assert navigation_features([{**item,"minimap_color":129}]) == []


def test_navigation_catalogue_reloads_data_and_discards_invalid_edits(tmp_path):
    path = tmp_path / "types.json"
    data = {"version":1,"asset_sha256":ASSET_SHA256,"types":{"19000":{"kind":"hole"}}}
    path.write_text(json.dumps(data))
    assert load_confirmed_types(path) == {19000:{"kind":"hole"}}
    data["types"]["19001"] = {"kind":"teleport"}
    path.write_text(json.dumps(data))
    assert load_confirmed_types(path)[19001]["kind"] == "teleport"
    data["types"]["19001"]["kind"] = "execute"
    path.write_text(json.dumps(data))
    assert load_confirmed_types(path) == {}
    data["types"] = {"19000":{"kind":"hole"}}
    data["asset_sha256"] = "wrong asset"
    path.write_text(json.dumps(data))
    assert load_confirmed_types(path) == {}
    path.write_text("x"*65537)
    assert load_confirmed_types(path) == {}


def test_changed_asset_does_not_reuse_confirmed_ids(world):
    world.reader = pm.MapReader(world.mem.read,world.base,allow_confirmed=False)
    cell = center_cell(world.reader.snapshot())
    assert cell["navigation"] == [] and cell["stairs"] is None


def test_corrupt_definition_is_unknown_not_walkable(world):
    struct.pack_into("<H",world.mem.regions[world.definition],26,999)
    assert center_cell(world.reader.snapshot())["kind"] == "unknown"


def test_recycled_tile_is_unknown(world):
    struct.pack_into("<I",world.mem.regions[world.tile],56,101)
    assert center_cell(world.reader.snapshot())["kind"] == "unknown"


def test_tree_cycle_fails_boundedly(world):
    struct.pack_into("<I",world.mem.regions[world.node],0,world.node)
    with pytest.raises(pm.MapReadError):
        world.reader.snapshot()


def test_concurrent_floor_change_does_not_publish_old_grid(world):
    count = 0
    def read(addr, size):
        nonlocal count
        if addr == world.base + pm.MAP_CENTER_RVA:
            count += 1
            if count == 2:
                return struct.pack("<III",100,100,8)
        return world.mem.read(addr,size)
    world.reader.read = read
    with pytest.raises(pm.MapReadError, match="Pozycja"):
        world.reader.snapshot()


def test_asset_inspection_does_not_guess_properties(tmp_path):
    asset = tmp_path / "file.dat"
    asset.write_bytes(bytes(range(256)))
    result = pm.inspect_dat(asset)
    assert result["status"] == "opaque" and result["known_asset"] is False
    assert "items" not in result and result["size"] == 256


def test_disconnected_monitor_does_not_open_process_or_change_actions():
    state = SimpleNamespace(connected=False, settings={"actions":{"auto_potions":{"enabled":True}}})
    monitor = pm.PassiveMapMonitor(state)
    monitor.start()
    monitor.stop()
    assert monitor.snapshot()["cells"] == []
    assert state.settings["actions"]["auto_potions"]["enabled"] is True


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 process enumeration")
def test_process_enumeration_copies_pid_and_requests_read_only(monkeypatch):
    import passive_map_win32 as win
    entry = SimpleNamespace(szExeFile=b"dbvStart.exe", th32ProcessID=123)
    def processes():
        yield entry
        entry.szExeFile, entry.th32ProcessID = b"other.exe", 999
        yield entry
    kernel = Mock()
    kernel.OpenProcess.return_value = 0
    monkeypatch.setattr(win.ctypes,"WinDLL",lambda *a,**k:kernel)
    monkeypatch.setattr(win.pymem.process,"list_processes",processes)
    with pytest.raises(pm.MapReadError,match="uprawnień"):
        win.ReadOnlyClient()
    kernel.OpenProcess.assert_called_once_with(0x410,False,123)
