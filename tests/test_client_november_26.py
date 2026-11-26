"""Synthetic fixtures for the af433830 client; no Windows/game access."""
import struct
from types import SimpleNamespace

import pytest

from client_profiles import select_profile
from client_player_state import ClientPlayerReader
from client_equipment import EquipmentReader
from passive_map import MapReadError

DIGEST = "af43383076c0abc7192b407210efc54da6b6cbaf4ff8a2aa18f22a7f7d7486e4"


def new_player():
    profile = select_profile(DIGEST)
    base, ptr, pid = 0x400000, 0x20000, 0x10000061
    obj = bytearray(0x620)
    struct.pack_into('<I', obj, 0, base + 0x8BE900)
    struct.pack_into('<I', obj, 0x38, pid)
    struct.pack_into('<I', obj, 0x58, 3)
    struct.pack_into('<H', obj, 0xD4, 1984)
    struct.pack_into('<I', obj, 0x548, 54)
    struct.pack_into('<10d', obj, 0x558, 36, 100, 522495, 522495, 101407684, 184, 55, 3, 100, 66)
    regions = {
        ptr: obj,
        base + 0xB7A00C: struct.pack('<III', 100, 100, 7),
        base + 0xBAEC0: bytes.fromhex('66 8b 81 d4 00 00 00 c3'),
        base + 0xBB310: bytes.fromhex('8b 81 48 05 00 00 c3'),
    }
    def read(address, size):
        for start, data in regions.items():
            if start <= address and address + size <= start + len(data):
                return bytes(data[address-start:address-start+size])
        raise MapReadError('unmapped synthetic memory')
    source = SimpleNamespace(base=base, read=read, profile=profile)
    reader = ClientPlayerReader(source, pid)
    reader.ptr = ptr
    return reader, obj, regions


def test_new_client_reads_shifted_stats_conditions_and_speed():
    reader, _, _ = new_player()
    sample = reader.snapshot()
    assert (sample['hp'], sample['max_hp'], sample['mana'], sample['max_mana']) == (36,100,3,100)
    assert (sample['level'], sample['magic_level'], sample['conditions'], sample['movement_speed']) == (184,66,54,1984)
    assert sample['direction'] == 3 and sample['position'] == (100,100,7)


def test_old_local_player_vtable_is_rejected_on_new_client():
    reader, obj, _ = new_player()
    struct.pack_into('<I', obj, 0, reader.source.base + 0x8BA9B0)
    with pytest.raises(MapReadError):
        reader.snapshot()


def test_new_client_torn_stats_are_rejected():
    reader, _, _ = new_player()
    original = reader.source.read
    count = 0
    def torn(address, size):
        nonlocal count
        value = original(address,size)
        if address == reader.ptr + 0x558 and size == 80:
            count += 1
            if count == 2:
                return struct.pack('<d',35) + value[8:]
        return value
    reader.source.read = torn
    with pytest.raises(MapReadError):
        reader.snapshot()


def test_equipment_uses_new_inventory_and_game_addresses():
    reader, obj, regions = new_player()
    base = reader.source.base
    item, head = 0x30000, 0x40000
    struct.pack_into('<I',obj,0x484+9*8,item)
    raw = bytearray(0x3b)
    struct.pack_into('<I',raw,0,base+0x8BE72C)
    struct.pack_into('<H',raw,0x35,3051)
    regions[item] = raw
    regions[base+0xB79B50+0x2c] = struct.pack('<II',head,0)
    sentinel = bytearray(28)
    struct.pack_into('<I',sentinel,4,head)
    sentinel[13] = 1
    regions[head] = sentinel
    sample = EquipmentReader(reader).snapshot()
    assert sample['slots'][9] == 3051
    assert sample['sources'] == []


def test_new_profile_has_its_own_dll_and_pipe():
    from client_profiles import UPDATED
    profile = select_profile(DIGEST)
    assert profile.observer_dll == 'dbvobserver_marker_20261126.dll'
    assert profile.observer_pipe == rb'\\.\pipe\dbvbot_marker_20261126'
    assert profile.observer_dll != UPDATED.observer_dll


def test_new_bridge_uses_new_pipe_and_injects_exact_selected_artifact(monkeypatch, tmp_path):
    from unittest.mock import Mock
    from actions import dll_bridge as action
    from dll_bridge import DllBridge
    profile = select_profile(DIGEST)
    bridge = DllBridge(read_only=True, profile=profile)
    assert bridge.pipe_name == rb'\\.\pipe\dbvbot_marker_20261126'
    folder = tmp_path / 'dll'
    folder.mkdir()
    (folder / profile.observer_dll).write_bytes(b'new synthetic observer')
    (folder / 'dbvobserver_marker_20261117.dll').write_bytes(b'old synthetic observer')
    monkeypatch.setattr(action, 'PROJECT_ROOT', str(tmp_path))
    monkeypatch.setattr(action, '_dbg', Mock())
    injection = Mock()
    monkeypatch.setattr(action.dll_inject, 'inject', injection)
    action._inject_fresh_dll(observer=True, profile=profile)
    from pathlib import Path
    assert Path(injection.call_args.kwargs['dll_path']).read_bytes() == b'new synthetic observer'


def test_offline_build_check_rejects_unknown_executable_without_accepting_a_profile(tmp_path):
    from check_client_build import verify
    path = tmp_path / 'unknown.exe'
    path.write_bytes(b'MZ' + bytes(128))
    with pytest.raises(ValueError, match='weryfikacja'):
        verify(path)


def test_startup_requires_selected_dll_before_reset_or_process_writes(monkeypatch, tmp_path):
    import asyncio
    from unittest.mock import AsyncMock, Mock
    import mcp_server as server
    import passive_map_win32
    profile = select_profile(DIGEST)
    close = Mock()
    monkeypatch.setattr(passive_map_win32,'ReadOnlyClient',lambda:SimpleNamespace(profile=profile,close=close))
    monkeypatch.setattr(server,'os',SimpleNamespace(name='nt'))
    monkeypatch.setattr(server,'__file__',str(tmp_path/'mcp_server.py'))
    reset = AsyncMock(side_effect=AssertionError('session reset before compatibility validation'))
    monkeypatch.setattr(server,'_reset_bot',reset)
    folder = tmp_path/'dll'
    folder.mkdir()
    (folder/'dbvobserver_20261116.dll').write_bytes(b'wrong profile fixture')
    result = asyncio.run(server.start_bot())
    assert result == 'ERROR: Build dll/dbvobserver_marker_20261126.dll before starting this client version.'
    close.assert_called_once()
    reset.assert_not_called()


def test_python_and_native_profiles_agree():
    import re
    from pathlib import Path
    profile = select_profile(DIGEST)
    header = Path(__file__).resolve().parents[1]/'dll'/'observer_layout.h'
    new = header.read_text().split('#else',1)[0]
    values = {key:int(value,16) for key,value in re.findall(r'(\w+)\s*=\s*(0x[0-9A-F]+)',new)}
    assert values['MAP_BLOCKS'] == profile.map_blocks
    assert values['MAP_CENTER'] == profile.map_center
    assert values['VT_TILE'] == profile.tile_vtable
    assert values['GAME'] == profile.player_layout.game_singleton
    for constant,kind in [('VT_PLAYER','player'),('VT_SELF','self'),('VT_MONSTER','monster'),('VT_NPC','npc')]:
        assert (values[constant],kind) in profile.creature_vtables
