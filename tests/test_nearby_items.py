"""Panel commands exercised against map samples and a simulated transport only."""
import asyncio
import time
from types import SimpleNamespace

import pytest
import nearby_items
from browse_fields import BrowseFields, field_slot
from command_delivery import CommandReceipt
from nearby_items import NearbyItemTest
from protocol import PacketReader, build_move_item_packet, manual_move_count_bytes


def tile(x, y, *items, z=7, kind="clear"):
    return dict(x=x, y=y, z=z, kind=kind, items=[dict(id=i, stack=n, flags=f) for n,(i,f) in enumerate(items)])


@pytest.fixture
def panel():
    loot = (808, ["pickupable"])
    sample = dict(status="ready", captured_at=time.time(), center=[10,10,7], cells=[
        tile(10,10, (101,["ground"]), loot),
        tile(11,10, (100,["ground"]), (990,["container"]), (805,["pickupable"])),
        tile(9,9, loot), tile(12,10,loot), tile(9,9,loot,z=8),
        tile(10,11,loot,kind="unknown"), tile(9,11,(999,["not_moveable"]))])
    state = SimpleNamespace(connected=True, automation_paused=False,
        game_state=SimpleNamespace(player_id=7, position=(10,10,7), hp=100),
        game_proxy=SimpleNamespace(session_generation=5, logged_in=True),
        passive_map_monitor=SimpleNamespace(snapshot=lambda:sample))
    return NearbyItemTest(state), sample


def test_lists_only_live_movable_items_and_preserves_selection(panel):
    p, _ = panel
    data = p.snapshot()
    assert data["center"] == [10,10,7]
    assert [(x["item_id"],x["stack"],x["covered"],x["pickup"]) for x in data["rows"]] == [
        (808,1,False,True),(990,1,False,False),(805,2,True,True),(808,0,False,True)]
    assert p.snapshot() == data


@pytest.mark.parametrize("dest", ["backpack", "feet"])
def test_click_moves_one_from_the_selected_layer_only_once(panel, monkeypatch, dest):
    p, _ = panel
    row = p.snapshot()["rows"][-1]
    monkeypatch.setattr(nearby_items, "read_bags", lambda player: (2880, {}))
    sent = []
    async def dispatch(data, guard):
        assert guard()
        sent.append(data)
        receipt = CommandReceipt(1,5,"server",0)
        receipt.finish("dispatched")
        return receipt
    p.lease.proxy.inject_to_server = dispatch
    assert "Wysłano" in asyncio.run(p.move(row["token"], dest))
    target = (65535,3,0) if dest == "backpack" else (10,10,7)
    assert sent == [build_move_item_packet((9,9,7),808,0,target,1)]
    with pytest.raises(ValueError): asyncio.run(p.move(row["token"], dest))


@pytest.mark.parametrize("change", ["covered","underfoot","push_only","empty_bag","stale","walked","disconnected","replaced","paused","foreign_token"])
def test_unavailable_operations_never_send(panel, monkeypatch, change):
    p,sample = panel
    monkeypatch.setattr(nearby_items, "read_bags", lambda player: (0, {}))
    rows = p.snapshot()["rows"]
    row = rows[{"covered":2, "underfoot":0, "push_only":1}.get(change,-1)]
    dest = "backpack" if change in {"empty_bag","push_only"} else "feet"
    if change == "stale": sample["captured_at"] -= 10
    if change == "walked": p.lease.game.position = (10,11,7)
    if change == "disconnected": p.lease.proxy.session_generation += 1
    if change == "replaced": sample["cells"][2]["items"][0]["id"] += 1
    if change == "paused": p.state.automation_paused = True
    token = "0"*32 if change == "foreign_token" else row["token"]
    with pytest.raises(ValueError): asyncio.run(p.move(token, dest))


def test_pile_exposes_each_loose_item_in_client_order(panel):
    p, sample = panel
    cell = tile(11,10,(101,["ground"]),(203,["on_bottom", "not_moveable"]),
                (8192,["pickupable","stackable"]), (10257,["pickupable"]), (8192,["pickupable","stackable"]))
    sample["cells"] = [cell]
    for expected in (8192, 10257, 8192):
        rows = p.snapshot()["rows"]
        assert [r["item_id"] for r in rows if not r["covered"]] == [expected]
        assert rows[0]["stackable"] == (expected == 8192)
        cell["items"].pop(2)
        for i, obj in enumerate(cell["items"]):
            obj["stack"] = i


