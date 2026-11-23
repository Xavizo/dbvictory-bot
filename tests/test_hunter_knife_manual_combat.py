"""Manual targeting during standalone Hunter Knife processing, without a game client."""
import asyncio
from types import SimpleNamespace as NS

import pytest

import hunter_knife_action as knife
from command_delivery import CommandReceipt
from game_state import GameState
from hunter_knife import FRESH, SPENT
from potions import PotionScheduler, validate_config
from test_hunter_knife import NAME, tile
from test_potions import rule


ORIGIN = (100, 100, 7)
FURTHER = (108, 108, 7)
OWN_TILES = ((99, 100, 7), (100, 101, 7), (101, 100, 7), (99, 101, 7))
FOREIGN = (101, 101, 7)
COVERED = (100, 99, 7)
FREE = (99, 99, 7)
LIVE_TARGET = 77


@pytest.mark.parametrize("scenario", ["fighting", "target_at_dispatch", "move_at_dispatch"])
def test_standalone_manual_target_and_corpse_group(monkeypatch, scenario):
    async def play():
        clock = [100.]
        wall = lambda: 1000. + clock[0]
        monkeypatch.setattr(knife, "time", NS(monotonic=lambda: clock[0], time=wall))
        monkeypatch.setattr("passive_map_win32.ReadOnlyClient", lambda: NS(close=lambda: None))
        monkeypatch.setattr("client_player_state.ClientPlayerReader", lambda *_: object())
        reader = NS(snapshot=lambda: {"slots": {6: 5908}, "sources": []})
        monkeypatch.setattr("client_equipment.EquipmentReader", lambda *_: reader)

        game = GameState()
        game.position, game.hp, game.max_hp = ORIGIN, 40, 100
        game.creatures[LIVE_TARGET] = dict(health=80, name="Wolf", x=102, y=100, z=7, t=wall())
        game.attack_target_id = LIVE_TARGET if scenario == "fighting" else 0
        config = {"enabled": True}
        monitor = NS()
        state = NS(game_state=game, game_proxy=object(), automation_paused=False,
                   adaptive_cavebot=None, passive_map_monitor=monitor, settings={"actions": {knife.NAME: config}})
        bot = NS(state=state, player_id=10, position=ORIGIN, is_connected=True)
        # An existing, unclaimed fresh body has exactly the same ID as all our later kills.
        contents = {p: [] for p in (*OWN_TILES, COVERED, FREE)}
        contents[FOREIGN] = [FRESH]
        sent, cancelled, completed = [], [], []
        loot_added = False
        potion = PotionScheduler()
        heal_config = validate_config({"rules": [rule()]})

        def publish():
            cells = [dict(tile(p, *items), kind="clear") for p, items in contents.items()]
            monitor._result = dict(status="ready", center=bot.position, captured_at=wall(), cells=cells)

        async def use(_payload, guard):
            attempt = game.hunter_knife.pending
            assert attempt.source in OWN_TILES and attempt.drop is None
            assert guard()
            game.stats_updated_at = wall()
            assert potion.next_rule(heal_config, game, clock[0], wall())["id"] == "hp"
            if not sent and not cancelled:
                game.attack_target_id = LIVE_TARGET
                game.creatures[LIVE_TARGET]["t"] = wall()
                if scenario == "move_at_dispatch":
                    game.position = bot.position = FURTHER
                    publish()
            ok = guard()
            receipt = CommandReceipt(len(sent) + len(cancelled) + 1, 1, "server", 0)
            receipt.finish("dispatched" if ok else "cancelled")
            bot.last_receipt = receipt
            if ok:
                assert game.position == ORIGIN and game.attack_target_id == LIVE_TARGET
                sent.append(attempt.source)
                contents[attempt.source] = [SPENT]
            else:
                cancelled.append(attempt.source)

        async def tick(seconds):
            nonlocal loot_added
            clock[0] = round(clock[0] + max(seconds, .001), 4)
            runtime = game.hunter_knife
            game.creatures[LIVE_TARGET]["t"] = wall()
            if not loot_added:
                for cid, p in enumerate((*OWN_TILES, COVERED), 1):
                    runtime.queue.death(dict(name=NAME, cid=cid, x=p[0], y=p[1], z=p[2]), clock[0])
                    runtime.queue.personal_loot(f"[{NAME}]: nothing.", clock[0])
                    contents[p] = [FRESH, SPENT] if p == COVERED else [FRESH]
                loot_added = True
            if clock[0] >= 102.:
                completed.append(runtime.done)
                assert COVERED in runtime.queue.stacks
                config["enabled"] = False
            publish()

        bot.inject_to_server, bot.sleep = use, tick
        publish()
        await knife.run_knife(bot)
        assert game.attack_target_id == LIVE_TARGET and contents[FOREIGN] == [FRESH]
        assert contents[COVERED] == [FRESH, SPENT]
        if scenario == "move_at_dispatch":
            assert len(cancelled) == 1 and sent == [] and completed == [0]
            assert game.position == FURTHER
        else:
            assert not cancelled and len(sent) == len(set(sent)) == len(OWN_TILES)
            assert set(sent) == set(OWN_TILES) and completed == [len(OWN_TILES)]
            assert game.position == ORIGIN
    asyncio.run(play())
