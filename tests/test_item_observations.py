"""Only relevant session changes or the scheduler's own deadline wake item decisions."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from dbv.brain.state.item_observations import observed_inputs, wait_for_inputs
from dbv.brain.state.session_observations import session_observations


def test_observation_between_check_and_sleep_is_not_lost_or_reused_across_sessions():
    async def replay():
        game = SimpleNamespace()
        other = SimpleNamespace()
        bot = SimpleNamespace(sleep=AsyncMock())
        first = session_observations(game)
        families = ("health",)
        cursor = observed_inputs(game, families)
        first.publish("client_snapshot", ("health",), 1.)
        await asyncio.wait_for(wait_for_inputs(bot, families, cursor, 1.), .5)
        cursor = observed_inputs(game, families)
        waiter = asyncio.create_task(wait_for_inputs(bot, families, cursor, 1.))
        session_observations(other).publish("client_snapshot", families, 2.)
        first.publish("atlas", ("map",), 2.)
        await asyncio.sleep(0)
        assert not waiter.done()
        first.publish("client_snapshot", families, 3.)
        await asyncio.wait_for(waiter, .5)
        assert [c.args for c in bot.sleep.await_args_list] == [(0,), (0,)]
    asyncio.run(replay())


def test_quiet_source_and_older_adapter_still_wake_for_cooldown_or_pause():
    async def replay():
        game = SimpleNamespace()
        families = ("inventory",)
        bot = SimpleNamespace(sleep=AsyncMock())
        await wait_for_inputs(bot, families, observed_inputs(game, families), .25)
        bot.sleep.assert_awaited_once_with(.25)
        session_observations(game)
        await wait_for_inputs(bot, families, observed_inputs(game, families), 0)
        bot.sleep.assert_awaited_with(0)
    asyncio.run(replay())
