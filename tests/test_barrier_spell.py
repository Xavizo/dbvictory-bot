import asyncio
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from dbv.brain.combat.barrier_spell import BarrierScheduler, DEFAULTS, run_barrier, validate_config
from dbv.brain.state.game_state import GameState, scan_packet
from dbv.client_bridge.packet_observer import detach_state, apply_scan


def sample(t, active=True, **kwargs):
    return {"hp": 100, "conditions": 0x8804 | (16 if active else 0), "monotonic": t, **kwargs}


def test_barrier_renewal_even_if_icon_stays_on_and_only_one_draw_per_cast():
    draws = []
    def draw(a, b):
        draws.append((a, b))
        return 20
    timer = BarrierScheduler(draw)
    cfg = validate_config({**DEFAULTS, "duration": 200})
    assert timer.observe(sample(10, False), cfg, 0, 10)
    timer.sent(None, 10)
    assert not timer.observe(sample(10.1), cfg, 0, 10.1)
    assert not timer.observe(sample(10.2), cfg, 10.15, 10.2)
    assert not timer.observe(sample(190), cfg, 10.15, 190)
    assert timer.observe(sample(190.2), cfg, 10.15, 190.2)
    timer.sent(None, 190.2)
    assert not timer.observe(sample(190.4), cfg, 190.35, 190.4)
    assert len(draws) == 2 and draws[0] == (5, 30)
    assert timer.due == pytest.approx(370.35)
    assert timer.observe(sample(191, False), cfg, 190.35, 191)


def test_unknown_start_does_not_recast_over_a_band_and_expiry_is_immediate():
    timer = BarrierScheduler()
    assert not timer.observe(sample(10), DEFAULTS, 0, 10)
    assert not timer.observe(sample(500), DEFAULTS, 0, 500)
    assert timer.observe(sample(501, False), DEFAULTS, 0, 501)


def test_manual_cooldown_arriving_before_icon_suppresses_duplicate_cast():
    timer = BarrierScheduler(lambda *_: 5)
    assert not timer.observe(sample(50.2, False), DEFAULTS, 50, 50.2)
    assert not timer.observe(sample(51), DEFAULTS, 50, 51)
    assert timer.due == 50 + DEFAULTS["duration"] - 5


def test_band_or_other_unknown_activation_does_not_start_spell_timer():
    timer = BarrierScheduler()
    assert timer.observe(sample(10, False, mana=10), DEFAULTS, 0, 10)
    assert not timer.observe(sample(11), DEFAULTS, 0, 11)
    assert timer.due is None
    assert timer.observe(sample(400,False), DEFAULTS, 0, 400)


def test_unconfirmed_early_renewal_waits_for_expiry_without_spamming():
    timer = BarrierScheduler(lambda *_: 20)
    cfg = {**DEFAULTS, "duration": 200}
    assert not timer.observe(sample(10), cfg, 10, 10)
    assert timer.observe(sample(190), cfg, 10, 190)
    timer.sent(None, 190)
    for t in (191, 193, 199, 205):
        assert not timer.observe(sample(t), cfg, 10, t)
    assert timer.observe(sample(210, False), cfg, 10, 210)


def test_absent_effect_handles_stale_samples_death_and_failed_casts():
    timer = BarrierScheduler()
    for s in (None, sample(5, False), sample(10, False, hp=0), sample(10, False, mana=0)):
        assert not timer.observe(s, DEFAULTS, 0, 10)
    assert timer.observe(sample(10, False), DEFAULTS, 0, 10)
    timer.sent(None, 10)
    for t in (11, 13, 14):
        assert not timer.observe(sample(t, False), DEFAULTS, 0, t)
    assert timer.observe(sample(15, False), DEFAULTS, 0, 15)


@pytest.mark.parametrize("cfg", [{"min_before":31}, {"min_before":-1}, {"duration":10}, {"max_before":False}, {"duration":float("nan")}, {"other":5}])
def test_invalid_config(cfg):
    with pytest.raises(ValueError):
        validate_config(cfg)


def test_only_barrier_spell_cooldown_survives_observer_publication():
    game = GameState()
    scan = detach_state(game, 0)
    def cooldown(op, spell):
        scan_packet(bytes([op, spell]) + struct.pack("<I",1500), scan.state)
    cooldown(0xa5, 219)
    cooldown(0xa4, 195)
    assert not hasattr(scan.state, "barrier_cast_at")
    cooldown(0xa4, 219)
    apply_scan(game, scan)
    assert game.barrier_cast_at == scan.state.barrier_cast_at > 0


def test_normal_action_entry_uses_own_spell_and_honors_pause_before_delivery():
    async def scenario():
        import time
        game = SimpleNamespace(player_id=1, client_snapshot=sample(time.monotonic(),False))
        state = SimpleNamespace(connected=True, automation_paused=False, game_state=game,
                                game_proxy=object(), settings={"actions":{"barrier_spell":{"enabled":True}}})
        async def cast(words, *, guard):
            assert words == "barrier" and guard()
            state.automation_paused = True
            assert not guard()
        bot = SimpleNamespace(state=state, say=AsyncMock(side_effect=cast), sleep=AsyncMock())
        await run_barrier(bot)
        assert bot.say.await_count == 1
    asyncio.run(scenario())
