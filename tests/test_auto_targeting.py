"""Target selection picks the closest living, reachable, recently seen monster."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from actions import auto_targeting as targeting

NOW = 1000.0
ME = (100, 100, 7)


def monster(x, y, health=100, seen=NOW):
    return {"x": x, "y": y, "z": 7, "health": health, "t": seen}


def test_closest_monster_wins():
    creatures = {0x40000001: monster(105, 100), 0x40000002: monster(101, 102), 0x40000003: monster(99, 99)}
    assert targeting.pick_target(creatures, ME, {}, NOW) == 0x40000003


def test_players_dead_stale_and_unreachable_monsters_are_skipped():
    creatures = {0x10000001: monster(100, 101), 0x40000001: monster(100, 101, health=0),
                 0x40000002: monster(100, 101, seen=NOW - 61), 0x40000003: monster(100, 101),
                 0x40000004: monster(108, 108)}
    assert targeting.pick_target(creatures, ME, {0x40000003: NOW + 5}, NOW) == 0x40000004
    assert targeting.pick_target(creatures, ME, {0x40000003: NOW - 5}, NOW) == 0x40000003


def test_no_position_means_no_target():
    assert targeting.pick_target({0x40000001: monster(1, 1)}, (0, 0, 0), {}, NOW) is None


def test_target_is_sent_once_then_repeated_and_cleared_when_luring(monkeypatch):
    gs = SimpleNamespace(dll_bridge=object(), lure_active=False, position=ME, unreachable_creatures={},
                         creatures={0x40000001: monster(101, 100, seen=NOW)}, attack_target=None, attack_target_id=0)
    state = SimpleNamespace(game_state=gs, game_proxy=SimpleNamespace(logged_in=True))
    clock = iter([NOW, NOW + 0.1, NOW + 0.6, NOW + 0.7])
    monkeypatch.setattr(targeting.time, "time", lambda: next(clock))
    ticks = []
    async def sleep(_):
        ticks.append(1)
        if len(ticks) == 5:
            gs.lure_active = True
        if len(ticks) == 7:
            raise asyncio.CancelledError
    bot = SimpleNamespace(state=state, is_connected=True, sleep=sleep, log=Mock(), inject_to_server=AsyncMock())
    try:
        asyncio.run(targeting.run(bot))
    except asyncio.CancelledError:
        pass
    assert bot.inject_to_server.await_count == 2  # at NOW and again after 0.5 s
    assert gs.attack_target is None and gs.attack_target_id == 0