def test_count_width_matches_native_drag_and_leaves_ambiguous_data_alone():
    ground_drag = bytes.fromhex("de 00 6e 00 08 00 20 01 dd 00 6e 00 08 01")
    for width, suffix in ((1, b""), (2, b"\x00")):
        reader = PacketReader(ground_drag + suffix)
        assert manual_move_count_bytes(reader) == width and reader.position == 0
        assert build_move_item_packet((222,110,8), 8192, 1, (221,110,8), 1, count_bytes=width) == b"\x78" + ground_drag + suffix
    assert manual_move_count_bytes(PacketReader(ground_drag + b"\x65")) is None


def test_direct_open_container_uses_its_next_slot_and_rejects_reused_window(panel, monkeypatch):
    p, _ = panel
    windows = {2:(300,4), 1:(200,0)}
    monkeypatch.setattr(nearby_items, "read_bags", lambda player: (2880, dict(windows)))
    choices = asyncio.run(p.containers())
    assert choices == asyncio.run(p.containers())
    assert [(c["number"], c["size"], set(c)) for c in choices] == [
        (1,0,{"number","size","token"}),(2,4,{"number","size","token"})]
    row = p.snapshot()["rows"][-1]
    p.lease.game.manual_move_count_bytes = 2
    async def dispatch(data, guard):
        assert guard() and data[-7:] == bytes.fromhex("ff ff 42 00 04 01 00")
        receipt = CommandReceipt(1,5,"server",0)
        receipt.finish("dispatched")
        return receipt
    p.lease.proxy.inject_to_server = dispatch
    monkeypatch.setattr(nearby_items, "RESULT_WAIT", 0)
    assert "Brak potwierdzenia" in asyncio.run(p.move(row["token"], "backpack", choices[1]["token"]))
    windows[2] = (301,4)
    with pytest.raises(ValueError, match="kontener"):
        asyncio.run(p.move(p.snapshot()["rows"][-1]["token"], "backpack", choices[1]["token"]))


@pytest.mark.parametrize("dest", ["backpack", "feet"])
def test_browse_picks_covered_erath_from_its_visible_slot(panel, monkeypatch, dest):
    from game_state import GameState, scan_packet
    from packet_observer import apply_scan, detach_state

    p, sample = panel
    pos = (11, 10, 7)
    sample["cells"] = [tile(*pos[:2], (101, ["ground"]), (102, ["on_bottom", "not_moveable"]),
                            (890, ["pickupable"]), (450, ["pickupable"]), (8192, ["pickupable", "stackable"])),
                       tile(10, 10, (101, ["ground"]))]
    # The live monitor publishes a new sample for every frame.
    p.state.passive_map_monitor.snapshot = lambda: dict(sample)
    sample["captured_at"] = time.time() - .1
    live = GameState()
    live.browse_fields.requested(pos, 10)
    parsed = detach_state(live, 0)
    parsed.received_perf = 10.1
    title = b"Browse Fields"
    header = bytes([0x6e, 6, 100, 0, len(title), 0]) + title + bytes([30, 0])
    scan_packet(header, parsed.state, received_at=10.1)
    apply_scan(live, parsed)
    p.lease.game.browse_fields = live.browse_fields
    row = next(r for r in p.snapshot()["rows"] if r["item_id"] == 8192)
    assert row["stack"] == 4 and row["covered"] and row["browsed"]
    windows = {1: (555, 3), 6: (123, 3)}
    inventory = [dict(container=6, index=i, item_id=value) for i, value in enumerate([8192, 450, 890])]
    inventory.append(dict(container=1, index=1, item_id=8192))
    def bags(player, *, field_items=None):
        return (2880, windows) if field_items is None else (2880, dict(windows), list(inventory))
    monkeypatch.setattr(nearby_items, "read_bags", bags)
    choices = asyncio.run(p.containers())
    assert [c["number"] for c in choices] == [1]
    selected_bag = choices[0]["token"] if dest == "backpack" else None
    expected = (65535, 65, 3) if dest == "backpack" else (10, 10, 7)
    sent = []
    async def dispatch(data, guard):
        assert guard()
        sent.append(data)
        remaining = tile(*pos[:2], (101, ["ground"]), (102, ["on_bottom", "not_moveable"]),
                         (890, ["pickupable"]), (450, ["pickupable"]))
        feet_items = [(8192, ["pickupable"])] if dest == "feet" else []
        sample["cells"] = [remaining, tile(10, 10, (101, ["ground"]), *feet_items)]
        if dest == "backpack":
            inventory.append(dict(container=1, index=3, item_id=8192))
            windows[1] = (555, 4)
        sample["captured_at"] = time.time()
        receipt = CommandReceipt(1, 5, "server", 0)
        receipt.finish("dispatched")
        return receipt
    p.lease.proxy.inject_to_server = dispatch
    assert "potwierdza" in asyncio.run(p.move(row["token"], dest, selected_bag)).lower()
    assert sent == [build_move_item_packet((65535, 70, 0), 8192, 0, expected, 1)]
    assert [i["id"] for i in sample["cells"][0]["items"]] == [101, 102, 890, 450]


