"""Exercise atlas, route planning and item use without a live client."""
import asyncio

import pytest

from dbv.brain.navigation.map_atlas import MapAtlas
from dbv.brain.navigation.map_navigation import load_confirmed_types, navigation_features
from dbv.brain.navigation.navigation_tools import cuttable_web, supported_transition, web_edge
from test_adaptive_cavebot import cell, setup
from test_automation_safety import state
from test_dig_and_paged_atlas import fake_tools


CURRENT_DAT = "cc6d91160218e7565ab1247d19ae483485dedaf6a18e24a05978ef31badb3669"
ORIGIN, WEB, END = [10,10,7], [11,10,7], [12,10,7]


def test_web_prefers_confirmed_aio_even_for_older_sketch():
    from dbv.brain.cavebot.adaptive_cavebot import tool_location
    from dbv.brain.navigation.navigation_tools import transition_tools

    edge = dict(web_edge(ORIGIN, WEB, 183), tool_id=8899, tool_ids=[8899, 8900])
    inventory = {"sources": [{"container": 0, "index": 1, "item_id": 8899}], "slots": {10: 8900}}
    candidates = transition_tools(edge)
    assert tool_location(inventory, candidates)[1] == 8900
    inventory["slots"].clear()
    assert tool_location(inventory, candidates)[1] == 8899


def web_tile(iid=183):
    tile = cell(*WEB, kind="blocked")
    tile["items"][0]["flags"] = ["ground"]
    tile["items"].append(dict(id=iid, stack=1, flags=["not_walkable","not_moveable"]))
    tile["navigation"] = navigation_features(tile["items"], load_confirmed_types(asset_sha256=CURRENT_DAT))
    return tile


def cut(tile):
    tile["items"].pop()
    tile.update(kind="clear", navigation=[])


@pytest.mark.parametrize("iid", [182,183])
def test_profile_and_historical_web(tmp_path,iid):
    tile = web_tile(iid)
    assert cuttable_web(tile)["stack"] == 1
    assert not web_tile(184)["navigation"]
    assert iid not in load_confirmed_types(asset_sha256="unknown")
    tile["navigation"] = []
    atlas = MapAtlas(tmp_path,"f"*64)
    atlas.observe(dict(status="ready",cells=[cell(*ORIGIN), tile, cell(*END)]))
    assert not atlas.plan([ORIGIN,END],[])["complete"]
    types = load_confirmed_types(asset_sha256=CURRENT_DAT)
    atlas = MapAtlas(tmp_path,atlas.scope,web_definitions={k:v for k,v in types.items() if v["kind"]=="web"})
    assert cuttable_web(next(c for c in atlas.view(*WEB)["cells"] if c["x"]==WEB[0]))["id"] == iid
    atlas.tiles.clear()  # Exercise the disk-backed page cache too.
    plan = atlas.plan([ORIGIN,WEB,END],[],loop=True)
    assert plan["loop_closed"] and len(plan["transitions"]) == 2
    assert all(supported_transition(e) and e["item_id"] == iid and e["entry"] == e["exit"] == WEB for e in plan["transitions"])


@pytest.mark.parametrize("iid", [182,183])
def test_bypass_and_permanent_wall(tmp_path,iid):
    atlas = MapAtlas(tmp_path,'e'*64)
    tile = web_tile(iid)
    tile["items"].append(dict(id=4459,stack=2,flags=["not_walkable","blocks_projectiles"]))
    atlas.observe(dict(status="ready",cells=[cell(*ORIGIN),tile,cell(*END)]))
    assert not atlas.plan([ORIGIN,END],[])["complete"]
    atlas.observe(dict(status="ready",cells=[web_tile(iid),cell(11,11)]))
    plan = atlas.plan([ORIGIN,END],[])
    assert plan["path"] == [ORIGIN,[11,11,7],END] and not plan["transitions"]


