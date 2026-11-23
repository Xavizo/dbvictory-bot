"""Offline coverage for route progress around several nearby owned corpses."""
import asyncio
from types import SimpleNamespace

import hunter_knife_action as knife
from command_delivery import CommandReceipt
from game_state import GameState
from hunter_knife import FRESH, SPENT, OwnedStack
from potions import PotionScheduler, validate_config
from test_hunter_knife import tile
from test_potions import rule


A = (101, 100, 7)
B = (100, 101, 7)
ORIGIN = (100, 100, 7)


def group(monkeypatch):
    clock = [100.]
    monkeypatch.setattr(knife, "time", SimpleNamespace(time=lambda: clock[0], monotonic=lambda: clock[0]))
    game = GameState()
    target = dict(id=4, name="Mutant Fire Elemental", health=85, x=99, y=100, z=7, t=100.)
    game.position = ORIGIN
    game.creatures[4] = target
    game.attack_target_id = 4
    combat = SimpleNamespace(config={"enabled": True}, sent_target=4, target=target)
    worker = knife.KnifeAutomation(game, SimpleNamespace(status="running", combat=combat))
    worker.tool_ready = True
    for p in (A, B):
        worker.queue.stacks[p] = OwnedStack(1, 108., 99.8, 100.)
    def frame(t, first=FRESH, second=FRESH):
        clock[0] = target["t"] = t
        return worker.next(dict(captured_at=t, cells=[tile(A, first), tile(B, second)]), t)
    return worker, frame


def test_group_retains_route_for_two_observed_effects_while_target_and_healing_are_active(monkeypatch):
    worker, frame = group(monkeypatch)
    worker.pending = frame(100.)
    assert worker.pending.source == A and worker.hold()
    game = worker.game
    game.hp, game.max_hp, game.stats_updated_at = 40, 100, 100.
    assert worker.fighting()
    assert PotionScheduler().next_rule(validate_config({"rules": [rule()]}), game, 100., 100.)["id"] == "hp"
    assert frame(100.3) is None and worker.hold()
    worker.pending = frame(100.4, SPENT)
    assert worker.pending.source == B and worker.hold()
    assert frame(100.6, SPENT, SPENT) is None
    assert worker.done == 2 and worker.fighting() and not worker.hold()


def test_missing_effect_releases_group_hold_and_never_retries_that_body(monkeypatch):
    worker, frame = group(monkeypatch)
    worker.pending = frame(100.)
    assert worker.hold()
    assert frame(100.4) is None and worker.hold()
    assert frame(100.6) is None and not worker.hold()
    assert frame(101.) is None
    another = frame(101.6)
    assert another.source == B and A not in worker.queue.stacks and worker.done == 0


def test_temporarily_blocked_claims_do_not_restart_group_hold_without_progress(monkeypatch):
    worker, frame = group(monkeypatch)
    assert frame(100.) is not None and worker.hold()
    # The next two local checks both found a definite no-send. The map is
    # unchanged and all choices are deferred for this frame.
    worker.deferred_tiles.update((A, B))
    assert frame(100.) is None and not worker.hold()
    assert frame(100.6) is not None and not worker.hold()


def test_locally_cancelled_use_at_same_position_keeps_claim_until_next_frame(monkeypatch):
    async def play():
        clock = [100.]
        wall = lambda: clock[0] + 1000
        monkeypatch.setattr(knife, "time", SimpleNamespace(monotonic=lambda: clock[0], time=wall))
        monkeypatch.setattr("passive_map_win32.ReadOnlyClient", lambda: SimpleNamespace(close=lambda: None))
        monkeypatch.setattr("client_player_state.ClientPlayerReader", lambda *_: object())
        reader = SimpleNamespace(snapshot=lambda: {"slots": {6: 5908}, "sources": []})
        monkeypatch.setattr("client_equipment.EquipmentReader", lambda *_: reader)
        game = GameState()
        game.position, game.hp = ORIGIN, 100
        monitor = SimpleNamespace()
        config = {"enabled": True}
        state = SimpleNamespace(game_state=game, game_proxy=object(), automation_paused=False,
                                adaptive_cavebot=None, passive_map_monitor=monitor,
                                settings={"actions": {knife.NAME: config}})
        bot = SimpleNamespace(state=state, player_id=10, position=ORIGIN, is_connected=True)
        def publish(item):
            monitor._result = dict(status="ready", center=ORIGIN, captured_at=wall(),
                                   cells=[tile(A, item) if item else tile(A)])
        publish(None)
        attempts = []
        completed = []
        async def use(_payload, guard):
            assert guard()
            attempts.append(monitor._result["captured_at"])
            receipt = CommandReceipt(len(attempts), 1, "server", 0)
            receipt.finish("cancelled" if len(attempts) == 1 else "dispatched")
            bot.last_receipt = receipt
        async def tick(seconds):
            clock[0] = round(clock[0] + max(.001, seconds), 4)
            worker = game.hunter_knife
            if clock[0] == 100.1:
                worker.queue.stacks[A] = OwnedStack(1, 105., 100., 100.1)
                publish(FRESH)
            elif len(attempts) == 1:
                assert A in worker.queue.stacks
                if clock[0] >= 100.4:
                    publish(FRESH)
            elif len(attempts) == 2:
                publish(SPENT)
            if worker.done or clock[0] >= 101.:
                completed.append(worker.done)
                config["enabled"] = False
        bot.sleep, bot.inject_to_server = tick, use
        await knife.run_knife(bot)
        assert len(attempts) == 2 and attempts[1] > attempts[0] and completed == [1]
    asyncio.run(play())
