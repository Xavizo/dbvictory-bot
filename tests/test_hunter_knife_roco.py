import asyncio

import pytest

from dbv.brain.combat.hunter_knife import FRESH, ROCO, SPENT, loot_species
from dbv.brain.combat.hunter_knife_action import KnifeAutomation
from test_hunter_knife import tile
from test_hunter_knife_visible import CENTER, OWN, KnifeReplay


@pytest.mark.parametrize("name", ["Roco", "Chibi Roco", "Fury Roco"])
def test_recognizes_personal_loot_for_each_roco(name):
    assert loot_species(f"16:01 [{name}]: nothing.") == name.casefold()
    assert loot_species(f"[{name} Summon]: nothing.") is None


@pytest.mark.parametrize("cavebot", [False, True])
@pytest.mark.parametrize("result", ["skinned", "decayed", "gone", "refused"])
def test_short_lived_roco_first_and_then_elemental(monkeypatch, cavebot, result):
    sim = KnifeReplay(monkeypatch, cavebot)
    pos = (1587, 923, 7)
    center = (1586, 923, 7)
    sim.bot.position = sim.game.position = center
    sim.contents = {center: [FRESH], pos: [ROCO]}
    target_ids = []
    async def send(payload, guard):
        await sim.dispatch(payload, guard)
        target_ids.append(int.from_bytes(payload[-3:-1], "little"))
    sim.bot.inject_to_server = send

    def advance(s):
        if s.outcome is None:
            return
        if s.outcome.source == center:
            s.answer(denied=False)
            return
        assert s.tick == 1 and s.outcome.source == pos
        if result == "refused":
            s.answer(denied=True)
        else:
            s.outcome = None
            s.contents[pos] = {"skinned": [SPENT], "decayed": [8549], "gone": []}[result]

    asyncio.run(sim.run(monkeypatch, advance, limit=25))
    assert sim.attempts == [(pos, 2), (center, 2)]
    assert target_ids == [8548, 9068]
    assert sim.worker.done == (2 if result == "skinned" else 1)
    assert sim.game.knife_denials == int(result == "refused")
    assert sim.game.knife_loot_count == 0


def test_mixed_stack_refusal_and_replacement_keep_independent_budgets(monkeypatch):
    sim = KnifeReplay(monkeypatch)
    worker = KnifeAutomation(sim.game)
    worker.tool_ready = True
    def frame(stamp, *bodies):
        return worker.next(dict(captured_at=stamp, cells=[tile(OWN[0], *bodies)]), stamp)

    denied = worker.pending = frame(100, FRESH)
    assert worker.rejected(100.01)
    assert not frame(100.1, FRESH)
    roco = worker.pending = frame(100.2, FRESH, ROCO)
    assert roco.item["id"] == ROCO and roco.layer != denied.layer
    # The elemental underneath changes; this cannot confirm a use on Roco.
    worker.acknowledge({OWN[0]: tile(OWN[0], SPENT, ROCO)}, 100.3, 100.3)
    assert worker.done == 0 and worker.pending is roco


def test_skips_roco_that_disappears_before_delivery(monkeypatch):
    sim = KnifeReplay(monkeypatch)
    sim.contents[OWN[0]] = [ROCO]
    checks = []
    async def delayed(_payload, guard):
        sim.contents[OWN[0]] = []
        sim.now += .02
        sim.publish()
        checks.append(guard())
        sim.bot.last_receipt = None
    sim.bot.inject_to_server = delayed
    asyncio.run(sim.run(monkeypatch, lambda _: None, limit=5))
    assert checks == [False] and sim.worker.done == 0
