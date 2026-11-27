"""Internal action: attach the observer DLL and feed its data into the game state.

Runs whenever the bot is connected. It
  1. picks the observer DLL that matches the verified client build,
  2. reuses its pipe if the DLL is already loaded, otherwise loads a fresh copy,
  3. binds the logged-in character in client memory and samples its vitals,
  4. copies nearby monsters from the DLL snapshots into ``game_state.creatures``,
  5. keeps the visual target marker lease alive while combat actions run.
"""
from __future__ import annotations

import asyncio, logging, os, shutil, time
from pathlib import Path

from dbv.client_bridge import injector
from dbv.client_bridge.observer_pipe import ObserverPipe

log = logging.getLogger("action.observer")

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
POLL_INTERVAL = 0.016      # ~60 snapshots per second
NEARBY = 10                # tiles (Chebyshev) around the player that count as nearby
FORGET_AFTER = 0.5         # keep a creature this long after it drops out of a snapshot
SILENCE_LIMIT = 30         # seconds without any snapshot before the DLL is reloaded
HEALTHY_WAIT = 3.0         # an already-loaded DLL must answer within this time
PIPE_WAIT = 9.0            # time for a freshly loaded DLL to open its pipe
FIRST_DATA_WAIT = 20.0     # time for that DLL to publish its first snapshot
CREATURE_IDS = range(0x10000000, 0x80000000)
COMBAT_ACTIONS = frozenset({"adaptive_cavebot", "auto_combat", "auto_targeting"})


def _make_bridge():
    from dbv.client_bridge.passive_map_win32 import ReadOnlyClient
    client = ReadOnlyClient()
    try:
        return ObserverPipe(client.profile)
    finally:
        client.close()


def _load_fresh_copy(profile):
    """Load a uniquely named copy: Windows ignores a second LoadLibrary of the same name."""
    source = os.path.join(PROJECT_ROOT, "dll", profile.observer_dll)
    if not os.path.isfile(source):
        raise FileNotFoundError(f"Missing observer DLL: {profile.observer_dll}")
    copy = os.path.join(os.path.dirname(source), f"dbvobserver_live_{time.time_ns()}.dll")
    shutil.copy2(source, copy)
    log.info("loading %s", os.path.basename(copy))
    injector.inject(copy)
    return copy


async def _first_snapshot(bot, bridge, within):
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        await bot.sleep(0.25)
        if bridge.poll() is not None:
            return True
    return False


async def _attach(bot, bridge):
    """Connect to the observer pipe, loading the DLL when needed. True on success."""
    await bot.sleep(0.2)  # let a just-started DLL thread create its pipe
    if bridge.open():
        if await _first_snapshot(bot, bridge, HEALTHY_WAIT):
            return True
        log.info("observer pipe is silent; loading a fresh DLL")
        bridge.close()
    try:
        _load_fresh_copy(bridge.profile)
    except Exception as exc:
        log.warning("observer DLL could not be loaded: %s", exc)
        bot.log(f"[DLL] Nie udało się załadować DLL: {exc}")
        return False
    deadline = time.monotonic() + PIPE_WAIT
    while time.monotonic() < deadline:
        await bot.sleep(0.3)  # poll; a fresh DLL opens its pipe within a second or two
        if bridge.open():
            if not await _first_snapshot(bot, bridge, FIRST_DATA_WAIT):
                log.info("observer DLL loaded but has not published data yet")
            return True
    return False


def _marker_active(state, gs, proxy):
    """The marker may show only while a combat action runs on a live, healthy session."""
    if not proxy or state.game_proxy is not proxy or not proxy.logged_in or state.automation_paused:
        return False
    tasks = getattr(state, "_action_tasks", {})
    if not any(name in COMBAT_ACTIONS and not task.done() for name, task in tasks.items()):
        return False
    sample = getattr(gs, "client_snapshot", None)
    return bool(sample) and sample.get("hp", 0) > 0 and 0 <= time.monotonic() - sample.get("monotonic", -100) < 0.5


