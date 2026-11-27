"""Food: eat a red ham every 10 seconds.

Uses the hotkey form of "use item" (position 0xFFFF, 0, 0), so the server
finds the ham in any open or closed backpack by itself.
"""
from dbv.client_bridge.constants import ITEM_RED_HAM
from dbv.client_bridge.protocol import build_use_item_packet

EVERY_SECONDS = 10
HOTKEY_POSITION = (0xFFFF, 0, 0)


def eat_packet(item_id=ITEM_RED_HAM):
    return build_use_item_packet(*HOTKEY_POSITION, item_id, 0, 0)


async def run(bot):
    meal = eat_packet()
    while True:
        if bot.is_connected:
            await bot.inject_to_server(meal)
            bot.log(f"Zjedzono jedzenie (przedmiot {ITEM_RED_HAM})")
        await bot.sleep(EVERY_SECONDS)
