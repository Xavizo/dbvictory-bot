"""Target selection: always attack the closest living monster.

Every 50 ms the closest monster (by tile distance) from the observer feed is
picked. A new choice is sent to the server at once and the current one is
re-sent every 0.5 s, so a lost packet never leaves the character idle. The
proxy turns each sent attack into the red target marker in the client.

The choice is published as ``game_state.attack_target`` for other actions.
While the cavebot is luring, nothing is attacked.
"""
import time

from dbv.client_bridge.constants import MONSTER_ID_MIN
from dbv.client_bridge.protocol import build_attack_packet

TICK = 0.05
REPEAT_EVERY = 0.5
STALE_AFTER = 60  # seconds since the creature was last seen


def _tiles(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def pick_target(creatures, position, unreachable, now):
    """Closest attackable monster id, or None."""
    if not position or position[0] <= 0 or position[1] <= 0:
        return None
    best, best_distance = None, None
    for cid, info in creatures.items():
        if cid < MONSTER_ID_MIN or not 0 < info.get("health", 0) <= 100:
            continue
        if now - info.get("t", 0) >= STALE_AFTER or unreachable.get(cid, 0) > now:
            continue
        distance = _tiles((info.get("x", 0), info.get("y", 0)), position)
        if best_distance is None or distance < best_distance:
            best, best_distance = cid, distance
    return best


def _publish(gs, target):
    gs.attack_target, gs.attack_target_id = target, target or 0


async def run(bot):
    state = bot.state
    current, sent_at = None, 0.0
    while True:
        await bot.sleep(TICK)
        gs = state.game_state
        if not bot.is_connected or getattr(gs, "dll_bridge", None) is None:
            continue
        if gs.lure_active:
            if current is not None:
                bot.log("[TARGET] Cavebot prowadzi potwory, wstrzymuję atak")
                _publish(gs, None)
                current = None
            continue
        now = time.time()
        target = pick_target(gs.creatures, gs.position, gs.unreachable_creatures, now)
        if target is None:
            if current is not None:
                _publish(gs, None)
                current = None
            continue
        if target != current:
            info = gs.creatures[target]
            bot.log(f"Atakuję {info.get('name', '?')} (0x{target:08X}) hp={info['health']}% "
                    f"odległość={_tiles((info.get('x', 0), info.get('y', 0)), gs.position)}")
            current, sent_at = target, 0.0
        proxy = state.game_proxy
        if now - sent_at >= REPEAT_EVERY and proxy is not None and proxy.logged_in:
            sent_at = now
            await bot.inject_to_server(build_attack_packet(target))
        _publish(gs, target)