def nearby_monsters(creatures, player_ids, position, now):
    """Turn one DLL snapshot into ``game_state.creatures`` entries.

    Returns ``(player_name, monsters)``. Monsters are alive creatures on the
    player's floor within ``NEARBY`` tiles. When the snapshot contains the
    player, distances are measured from that entry and positions are shifted
    into the client's own coordinates, so both always use one frame.
    """
    px, py, pz = position
    me = next((c for c in creatures if c.get("id") in player_ids), None)
    name = str(me.get("name", ""))[:64] if me else None
    ref = (me.get("x", 0), me.get("y", 0), me.get("z", 0)) if me and me.get("x", 0) > 0 else None
    rx, ry, rz = ref or (px, py, pz)
    dx, dy = (px - rx, py - ry) if ref and px > 0 else (0, 0)
    found = {}
    for c in creatures:
        cid, x, y, z = c.get("id", 0), c.get("x", 0), c.get("y", 0), c.get("z", 0)
        if cid not in CREATURE_IDS or cid in player_ids or c.get("hp", 0) <= 0:
            continue
        if (x, y) == (0, 0) or x > 65535 or z > 15:
            continue
        if rx > 0 and ry > 0 and (z != rz or max(abs(x - rx), abs(y - ry)) > NEARBY):
            continue
        found[cid] = {"health": c.get("hp", 0), "name": c.get("name", "?"),
                      "x": x + dx, "y": y + dy, "z": pz, "t": now, "source": "dll"}
    return name, found


def _merge(gs, monsters, now):
    gs.creatures.update(monsters)
    for cid in [cid for cid, info in gs.creatures.items()
                if info.get("source") == "dll" and cid not in monsters
                and now - info.get("t", 0) > FORGET_AFTER]:
        del gs.creatures[cid]


async def _follow(bot, state, gs, bridge, proxy, player_id):
    last_data = time.monotonic()
    while bot.is_connected and state.game_state is gs:
        if gs.client_snapshot and not gs.client_state_error:
            history = getattr(gs, "movement_history", None)
            if history:
                history.player_position(gs.client_snapshot["position"])
        creatures = bridge.poll()
        bridge.renew_marker(active=_marker_active(state, gs, proxy))
        now = time.time()
        if creatures is not None:
            last_data = time.monotonic()
            name, monsters = nearby_monsters(creatures, {player_id, gs.player_id}, gs.position, now)
            if name is not None:
                gs.player_name = name
            _merge(gs, monsters, now)
        elif time.monotonic() - last_data > SILENCE_LIMIT:
            bot.log(f"[DLL] Brak danych przez {SILENCE_LIMIT}s, ponowne ładowanie DLL")
            bridge.close()
            if await _attach(bot, bridge):
                bot.log("[DLL] DLL połączony ponownie")
            else:
                bot.log("[DLL] Ponowne ładowanie nie powiodło się; może być potrzebny restart gry")
            last_data = time.monotonic()
        await bot.sleep(POLL_INTERVAL)  # next snapshot


async def run(bot):
    state = bot.state
    bridge = _make_bridge()
    while not bot.is_connected:
        await bot.sleep(1.0)  # until a character is in game
    try:
        attached = await _attach(bot, bridge)
    except BaseException:
        bridge.close()
        raise
    if not attached:
        bridge.close()
        return

    from dbv.client_bridge.passive_map_win32 import ReadOnlyClient
    from dbv.client_bridge.client_player_state import ClientPlayerReader, bind_local_player, poll_bound_vitals
    gs = state.game_state
    client = ReadOnlyClient()
    proxy = state.game_proxy
    vitals = None
    try:
        reader = ClientPlayerReader(client, 0)
        if not await bind_local_player(bot, state, gs, reader):
            bot.log("[DLL] Brak potwierdzonej postaci dla bieżącego logowania.")
            return
        gs.dll_bridge = bridge  # status, panel and target selection look here
        if proxy is not None:
            proxy.client_target_marker = bridge.request_marker
        vitals = asyncio.create_task(poll_bound_vitals(bot, state, gs, reader, POLL_INTERVAL))
        try:
            await _follow(bot, state, gs, bridge, proxy, bot.player_id)
        except Exception:
            # Creature data is a convenience; the vitals sampler keeps healing alive.
            log.exception("creature feed stopped; vitals sampling continues")
            await vitals
    finally:
        if vitals is not None:
            vitals.cancel()
            await asyncio.gather(vitals, return_exceptions=True)
        if getattr(proxy, "client_target_marker", None) == bridge.request_marker:
            proxy.client_target_marker = None
        client.close()
        bridge.close()