@pytest.mark.parametrize("tool,mode",[(8899,"intact"),(8900,"regrown"),(None,"open"),(3003,"missing"),(8900,"blocked_after"),(8899,"no_ack")])
@pytest.mark.parametrize("iid", [182,183])
def test_route_web_sequence(state,tmp_path,monkeypatch,tool,mode,iid):
    tile = web_tile(iid)
    cells = [cell(*ORIGIN),tile]
    intact = web_tile(iid)
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
            assert payload[14:] == iid.to_bytes(2,"little") + bytes([1])
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
    from dbv.brain.cavebot.adaptive_cavebot import MovementDeferred
    tile = web_tile()
    controller,_,_ = setup(state,tmp_path,[cell(*ORIGIN),tile],[ORIGIN,WEB])
    tile["occupants"] = 1
    with pytest.raises(MovementDeferred, match="zajęte"):
        asyncio.run(controller.clear_web(web_edge(ORIGIN,WEB,183), {"cells":[tile]}))


def test_late_web_clear_releases_hold_without_resending_tool(state,tmp_path):
    from types import SimpleNamespace
    tile=web_tile()
    controller,_,_=setup(state,tmp_path,[cell(*ORIGIN),tile],[ORIGIN,WEB])
    controller.domains=SimpleNamespace(movement_state="running")
    controller.status="running"
    controller.navigation_hold="Czekam na wolne pole pajęczyny."
    controller._held_web=(ORIGIN,WEB)
    async def change_tile():
        while controller.domains.movement_state!="held":
            await asyncio.sleep(.01)
        cut(tile)
        tile["occupants"]=1
        await asyncio.sleep(.12)
        assert controller.navigation_hold and controller.index==0
        tile["occupants"]=0
    retry=controller.retry_navigation
    def resumed():
        retry()
        assert controller.index==0 and controller._held_web is None
        controller.status="complete"
    controller.retry_navigation=resumed
    async def scenario():
        await asyncio.wait_for(asyncio.gather(controller._navigation_loop(),change_tile()),2)
    asyncio.run(scenario())
    assert state.game_proxy._inject_queue.empty()


def test_cut_recipe_is_same_floor_and_aio_only():
    edge = web_edge(ORIGIN, WEB,183)
    for change in ({"exit":[11,10,8]}, {"origin":END, "entry":ORIGIN}, {"item_id":184}, {"tool_id":3457}):
        assert not supported_transition({**edge, **change})


def test_unknown_or_conflicting_web_layer():
    tile = web_tile(182)
    tile["navigation"][0]["item_id"] = 183
    assert cuttable_web(tile) is None
    tile["items"].append(dict(id=183, stack=2, flags=["not_walkable"]))
    assert cuttable_web(tile) is None


@pytest.mark.parametrize('kind',['web','pile'])
def test_body_cleared_then_fresh_tool_target_and_step(state,tmp_path,monkeypatch,kind):
    from test_dig_and_paged_atlas import edge as dig_edge
    from dbv.client_bridge.protocol import ClientOpcode
    from dbv.brain.cavebot.adaptive_cavebot import MovementDeferred
    import struct

    edge = web_edge(ORIGIN, WEB) if kind=='web' else dig_edge(8900)
    entry = edge['entry']
    tile = web_tile() if kind=='web' else cell(*entry,item=edge['item_id'])
    origin = cell(*edge['origin'])
    drop = cell(11,9)
    drop['items'][0]['flags']=['ground']
    cells = [origin, tile, drop]
    if kind=='pile':
        cells.append(cell(*edge['exit']))
    controller,_,_=setup(state,tmp_path,cells,[edge['origin'],edge['exit']],[edge] if kind=='pile' else [])
    fake_tools(monkeypatch,(8900,))
    controller.status='running'
    body=dict(id=8506,stack=1,flags=['container','force_use'])
    if kind=='web':
        tile['items'][1]['stack']=2
        tile['navigation'][0]['stack']=2
    tile['items'].insert(1,body)
    sent=[]
    async def dispatch(payload, guard=None):
        assert guard()
        sent.append(payload[0]); controller.last_command=0
        if payload[0]==ClientOpcode.MOVE_THING:
            assert struct.unpack('<HHBHBHHBB',payload[1:])==(*entry,8506,1,11,9,7,1)
        elif payload[0]==ClientOpcode.USE_ITEM_EX:
            assert payload[14:]==edge['item_id'].to_bytes(2,'little')+bytes([1 if kind=='web' else 0])
            if kind=='web':cut(tile)
            else:tile['items'][0]['id']=edge['prepared_item_id']
        else:
            state.game_state.position=tuple(edge['exit'])
    state.game_proxy.inject_to_server=dispatch
    async def act():
        tile.update(occupants=1,occupant_types={'player':1})
        with pytest.raises(MovementDeferred,match='zajęte'):
            await controller.transition(edge,controller.map_now())
        assert sent==[]
        tile.update(occupants=0,occupant_types={})
        for _ in range(2):
            with pytest.raises(MovementDeferred):
                await controller.transition(edge,controller.map_now())
        assert sent==[ClientOpcode.MOVE_THING]
        tile['items'].remove(body)
        drop['items'].append(body)
        if kind=='web':
            tile['items'][1]['stack']=1
            tile['navigation'][0]['stack']=1
        fresh = dict(controller.map_now(), captured_at=controller._pending_body_move.sent_at + .1)
        await controller.transition(edge, fresh)
    asyncio.run(act())
    assert len(sent)==3 and sent[1]==ClientOpcode.USE_ITEM_EX and list(state.game_state.position)==edge['exit']


