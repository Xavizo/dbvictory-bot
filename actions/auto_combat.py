"""Automatic combat, checked every 0.2 s, first match wins:

1. Two or more living monsters next to the character: cast Energy Grenade.
2. A target and at least 5% mana: cast Darkness Blaster.
3. A target and less mana: use rune 3165 on it.

The target is the one chosen by target selection (``game_state.attack_target``)
or, without it, the last monster the player clicked in the client. Nothing is
cast while the cavebot is luring.
"""
import itertools
import time

from dbv.client_bridge.constants import ITEM_RUNE_3165, MONSTER_ID_MIN
from dbv.client_bridge.protocol import ClientOpcode, build_use_on_creature_packet

TICK = 0.2
AREA_SPELL, AREA_MIN_MONSTERS, AREA_RANGE = "Energy Grenade", 2, 1
SPELL, SPELL_MIN_MANA_PERCENT = "Darkness Blaster", 5
RUNE = ITEM_RUNE_3165
STALE_AFTER = 60  # seconds since a creature was last seen


def adjacent_monsters(creatures, position, now):
    """Living, recently seen monsters within AREA_RANGE tiles on the player's floor."""
    if not position or position[0] <= 0 or position[1] <= 0:
        return 0
    px, py, pz = position
    return sum(1 for cid, c in creatures.items()
               if cid >= MONSTER_ID_MIN and 0 < c.get("health", 0) <= 100 and c.get("z") == pz
               and now - c.get("t", 0) < STALE_AFTER
               and max(abs(c.get("x", 0) - px), abs(c.get("y", 0) - py)) <= AREA_RANGE)


def mana_percent(gs):
    return gs.mana * 100 / gs.max_mana if gs.max_mana > 0 else 100


def choose(gs, clicked, now):
    """What to do this tick: ("say", words), ("rune", target) or None."""
    if adjacent_monsters(gs.creatures, gs.position, now) >= AREA_MIN_MONSTERS:
        return "say", AREA_SPELL
    target = getattr(gs, "attack_target", None) or clicked
    if not target:
        return None
    known = gs.creatures.get(target)
    if known and known.get("health", 0) <= 0:
        return None
    if mana_percent(gs) >= SPELL_MIN_MANA_PERCENT:
        return "say", SPELL
    return "rune", target


async def run(bot):
    state = bot.state
    proxy = state.game_proxy  # the relay this run listens on
    clicked = [None]  # last attack the player sent from the client

    def on_client_packet(opcode, reader):
        if ClientOpcode.ATTACK == opcode:
            try:
                clicked[0] = reader.read_u32() or None
            except Exception:
                pass

    proxy.register_client_packet_callback(on_client_packet)
    bot.log("[COMBAT] Start: obszarowy > czar > runa")
    paused = False
    try:
        for _ in itertools.count():   # until the action is stopped
            gs = state.game_state
            if state.game_proxy and bot.is_connected:
                if gs.lure_active != paused:
                    paused = gs.lure_active
                    bot.log("[COMBAT] Cavebot prowadzi potwory, walka wstrzymana" if paused
                            else "[COMBAT] Walka wznowiona")
                step = None if paused else choose(gs, clicked[0], time.time())
                if step and step[0] == "say":
                    await bot.say(step[1])
                elif step:
                    await bot.inject_to_server(build_use_on_creature_packet(0xFFFF, 0, 0, RUNE, 0, step[1]))
            await bot.sleep(TICK)
    finally:
        proxy.unregister_client_packet_callback(on_client_packet)
        bot.log("[COMBAT] Stop")
