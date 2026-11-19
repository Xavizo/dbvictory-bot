"""Exercise atlas, route planning and item use without a live client."""
import asyncio

import pytest

from map_atlas import MapAtlas
from map_navigation import load_confirmed_types, navigation_features
from navigation_tools import WEB_ITEM_ID, cuttable_web, supported_transition, web_edge
from test_adaptive_cavebot import cell, setup
from test_automation_safety import state
from test_dig_and_paged_atlas import fake_tools


CURRENT_DAT = "cc6d91160218e7565ab1247d19ae483485dedaf6a18e24a05978ef31badb3669"
ORIGIN, WEB, END = [10,10,7], [11,10,7], [12,10,7]


def web_tile(iid=WEB_ITEM_ID):
    tile = cell(*WEB, kind="blocked")
    tile["items"][0]["flags"] = ["ground"]
    tile["items"].append(dict(id=iid, stack=1, flags=["not_walkable","not_moveable"]))
    tile["navigation"] = navigation_features(tile["items"], load_confirmed_types(asset_sha256=CURRENT_DAT))
    return tile


def cut(tile):
    tile["items"].pop()
    tile.update(kind="clear", navigation=[])


def test_profile_and_historical_web(tmp_path):
    tile = web_tile()
    assert cuttable_web(tile)["stack"] == 1
    for iid in (182,184):
        assert not web_tile(iid)["navigation"]
    assert WEB_ITEM_ID not in load_confirmed_types(asset_sha256="unknown")
    tile["navigation"] = []
    atlas = MapAtlas(tmp_path,"f"*64)
    atlas.observe(dict(status="ready",cells=[cell(*ORIGIN), tile, cell(*END)]))
    assert not atlas.plan([ORIGIN,END],[])["complete"]
    atlas = MapAtlas(tmp_path,atlas.scope, web_definition=load_confirmed_types(asset_sha256=CURRENT_DAT)[WEB_ITEM_ID])
    assert cuttable_web(next(c for c in atlas.view(*WEB)["cells"] if c["x"]==WEB[0]))
    atlas.tiles.clear()  # Exercise the disk-backed page cache too.
    plan = atlas.plan([ORIGIN,WEB,END],[],loop=True)
    assert plan["loop_closed"] and len(plan["transitions"]) == 2
    assert all(supported_transition(e) and e["entry"] == e["exit"] == WEB for e in plan["transitions"])


def test_bypass_and_permanent_wall(tmp_path):
    atlas = MapAtlas(tmp_path,'e'*64)
    tile = web_tile()
    tile["items"].append(dict(id=4459,stack=2,flags=["not_walkable","blocks_projectiles"]))
    atlas.observe(dict(status="ready",cells=[cell(*ORIGIN),tile,cell(*END)]))
    assert not atlas.plan([ORIGIN,END],[])["complete"]
    atlas.observe(dict(status="ready",cells=[web_tile(),cell(11,11)]))
    plan = atlas.plan([ORIGIN,END],[])
    assert plan["path"] == [ORIGIN,[11,11,7],END] and not plan["transitions"]


@pytest.mark.parametrize("tool,mode",[(8899,"intact"),(8900,"regrown"),(None,"open"),(3003,"missing"),(8900,"blocked_after"),(8899,"no_ack")])
def test_route_web_sequence(state,tmp_path,monkeypatch,tool,mode):
    tile = web_tile()
    cells = [cell(*ORIGIN),tile]
    intact = web_tile()
    if mode == "regrown":
        cut(tile)
    controller,_,_ = setup(state,tmp_path,cells,[ORIGIN,WEB])
    if mode == "open":
        cut(tile)
    if mode == "regrown":
        tile.update(intact)
    fake_tools(monkeypatch, () if tool is None else (tool,))
    sent = []
    async def dispatch(payload, guard=None):
        assert guard()
        sent.append(payload[0])
        controller.last_command = 0
        if payload[0] == 0x83:
            assert tool in (8899, 8900) and int.from_bytes(payload[6:8],"little") == tool
            if mode != "no_ack":
                cut(tile)
            if mode == "blocked_after":
                tile["kind"] = "blocked"
                tile["items"].append(dict(id=4459,stack=1,flags=["not_walkable"]))
        else:
            assert payload[0] == 0x66 and tile["kind"] == "clear"
            state.game_state.position = tuple(WEB)
    state.game_proxy.inject_to_server = dispatch
    asyncio.run(controller.run())
    if mode in {"intact","open","regrown"}:
        assert controller.status == "complete"
        assert sent == ([0x66] if mode == "open" else [0x83,0x66])
    else:
        assert controller.status == "paused" and state.game_state.position == tuple(ORIGIN)
        assert sent == ([] if mode == "missing" else [0x83])


def test_occupied_web_defers_before_using_tool(state,tmp_path):
    from adaptive_cavebot import MovementDeferred
    tile = web_tile()
    controller,_,_ = setup(state,tmp_path,[cell(*ORIGIN),tile],[ORIGIN,WEB])
    tile["occupants"] = 1
    with pytest.raises(MovementDeferred, match="zajęte"):
        asyncio.run(controller.clear_web(web_edge(ORIGIN,WEB), {"cells":[tile]}))


def test_cut_recipe_is_same_floor_and_aio_only():
    edge = web_edge(ORIGIN, WEB)
    for change in ({"exit":[11,10,8]}, {"origin":END, "entry":ORIGIN}, {"item_id":184}, {"tool_id":3457}):
        assert not supported_transition({**edge, **change})