@pytest.mark.parametrize("reject_first,site_count", [(False, 2), (True, 2), (False, 1)])
def test_two_bodies_on_web_and_report(state, tmp_path, monkeypatch, reject_first, site_count):
    import struct
    from dbv.brain.cavebot.adaptive_cavebot import BODY_ACK_SECONDS, MovementDeferred
    from dbv.brain.cavebot.movement_history import MovementHistory
    from dbv.brain.navigation.navigation_tools import covering_bodies
    from dbv.client_bridge.protocol import ClientOpcode

    tile = web_tile()
    sites = [cell(11, 9), cell(11, 11)][:site_count]
    for site in sites:
        site['items'][0]['flags'] = ['ground']
    cells = [cell(*ORIGIN), tile, *sites]
    controller, _, _ = setup(state, tmp_path, cells, [ORIGIN, WEB])
    controller.status = 'running'
    fake_tools(monkeypatch, (8900,))
    clock = [0.]
    history = state.game_state.movement_history = MovementHistory(tmp_path, clock=lambda: clock[0])
    edge = web_edge(ORIGIN, WEB)
    for stack in (2, 3):
        tile['items'].append(dict(id=8506, stack=stack, flags=['container', 'force_use']))
    commands, drops = [], []

    async def dispatch(payload, guard):
        assert guard()
        controller.last_command = 0
        commands.append(payload[0])
        if payload[0] == ClientOpcode.MOVE_THING:
            x, y, z, item, layer, dx, dy, dz, amount = struct.unpack('<HHBHBHHBB', payload[1:])
            assert [x, y, z] == WEB and item == tile['items'][layer]['id'] == 8506 and amount == 1
            drops.append((dx, dy, dz))
            if reject_first and len(drops) == 1:
                return
            body = tile['items'].pop(layer)
            for i, item in enumerate(tile['items']):
                item['stack'] = i
            site = next(c for c in sites if (c['x'], c['y'], c['z']) == drops[-1])
            site['items'].append(dict(body, stack=len(site['items'])))
        elif payload[0] == ClientOpcode.USE_ITEM_EX:
            assert not covering_bodies(tile)
            assert payload[14:] == bytes([183, 0, 1])
            cut(tile)
        else:
            assert payload[0] == 0x66 and tile['kind'] == 'clear'
            state.game_state.position = tuple(WEB)

    state.game_proxy.inject_to_server = dispatch
    async def visit(captured_at):
        await controller.transition(edge, dict(controller.map_now(), captured_at=captured_at))

    async def scenario():
        with pytest.raises(MovementDeferred):
            await visit(0)
        sent = controller._pending_body_move.sent_at
        with pytest.raises(MovementDeferred):
            await visit(sent)  # pre-command frame, even if its body count differs
        assert len(commands) == 1
        if reject_first:
            with pytest.raises(MovementDeferred):
                await visit(sent + BODY_ACK_SECONDS / 2)
            assert len(commands) == 1
            with pytest.raises(MovementDeferred):
                await visit(sent + BODY_ACK_SECONDS + .1)
            assert len(drops) == 2 and drops[1] != drops[0]
        with pytest.raises(MovementDeferred):
            await visit(controller._pending_body_move.sent_at + .2)
        await visit(controller._pending_body_move.sent_at + .2)

    asyncio.run(scenario())
    assert drops == ([(11, 9, 7), (11, 11, 7), (11, 11, 7)] if reject_first else
                     [(11, 9, 7), (11, 9 if site_count == 1 else 11, 7)])
    assert commands == [ClientOpcode.MOVE_THING] * len(drops) + [ClientOpcode.USE_ITEM_EX, 0x66]
    assert state.game_state.position == tuple(WEB)
    clock[0] = 31
    events = history.poll()['events']
    attempts = [e for e in events if e['event'] == 'body_move_requested']
    results = [e for e in events if e['event'] == 'body_move_observed']
    assert [e['destination'] for e in attempts] == [e['destination'] for e in results] == drops
    assert [(e['bodies_before'], e['bodies_after']) for e in results] == (
        ([(2, 2)] if reject_first else []) + [(2, 1), (1, 0)])
    assert all(e['target'] == tuple(WEB) for e in results)


