"""Speed Up: losowy termin względem końca efektu, bez powtarzania co sekundę."""
from dbv.brain.combat.speed_up import run_speed

async def run(bot):
    await run_speed(bot)
