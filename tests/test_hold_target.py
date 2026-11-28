"""Hold target and the full-screen range: synthetic combat, no game or real packets."""
import asyncio
import time
import pytest
from dbv.brain.cavebot import cavebot_combat
from dbv.brain.cavebot.cavebot_combat import validate_combat
from test_automation_safety import state, proxy  # noqa: F401 (fixtures)
from test_v2_combat import armed, monster


def test_eight_tiles_is_the_screen_edge_and_is_accepted():
    # The panel offered "whole screen" and 8 SQM; starting then failed with "range".
    config = validate_combat(dict(enabled=True, engage='near', radius=8, rules=[dict(pattern='*')],
                                  spells=[dict(words='Spell', trigger='visible', range=8),
                                          dict(words='Other', trigger='distance', range=8)]))
    assert config['radius'] == 8 and [s['range'] for s in config['spells']] == [8, 8]
    with pytest.raises(ValueError):
        validate_combat(dict(spells=[dict(words='Spell', range=9)]))


def test_hold_target_defaults_off_and_must_be_a_switch():
    assert validate_combat()['hold_target'] is False
    assert validate_combat(dict(hold_target=True))['hold_target'] is True
    with pytest.raises(ValueError):
        validate_combat(dict(hold_target=1))


def _leave(row, tile):
    tile.update(occupants=0, occupant_types={})
    row['z'] = 6  # went up the stairs


def _return(row, tile):
    tile.update(occupants=1, occupant_types={'monster': 1})
    row['z'] = 7


def test_held_target_is_picked_again_before_a_closer_monster(state, tmp_path):
    c, cells = armed(state, tmp_path, hold_target=True)
    first, row, tile = monster(state, cells, x=13, y=10, cid=0x40000001)
    c.status = 'running'

    async def scenario():
        await c.combat.tick(state.passive_map_monitor.snapshot())
        assert c.combat.sent_target == first
        _leave(row, tile)
        near, near_row, _ = monster(state, cells, x=11, y=10, cid=0x40000002)
        await c.combat.tick(state.passive_map_monitor.snapshot())
        assert c.combat.sent_target == near            # fights what is there meanwhile
        assert c.combat.snapshot()['held'] == dict(id=first, name='Wolf', lost=True)
        _return(row, tile)
        row['t'] = near_row['t'] = time.time()
        await c.combat.tick(state.passive_map_monitor.snapshot())
        assert c.combat.sent_target == first           # the held one comes first again
    asyncio.run(scenario())


def test_without_hold_the_nearest_monster_wins(state, tmp_path):
    c, cells = armed(state, tmp_path)
    first, row, tile = monster(state, cells, x=13, y=10, cid=0x40000001)
    c.status = 'running'

    async def scenario():
        await c.combat.tick(state.passive_map_monitor.snapshot())
        _leave(row, tile)
        near, near_row, _ = monster(state, cells, x=11, y=10, cid=0x40000002)
        await c.combat.tick(state.passive_map_monitor.snapshot())
        _return(row, tile)
        row['t'] = near_row['t'] = time.time()
        await c.combat.tick(state.passive_map_monitor.snapshot())
        assert c.combat.sent_target == near and c.combat.snapshot()['held'] is None
    asyncio.run(scenario())


def test_hold_ends_when_the_target_dies_or_stays_away(state, tmp_path, monkeypatch):
    c, cells = armed(state, tmp_path, hold_target=True)
    first, row, tile = monster(state, cells, x=13, y=10)
    c.status = 'running'
    asyncio.run(c.combat.tick(state.passive_map_monitor.snapshot()))
    _leave(row, tile)
    assert c.combat.held_candidate([]) is None and c.combat.held_target == first
    row['health'] = 0
    c.combat.held_candidate([])
    assert c.combat.held_target == 0
    c.combat.held_target, c.combat.held_lost_at = first, time.monotonic() - cavebot_combat.HOLD_SECONDS - 1
    row['health'] = 50
    c.combat.held_candidate([])
    assert c.combat.held_target == 0