def test_field_slots_and_duplicate_ids():
    layers = ((2, 8192), (3, 8192), (4, 777))
    assert field_slot(layers, (777, 8192, 8192), (3, 8192)) == 1
    assert field_slot(layers, (777, 8192, 8192), (2, 8192)) == 2
    assert field_slot(layers, (777, 8192), (2, 8192)) is None
    assert field_slot(((2, 8192), (3, 8192)), (8192, 8192), (2, 8192)) is None


def test_requires_unambiguous_native_open_and_invalidates_reused_window():
    fields = BrowseFields()
    a, b = (10, 10, 7), (11, 10, 7)
    fields.requested(a, 10)
    fields.window_changed(6, True, 9.9)
    assert fields.opened is None
    fields.requested(b, 10.2)
    fields.window_changed(6, True, 10.3)
    assert fields.opened is None
    fields.requested(a, 13)
    fields.window_changed(6, True, 13.1)
    assert fields.opened.position == a
    fields.navigating(1)
    assert fields.opened.position == a and fields.field_windows == {6}
    fields.window_changed(2, True, 13.15)
    assert fields.field_windows == {2, 6}
    fields.window_changed(6, False, 13.2)
    assert fields.field_windows == {2}
    assert fields.opened is None
    fields.requested(b, 14)
    fields.window_changed(6, True, 18)
    assert fields.opened is None


@pytest.mark.parametrize("case", ["different_tile", "wrong_title", "item_missing", "closed_during_read", "slot_changed"])
def test_browse_does_not_move_from_wrong_or_changed_view(panel, monkeypatch, case):
    p, sample = panel
    game = p.lease.game
    game.browse_fields = BrowseFields()
    pos = (10, 10, 7) if case == "different_tile" else (11, 10, 7)
    game.browse_fields.requested(pos, 5)
    game.browse_fields.window_changed(3, case != "wrong_title", 5.1)
    rows = p.snapshot()["rows"]
    covered = next(r for r in rows if r["covered"])
    calls = 0
    def read(player, *, field_items):
        nonlocal calls
        calls += 1
        if case == "closed_during_read":
            game.browse_fields.clear()
        ids = [990] if case == "item_missing" else [990, 805]
        if case == "slot_changed" and calls > 1:
            ids.reverse()
        return 101, {3: (777, len(ids))}, [dict(index=i, item_id=v, container=3) for i, v in enumerate(ids)]
    monkeypatch.setattr(nearby_items, "read_bags", read)
    with pytest.raises(ValueError):
        asyncio.run(p.move(covered["token"], "feet"))


def test_game_refusal_does_not_count_an_earlier_message(panel):
    p, sample = panel
    old = {"type": 26, "text": "Sorry, not possible."}
    messages = p.lease.game.messages = [old]
    assert nearby_items.recent_refusal(messages, old) is None
    messages.append(dict(old))
    item = list(nearby_items.nearby_items(sample))[-1]
    with pytest.raises(ValueError, match="Sorry, not possible"):
        asyncio.run(p.outcome(item, "feet", sample, old))


def test_new_map_can_confirm_the_destination_of_a_ground_move(panel):
    p, sample = panel
    item = list(nearby_items.nearby_items(sample))[-1]
    before = {**sample, "cells": list(sample["cells"]), "captured_at": sample["captured_at"] - .2}
    sample["cells"][0] = tile(10,10,(101,["ground"]),(808,["pickupable"]),(808,["pickupable"]))
    sample["cells"][2] = tile(9,9,(101,["ground"]))
    assert "Mapa potwierdza" in asyncio.run(p.outcome(item, "feet", before, None))


def test_guard_sees_item_replaced_while_move_is_queued(panel):
    p,sample = panel
    row = p.snapshot()["rows"][-1]
    async def dispatch(data, guard):
        sample["cells"][2]["items"] = []
        assert not guard()
        receipt = CommandReceipt(1,5,"server",0)
        receipt.finish("rejected")
        return receipt
    p.lease.proxy.inject_to_server = dispatch
    with pytest.raises(ValueError, match="nie został wysłany"): asyncio.run(p.move(row["token"], "feet"))
