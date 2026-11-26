import asyncio
from types import SimpleNamespace as NS

import pytest

from game_state import GameState
from potions import PotionDiagnostics, PotionScheduler, run_potions, validate_config
from test_potions import rule


def scene(*extra, enabled=True, cooldown=.1):
    gs = GameState()
    gs.player_id = 123
    gs.position = (100, 100, 7)
    gs.max_hp = gs.max_mana = 100
    rows = [rule("ki", 8465, resource="ki", threshold=30, cooldown=cooldown, interval=.1),
            *extra, rule("hp", 8465, threshold=60, cooldown=cooldown, interval=.1)]
    cfg = validate_config(dict(defer_senzu_ki=enabled, rules=rows), require_cooldowns=True)
    timer = PotionScheduler()

    def sample(t, ki, hp=100):
        gs.mana, gs.hp, gs.stats_updated_at = ki, hp, 1000 + t
        return timer.next_rule(cfg, gs, 100 + t, 1000 + t)

    return gs, cfg, timer, sample


def test_opt_in_and_validation():
    assert validate_config({})["defer_senzu_ki"] is False
    for invalid in (0, 1, "false", None):
        with pytest.raises(ValueError, match="boolean"):
            validate_config({"defer_senzu_ki": invalid})
    _, _, timer, sample = scene(enabled=False)
    sample(0, 95)
    assert sample(.25, 0)["id"] == "ki" and timer.deferral is None


def test_one_attempt_only_with_fresh_stats_and_real_cooldown():
    _, cfg, timer, sample = scene(cooldown=3)
    sample(0, 95)
    timer.record_item_use(8465, cfg, 100, 1000)
    assert sample(.25, 0) is None
    assert sample(1.24, 0) is None
    assert sample(1.25, 0) is None
    first = sample(3, 0)
    assert first["id"] == "ki"
    # Checking again at dispatch does not itself spend the attempt.
    assert sample(3, 0) == first
    timer.record_item_use(8465, cfg, 103, 1003, rule=first)
    assert sample(40, 0) is None
    assert timer.deferral.phase == "used"
    assert sample(41, 100) is None
    assert sample(41.25, 0) is None
    assert sample(42.25, 0)["id"] == "ki"


@pytest.mark.parametrize("cancel", ["recovery", "hit", "hp", "disabled"])
def test_first_wait_is_interruptible(cancel):
    gs, cfg, timer, sample = scene()
    sample(0, 95)
    assert sample(.25, 0) is None
    if cancel == "recovery":
        assert sample(.5, 30) is None and timer.deferral.phase == "normal"
        assert sample(.75, 25)["id"] == "ki"
    elif cancel == "disabled":
        cfg["defer_senzu_ki"] = False
        assert sample(.5, 0)["id"] == "ki" and timer.deferral is None
    else:
        if cancel == "hit":
            gs.last_incoming_hit = 100.45
        assert sample(.5, 0, 99 if cancel == "hp" else 100)["id"] == "ki"
        assert timer.deferral.phase == "normal"


@pytest.mark.parametrize("repeat, held", [(29., True), (30., True), (30.25, False)])
def test_repeat_window_starts_at_actual_attempt_and_manual_override_works(repeat, held):
    _, cfg, timer, sample = scene()
    sample(0, 95)
    sample(.25, 0)
    first = sample(1.25, 0)
    timer.record_item_use(8465, cfg, 101.25, 1001.25, rule=first)
    sample(1.5, 100)
    sample(1.25 + repeat - .25, 95)
    assert sample(1.25 + repeat, 0) is None
    assert (timer.deferral.phase == "held") == held
    if held:
        assert sample(100, 0) is None
        cfg["defer_senzu_ki"] = False
        assert sample(101, 0)["id"] == "ki"
    else:
        assert sample(2.25 + repeat, 0)["id"] == "ki"


@pytest.mark.parametrize("where", ["same_sample", "while_waiting", "held"])
def test_emergency_hp_has_priority_at_75_and_guard_can_recheck(where):
    _, cfg, timer, sample = scene()
    sample(0, 95)
    if where != "same_sample":
        sample(.25, 0)
    if where == "held":
        timer.record_item_use(8465, cfg, 100.5, 1000.5)  # manual Senzu during the wait
        sample(.75, 100)
        assert sample(1, 0) is None and timer.deferral.phase == "held"
    for _ in range(2):
        assert sample(1.25, 0, 75)["id"] == "hp"
    assert sample(1.5, 0, 100)["id"] == "ki"


def test_multi_step_recovery_and_other_items_keep_their_rules():
    _, _, _, sample = scene(rule("ki-potion", 10160, resource="ki", threshold=30))
    sample(0, 95)
    sample(.25, 48)
    assert sample(.5, 1)["id"] == "ki"
    sample(.75, 95)
    assert sample(1, 1)["id"] == "ki-potion"


def test_recent_hit_prevents_first_wait_and_session_resets_hold():
    gs, _, timer, sample = scene()
    sample(0, 95)
    gs.last_incoming_hit = 100.1
    assert sample(.25, 1)["id"] == "ki"
    sample(3, 95)
    assert sample(3.25, 0) is None
    gs.player_id += 1
    assert sample(3.5, 0)["id"] == "ki"
    assert timer.deferral.phase == "normal"


def test_wait_requires_new_stats_and_hp_remains_available_with_missing_ki():
    gs, cfg, timer, sample = scene()
    sample(0, 95)
    assert sample(.25, 0) is None
    assert timer.next_rule(cfg, gs, 101.5, 1001.5) is None
    gs.max_mana = 0
    assert sample(1.75, 0, 72)["id"] == "hp"


def test_confirmed_combat_does_not_raise_ordinary_hp_rule():
    gs, _, timer, sample = scene()
    sample(0, 95, 72)
    gs.last_incoming_hit = 100.2
    assert sample(.25, 0, 72)["id"] == "ki"
    assert not timer.deferral.emergency_hp


def test_late_hit_clears_repeat_hold():
    gs, cfg, timer, sample = scene()
    sample(0, 95)
    sample(.25, 0)
    timer.record_item_use(8465, cfg, 101.25, 1001.25)
    sample(1.5, 100)
    assert sample(1.75, 0) is None and timer.deferral.phase == "held"
    gs.last_incoming_hit = 101.9
    assert sample(2, 0)["id"] == "ki"


def test_action_dispatches_one_attempt_after_delay(monkeypatch):
    gs, cfg, timer, _ = scene()
    frame = [0, 95]
    calls = []
    def apply_frame(t, ki):
        frame[:] = [t, ki]
        gs.stats_updated_at, gs.hp, gs.mana = 1000 + t, 100, ki
    async def send(packet, guard):
        assert guard()
        calls.append((frame[0], int.from_bytes(packet[6:8], "little")))
    frames = iter([(.25, 0), (1, 0), (1.25, 1), (2, 0), (4, 0)])
    async def sleep(_):
        next_frame = next(frames, None)
        if next_frame is None:
            raise asyncio.CancelledError
        apply_frame(*next_frame)
    apply_frame(*frame)
    monkeypatch.setattr("potions.time.time", lambda: 1000 + frame[0])
    monkeypatch.setattr("potions.time.monotonic", lambda: 100 + frame[0])
    st = NS(settings={"actions": {"auto_potions": cfg}}, game_state=gs,
            potion_scheduler=timer, potion_diagnostics=PotionDiagnostics())
    bot = NS(state=st, is_connected=True, sleep=sleep, inject_to_server=send, log=lambda _: None)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_potions(bot))
    assert calls == [(1.25, 8465)]
