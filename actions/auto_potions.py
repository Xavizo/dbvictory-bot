"""Priority HP/Ki healing rules: Special Health Potion, Special Ki Potion and Senzu."""
from potions import run_potions


async def run(bot):
    await run_potions(bot)
