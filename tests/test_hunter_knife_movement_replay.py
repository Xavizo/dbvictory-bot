"""Offline acceptance replay for a route step between knife selection and dispatch."""
import asyncio
from types import SimpleNamespace

import pytest

import command_delivery
import hunter_knife_action as knife
from command_delivery import CommandReceipt
from hunter_knife import FRESH, SPENT


BODY = (100, 100, 7)
START = (100, 101, 7)
ADJACENT = (101, 101, 7)
OUTSIDE = (100, 102, 7)
NEXT_TARGET = (101, 100, 7)
SPECIES = "Fury Mutant Fire Elemental"


def tile(pos, corpse=None, monsters=0):
    items = [dict(id=1, stack=0, flags=["ground", "not_moveable"])]
    if corpse is not None:
        items.append(dict(id=corpse, stack=1, flags=["container", "force_use"]))
    return dict(x=pos[0], y=pos[1], z=pos[2], kind="clear", items=items,
                occupants=monsters, occupant_types={"monster": monsters})


async def replay(monkeypatch, route_step, *, receipt_status="dispatched", effect_delay_ms=0):
    elapsed_ms = 0
    now = lambda: 100 + elapsed_ms / 1000
    wall = lambda: 10000 + elapsed_ms / 1000
    fake_time = SimpleNamespace(time=wall, monotonic=now)
    monkeypatch.setattr(knife, "time", fake_time)
    monkeypatch.setattr(command_delivery, "time", fake_time)

    def mob(cid, pos):
        return dict(id=cid, name=SPECIES, x=pos[0], y=pos[1], z=pos[2], health=80, t=wall())

    first, second = 101, 102
    creatures = {first: mob(first, BODY)}
    game = SimpleNamespace(hp=100, position=START, attack_target_id=first,
                           experience=1000, creatures=creatures)
    combat = SimpleNamespace(config={"enabled": True}, sent_target=first, target=creatures[first])
    config = {"enabled": True}
    monitor = SimpleNamespace()
    state = SimpleNamespace(game_state=game, game_proxy=object(), automation_paused=False,
                            adaptive_cavebot=SimpleNamespace(status="running", combat=combat),
                            passive_map_monitor=monitor, settings={"actions": {knife.NAME: config}})
    bot = SimpleNamespace(player_id=10, state=state, position=START, is_connected=True)
    reader = SimpleNamespace(snapshot=lambda: {"slots": {6: 5908}, "sources": []})
    monkeypatch.setattr("passive_map_win32.ReadOnlyClient", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr("client_player_state.ClientPlayerReader", lambda *_: object())
    monkeypatch.setattr("client_equipment.EquipmentReader", lambda *_: reader)

    attempts = []
    moved = False
    dispatched = 0
    first_write_ms = None
    completed = 0
    claimed_at_end = None
    final_reason = None
    old_claim_intact = False

    def update_map():
        changed = (first_write_ms is not None and effect_delay_ms is not None
                   and elapsed_ms >= first_write_ms + effect_delay_ms)
        corpse = (SPENT if changed else FRESH) if first not in creatures else None
        monitor._result = dict(status="ready", center=bot.position, captured_at=wall(),
                               cells=[tile(BODY, corpse, int(first in creatures)),
                                      tile(NEXT_TARGET, monsters=int(second in creatures))])

    async def dispatch(_payload, guard):
        nonlocal dispatched, moved, old_claim_intact, first_write_ms
        worker = game.hunter_knife
        # Each attempt must be valid before any simulated route step. All the
        # other inputs stay fresh, including the tool and the corpse's stack.
        assert worker.pending.source == BODY and guard()
        if route_step and not moved and not worker.hold():
            moved = True
            game.position = bot.position = route_step
            update_map()
            old_claim_intact = BODY in worker.queue.stacks
        valid = guard()
        attempts.append((bot.position, valid))
        if valid:
            assert max(abs(bot.position[i] - BODY[i]) for i in (0, 1)) <= 1
            dispatched += 1
            first_write_ms = elapsed_ms
            assert dispatched == 1
        receipt = CommandReceipt(len(attempts), 1, "server", 0)
        receipt.finish(receipt_status if valid else "cancelled")
        bot.last_receipt = receipt
        update_map()

    async def tick(seconds):
        nonlocal elapsed_ms, completed, claimed_at_end, final_reason
        elapsed_ms += max(1, round(seconds * 1000))
        if elapsed_ms >= 100 and first in creatures:
            del creatures[first]
            creatures[second] = mob(second, NEXT_TARGET)
            combat.target = creatures[second]
            game.attack_target_id = combat.sent_target = second
            game.experience += 300
        if second in creatures:
            creatures[second]["t"] = wall()
        if bot.position == OUTSIDE and elapsed_ms >= 700:
            game.position = bot.position = START
        update_map()
        if elapsed_ms >= 2000:
            completed = game.hunter_knife.done
            claimed_at_end = BODY in game.hunter_knife.queue.stacks
            final_reason = game.hunter_knife.status
            config["enabled"] = False

    bot.sleep = tick
    bot.inject_to_server = dispatch  # In-process fake. Nothing is sent to the game.
    update_map()
    await knife.run_knife(bot)
    assert game.hunter_knife is None
    return attempts, moved, dispatched, completed, claimed_at_end, final_reason, old_claim_intact


@pytest.mark.parametrize("route_step", [None, ADJACENT, OUTSIDE],
                         ids=["stationary", "one-step-still-adjacent", "out-of-reach-then-back"])
def test_fresh_owned_body_survives_concurrent_route_step(monkeypatch, route_step):
    attempts, moved, sent, done, owned, reason, claim_before_guard = asyncio.run(
        replay(monkeypatch, route_step)
    )
    if route_step is not None:
        assert moved and claim_before_guard and attempts[0] == (route_step, False)
        assert sum(valid for _, valid in attempts) == 1
    assert sent == done == 1
    assert owned is False


@pytest.mark.parametrize("effect_delay_ms", [600, None], ids=["late-effect", "no-effect"])
def test_unknown_delivery_never_repeats_use_on_the_same_unchanged_body(monkeypatch, effect_delay_ms):
    attempts, _, possible_writes, done, _, reason, _ = asyncio.run(
        replay(monkeypatch, None, receipt_status="unknown", effect_delay_ms=effect_delay_ms)
    )
    assert attempts == [(START, True)]
    assert possible_writes == 1
    if effect_delay_ms is None:
        assert done == 0 and reason == "Brak potwierdzenia zmiany pola"
    else:
        assert done == 1 and reason == "Obrobiono ciało"


@pytest.mark.parametrize("manual", [None, 10.31])
def test_stack_effect_confirms_only_uncontested_use_and_splits_local_delivery_wait(manual):
    from hunter_knife import OwnedStack, body_counts

    async def observe():
        game = SimpleNamespace(knife_manual_use_at=manual)
        worker = knife.KnifeAutomation(game)
        worker.queue.stacks[BODY] = OwnedStack(1, 20., 10., 10.2)
        items = tile(BODY, FRESH)["items"]
        receipt = CommandReceipt(1, 1, "server", 0)
        receipt.finish("unknown")
        receipt.dispatched_at = 10.35
        worker.pending = knife.Attempt(BODY, items[-1], body_counts(items), 9., 10.3, None, 1, receipt)
        worker.acknowledge({BODY: tile(BODY, SPENT)}, 10., 10.55)
        assert not worker.queue.stacks
        if manual is None:
            assert worker.last_timing_ms == dict(recognition=200, wait_for_use=100, delivery_wait=50, confirmation=200)
            assert (worker.done, worker.unattributed, receipt.effect) == (1, 0, "confirmed")
        else:
            assert worker.last_timing_ms is None
            assert (worker.done, worker.unattributed, receipt.effect) == (0, 1, "pending")
    asyncio.run(observe())
