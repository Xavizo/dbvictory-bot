"""Barrier Band 3051: uzupełnia pusty slot; aktywny band ma ID 3088."""
from dbv.brain.combat.barrier_band import run_band


async def run(bot):
    await run_band(bot)
