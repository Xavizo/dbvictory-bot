"""An unreadable live tile on a recorded transition falls back to the atlas record."""
import asyncio
import time

import pytest
from dbv.brain.cavebot import adaptive_cavebot
from dbv.client_bridge.protocol import WALK_OPCODES, ClientOpcode, PacketReader
from test_adaptive_cavebot import cell, setup
from test_automation_safety import state

APPROACH, LADDER, LANDING = [1653,1105,5], [1652,1105,5], [1652,1106,4]
MOVED = "Odczyt pola niepełny. Pole zmieniło położenie podczas odczytu."


@pytest.fixture(autouse=True)
def short_grace(monkeypatch):
    monkeypatch.setattr(adaptive_cavebot, "KNOWN_TRANSITION_GRACE", .2)


def ladder_route(state, tmp_path):
    ladder = cell(*LADDER, item=408)
    ladder['items'].append(dict(id=1948, stack=1, flags=[]))
    ladder['navigation'] = [dict(kind='ladder', item_id=1948, stack=1)]
    edge = dict(id='castle-ladder', origin=LADDER, entry=LADDER, exit=LANDING, action='use',
                item_id=1948, entry_items=[408,1948], evidence='observed')
    cells = [cell(*APPROACH), ladder, cell(*LANDING)]
    c, cells, _ = setup(state, tmp_path, cells, [APPROACH, LADDER, LANDING], [edge])
    return c, cells


def make_unreadable(cells, point, reason=MOVED):
    tile = next(t for t in cells if [t['x'],t['y'],t['z']] == point)
    tile.clear()
    tile.update(x=point[0], y=point[1], z=point[2], kind='unknown', items=[], occupants=0,
                stairs=None, reason=reason)
    return tile


def fake_server(state, c):
    """Walks move one tile; using the ladder item climbs to the landing."""
    sent = []
    directions = {int(WALK_OPCODES[d]): delta for delta, d in adaptive_cavebot.OFFSETS.items()}
    async def inject(payload, guard=None):
        assert guard and guard()
        sent.append(payload)
        if payload[0] in directions:
            dx, dy = directions[payload[0]]
            x, y, z = state.game_state.position
            state.game_state.position = (x+dx, y+dy, z)
        else:
            assert payload[0] == ClientOpcode.USE_ITEM
            r = PacketReader(payload[1:])
            used = (r.read_u16(), r.read_u16(), r.read_u8(), r.read_u16(), r.read_u8())
            if used == (*LADDER, 1948, 1):
                state.game_state.position = tuple(LANDING)
        state.game_state.stats_updated_at = time.time()
        c.last_command = 0
    state.game_proxy.inject_to_server = inject
    return sent


async def run_until_done(c, timeout=4):
    run = asyncio.create_task(c.run())
    try:
        async with asyncio.timeout(timeout):
            while not run.done() and not c.laps:
                assert not c.navigation_hold, c.navigation_hold
                await asyncio.sleep(.01)
    finally:
        if not run.done() and c.status == 'running':
            c.pause('koniec testu')
        await run


@pytest.mark.parametrize('combat', [False, True])
def test_unreadable_ladder_is_climbed_from_the_atlas_record(state, tmp_path, combat):
    c, cells = ladder_route(state, tmp_path)
    make_unreadable(cells, LADDER)
    c.combat.config['enabled'] = combat
    sent = fake_server(state, c)
    started = time.monotonic()
    asyncio.run(run_until_done(c))
    assert c.laps == 1, c.reason
    assert list(state.game_state.position) == LANDING
    # One step onto the ladder, then one use of the recorded ladder object.
    assert [p[0] for p in sent] == [sent[0][0], ClientOpcode.USE_ITEM]
    assert sent[0][0] in {int(v) for v in WALK_OPCODES.values()}
    # The live read was retried before trusting the record, for each action.
    assert time.monotonic() - started >= 2 * adaptive_cavebot.KNOWN_TRANSITION_GRACE
    assert sum('znanego przejścia z Atlasu' in line for line in c.logs) == 2


def test_readable_again_ladder_uses_the_live_tile(state, tmp_path):
    c, cells = ladder_route(state, tmp_path)
    saved = dict(next(t for t in cells if [t['x'],t['y'],t['z']] == LADDER))
    tile = make_unreadable(cells, LADDER)
    sent = fake_server(state, c)
    async def recovers():
        await asyncio.sleep(.05)
        tile.clear();tile.update(saved)
    async def scenario():
        await asyncio.gather(run_until_done(c), recovers())
    asyncio.run(scenario())
    assert c.laps == 1 and len(sent) == 2
    assert not any('znanego przejścia' in line for line in c.logs)


def test_unrecorded_unreadable_tile_still_waits(state, tmp_path):
    a, b, d = [10,10,7], [11,10,7], [12,10,7]
    c, cells, _ = setup(state, tmp_path, [cell(*a), cell(*b), cell(*d)], [a, d])
    make_unreadable(cells, b)
    c.combat.config['enabled'] = True
    sent = fake_server(state, c)
    async def scenario():
        run = asyncio.create_task(c.run())
        await asyncio.sleep(3 * adaptive_cavebot.KNOWN_TRANSITION_GRACE)
        assert c.blockage and c.blockage['tile_kind'] == 'unknown'
        c.pause('koniec testu')
        await run
    asyncio.run(scenario())
    assert not sent and not any('znanego przejścia' in line for line in c.logs)


def test_unloaded_ladder_tile_is_not_trusted(state, tmp_path):
    c, cells = ladder_route(state, tmp_path)
    make_unreadable(cells, LADDER, "Pole nie jest załadowane przez klienta.")
    c.combat.config['enabled'] = True
    sent = fake_server(state, c)
    async def scenario():
        run = asyncio.create_task(c.run())
        await asyncio.sleep(3 * adaptive_cavebot.KNOWN_TRANSITION_GRACE)
        c.pause('koniec testu')
        await run
    asyncio.run(scenario())
    assert not sent


def test_atlas_record_must_hold_the_transition_object(state, tmp_path):
    c, cells = ladder_route(state, tmp_path)
    assert c.known_transition_tile(LADDER)['items'][1]['id'] == 1948
    # Another object recorded there since: the link is no longer trusted.
    c.state.passive_map_monitor.atlas.observe(dict(status='ready', cells=[cell(*LADDER, item=408)]))
    assert c.known_transition_tile(LADDER) is None
    # Ordinary tiles are never a known transition.
    assert c.known_transition_tile(APPROACH) is None


def test_door_record_is_never_used_as_fallback():
    edge = dict(action='use', item_id=5125, open_item_id=5126)
    tile = dict(items=[dict(id=5125, stack=1)])
    assert not adaptive_cavebot.edge_object_present(tile, edge)
    assert adaptive_cavebot.edge_object_present(tile, dict(action='use', item_id=5125))
