"""Panel commands exercised against map samples and a simulated transport only."""
import asyncio
import time
from types import SimpleNamespace

import pytest
import nearby_items
from command_delivery import CommandReceipt
from nearby_items import NearbyItemTest
from protocol import build_move_item_packet


def tile(x, y, *items, z=7, kind="clear"):
    return dict(x=x, y=y, z=z, kind=kind, items=[dict(id=i, stack=n, flags=f) for n,(i,f) in enumerate(items)])


@pytest.fixture
def panel():
    loot = (808, ["pickupable"])
    sample = dict(status="ready", captured_at=time.time(), center=[10,10,7], cells=[
        tile(10,10, (101,["ground"]), loot),
        tile(11,10, (100,["ground"]), (805,["pickupable"]), (990,["container"])),
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
        (808,1,False,True),(805,1,True,True),(990,2,False,False),(808,0,False,True)]
    assert p.snapshot() == data


@pytest.mark.parametrize("dest", ["backpack", "feet"])
def test_click_moves_one_from_the_selected_layer_only_once(panel, monkeypatch, dest):
    p, _ = panel
    row = p.snapshot()["rows"][-1]
    monkeypatch.setattr(nearby_items, "backpack_id", lambda player: 2880)
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
    monkeypatch.setattr(nearby_items, "backpack_id", lambda player: 0)
    rows = p.snapshot()["rows"]
    row = rows[{"covered":1, "underfoot":0, "push_only":2}.get(change,-1)]
    dest = "backpack" if change in {"empty_bag","push_only"} else "feet"
    if change == "stale": sample["captured_at"] -= 10
    if change == "walked": p.lease.game.position = (10,11,7)
    if change == "disconnected": p.lease.proxy.session_generation += 1
    if change == "replaced": sample["cells"][2]["items"][0]["id"] += 1
    if change == "paused": p.state.automation_paused = True
    token = "0"*32 if change == "foreign_token" else row["token"]
    with pytest.raises(ValueError): asyncio.run(p.move(token, dest))


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
