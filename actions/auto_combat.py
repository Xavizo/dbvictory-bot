"""Walka: fight without a route, with the cavebot's combat settings of its own.

Uses the panel's Techniki, Potwory and Dodatkowe ustawienia. A monster is
selected only while it is on screen, on this floor and reachable. With Hold
target, the last target is selected again when it comes back after leaving the
screen or the floor. Movement stays manual.

The cavebot comes first: while it runs, this action waits, and it takes over
again once the cavebot stops.
"""


async def run(bot):
    await bot.state.run_standalone_combat(bot)