def test_unidentified_containers_are_not_moved_and_occupied_destinations_wait(state,tmp_path):
    from dbv.brain.navigation.navigation_tools import covering_body
    from dbv.brain.cavebot.adaptive_cavebot import MovementDeferred
    tile=web_tile()
    controller,_,_=setup(state,tmp_path,[cell(*ORIGIN),tile],[ORIGIN,WEB])
    tile['items'].append(dict(id=4040,stack=2,flags=['container','force_use']))
    assert covering_body(tile) is None
    tile['items'][-1]['id']=8506
    occupied=cell(11,9)
    occupied['items'][0]['flags']=['ground']
    occupied.update(occupants=1,occupant_types={'player':1})
    with pytest.raises(MovementDeferred,match='Brak wolnego pola'):
        asyncio.run(controller.uncover_tool_target(web_edge(ORIGIN,WEB),dict(cells=[occupied,tile]),tile))
    assert state.game_proxy._inject_queue.empty()


def test_hunt_handles_intact_and_cut_web_on_ordinary_ground(tmp_path):
    from dbv.brain.hunts.hunt_routes import GroundMap,manual_circuit
    from dbv.brain.navigation.navigation_tools import has_cut_web
    kinds=load_confirmed_types(asset_sha256=CURRENT_DAT)
    only_web={i:f for i,f in kinds.items() if f['kind']=='web'}
    atlas=MapAtlas(tmp_path,'2'*64,web_definitions=only_web)
    # Real Gotrunks arrangement after AiO, with optional nonblocking decoration.
    intact=web_tile(183)
    intact['items'][0]['id']=486
    atlas.observe(dict(status='ready',cells=[cell(*ORIGIN),intact,cell(*END)]))
    edges=list(GroundMap(atlas,[]).neighbors(tuple(ORIGIN)))
    assert (tuple(WEB),45,True) in edges
    area=dict(seed=ORIGIN,cells=[[8,8,7]],sightings=[dict(point=END,observer_cell=[8,8,7],count=1)])
    assert manual_circuit(atlas,[],area)['transitions']==2
    after=cell(*WEB)
    after['items']=[dict(id=i,stack=n,flags=['ground','not_moveable'] if n==0 else ['not_moveable'])
                    for n,i in enumerate((486,2889,189))]
    assert has_cut_web(after['items'],only_web) and not has_cut_web(after['items'],{})
    atlas.observe(dict(status='ready',cells=[after]))
    saved=next(t for t in atlas.view(*WEB)['cells'] if t['x']==WEB[0])
    assert saved['web_cut'] and not cuttable_web(saved) and saved['kind']=='clear'
    assert manual_circuit(atlas,[],area)['transitions']==0
