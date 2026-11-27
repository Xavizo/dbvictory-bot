"""The bot's picture of the game, kept up to date from server packets.

``scan_packet`` reads one decrypted server frame. A frame bundles several
messages back to back, so it reads them in order until it meets one whose
end it cannot know (map and tile data), then looks in the rest only for the
few things that must not be missed: stats, condition icons and door changes.

Message layouts come from ``packet_formats`` in offsets.json (``PF``), with
the values below as defaults.
"""
import logging
import struct
import time
from collections import deque

from dbv.brain.state.message_history import MessageHistory
from dbv.brain.state.pet_tracking import PetTracker
from dbv.client_bridge.client_player_state import client_field
from dbv.client_bridge.constants import PACKET_FORMATS as PF
from dbv.client_bridge.protocol import Direction, ServerOpcode as Op
from dbv.paths import INSTALL_ROOT

log = logging.getLogger("game_state")

STOP = -1                 # a handler's answer when the message's end is unknown
MONSTER = 0x40000000      # creature ids from here up are monsters
MAX_CREATURE_AGE = 120    # seconds; packet-sourced creatures older than this are dropped
PRUNE_INTERVAL = 1.0
# Bounds that tell real PLAYER_STATS apart from look-alike bytes inside map data.
MAX_VALID_HP = 50000
MAX_VALID_LEVEL = 5000
MAX_VALID_MANA = 50000
MAX_VALID_CAPACITY = 100000
WORLD_LIGHT = 0x82
COOLDOWN, GROUP_COOLDOWN = 0xA4, 0xA5
HASTE_SPELL, BARRIER_SPELL = 195, 219
DBV_CUSTOM_0XCB = 0xCB
MESSAGE_LOOK = 25         # DBVictory's reply to Look


def _fmt(name):
    return PF.get(name, {})


def _u16(data, at):
    return struct.unpack_from("<H", data, at)[0]


def _u32(data, at):
    return struct.unpack_from("<I", data, at)[0]


def _on_map(x, y, z=0):
    return 0 < x < 65535 and 0 < y < 65535 and z < 16


class GameState:
    """Everything known about the session. Vitals and position prefer the client snapshot."""

    hp = client_field("hp")
    max_hp = client_field("max_hp")
    mana = client_field("mana")
    max_mana = client_field("max_mana")
    stats_updated_at = client_field("stats_updated_at")
    level = client_field("level")
    magic_level = client_field("magic_level")
    position = client_field("position", (0, 0, 0))

    @mana.setter
    def mana(self, value):
        if not self.client_state_required and value != self.__dict__.get("mana", 0):
            self.ki_revision += 1
        self.__dict__["mana"] = value

    @property
    def client_snapshot(self):
        return self._client_snapshot

    @client_snapshot.setter
    def client_snapshot(self, sample):
        previous = self._client_snapshot
        if self.client_state_required and (previous or {}).get("mana") != (sample or {}).get("mana"):
            self.ki_revision += 1
        self._client_snapshot = sample
        journal = getattr(self, "observations", None)
        if journal is not None and sample is not None:
            from dbv.brain.state.session_observations import publish_player_sample
            publish_player_sample(journal, sample)

    def __init__(self):
        # Where vitals come from: the verified client snapshot or packets.
        self.client_state_required = False
        self.ki_revision = 0               # counts every Ki change at the active source
        self._client_snapshot = None
        self.client_player_id = 0
        self.session_id = 0
        # Identity.
        self.player_id = 0                 # creature id from LOGIN_OR_PENDING
        self.player_name = ""
        self.pets = PetTracker()
        # Vitals and character.
        self.hp = self.max_hp = 0
        self.mana = self.max_mana = 0
        self.level = self.experience = self.capacity = 0
        self.magic_level = self.soul = self.speed = 0
        self.last_incoming_hit = None
        self.stats_updated_at = 0
        self.player_icons = 0              # condition bitmask from PLAYER_ICONS
        self.player_party_shield = 0
        self.in_protection_zone = False
        self.world_light_level = self.world_light_color = 0
        # Position: ``position`` may come from the client, ``packet_position`` only from packets.
        self.position = (0, 0, 0)
        self.packet_position = (0, 0, 0)
        self.packet_position_trusted = True
        self.dll_position_active = False
        self.last_map_time = 0
        # Creatures, {id: {"health": 0..100, "x", "y", "z", "t", "source", ...}}.
        self.creatures = {}
        self._last_prune_time = 0
        # Combat.
        self.attack_target_id = 0
        self.lure_active = False           # the cavebot is luring: do not attack
        self.unreachable_creatures = {}    # {id: until}; target selection skips these
        self.last_cant_throw = 0
        # Kills and loot.
        self.session_kills = 0
        self.kill_log = deque(maxlen=5000)
        self._prev_experience = 0
        self._recent_kills = set()         # dedupe deaths seen twice
        self._recent_kills_cleanup = 0
        self.personal_loot_channel = None  # channel ids are per session; only the name says "loot"
        self.knife_feedback = deque(maxlen=96)
        self.knife_loot_count = self.knife_candidate_count = self.knife_denials = 0
        self.knife_manual_use_at = None
        # Messages.
        self.message_history = MessageHistory()
        self.messages = self.message_history.general
        # Recent world events for the cavebot and diagnostics.
        self.tile_updates = deque(maxlen=50)      # (time, x, y, z) of door-like tile changes
        self.server_events = deque(maxlen=100)    # (time, kind, details)
        # Walking.
        self.cancel_walk_time = 0
        self.last_walk_cancel = None
        self.client_walk_requests = deque(maxlen=12)  # local diagnostics only
        self._last_walk_delta = (0, 0)             # undone when the server cancels the step


# ── diagnostics ─────────────────────────────────────────────────────

_stats_log = None
_stats_lines = 0
STATS_LOG_LIMIT = 500


def _dump_stats_debug(gs: GameState, raw_hex: str | None) -> None:
    """Append the stats to stats_debug.txt (first 500 updates of a run only)."""
    global _stats_log, _stats_lines
    if getattr(gs, "_packet_snapshot_detached", False) or _stats_lines >= STATS_LOG_LIMIT:
        return
    _stats_lines += 1
    try:
        if _stats_log is None:
            _stats_log = open(INSTALL_ROOT / "stats_debug.txt", "a", encoding="utf-8")
        extra = f" raw={raw_hex}" if raw_hex else ""
        _stats_log.write(f"[{time.strftime('%H:%M:%S')}] HP={gs.hp}/{gs.max_hp} MP={gs.mana}/{gs.max_mana} "
                         f"Cap={gs.capacity} XP={gs.experience} Lv={gs.level} ML={gs.magic_level} "
                         f"Soul={gs.soul}{extra}\n")
        if _stats_lines % 5 == 0:
            _stats_log.flush()
    except Exception:
        pass


# ── shared effects ──────────────────────────────────────────────────

def _set_packet_position(gs, xyz, *, absolute=True):
    gs.position = gs.packet_position = xyz
    if absolute:
        gs.packet_position_trusted = True
    gs.last_map_time = time.time()
    gs._map_update_seen = True


def _forget_packet_creatures(gs):
    gs.creatures = {cid: c for cid, c in gs.creatures.items() if c.get("source") == "dll"}


def _record_kill(gs: GameState, cid: int) -> None:
    creature = gs.creatures.get(cid, {})
    px, py, pz = gs.position
    gs.kill_log.append({"t": time.time(), "name": creature.get("name", ""), "cid": cid,
                        "x": creature.get("x", 0), "y": creature.get("y", 0), "z": creature.get("z", 0),
                        "px": px, "py": py, "pz": pz, "segment": None, "loop": 0})
    gs.session_kills += 1


def _note_death(gs, cid):
    if cid >= MONSTER and cid not in gs._recent_kills:
        gs._recent_kills.add(cid)
        _record_kill(gs, cid)


def _check_pz_message(text: str, gs: GameState) -> None:
    """Follow protection-zone enter/leave messages."""
    lower = text.lower()
    if "protection zone" not in lower:
        return
    if any(word in lower for word in ("enter", "inside", "cannot attack")):
        inside = True
    elif "left" in lower or "leave" in lower:
        inside = False
    else:
        return
    if inside != gs.in_protection_zone:
        log.info("PZ detected: %s protection zone", "entered" if inside else "left")
    gs.in_protection_zone = inside


def _server_text(gs, kind, text, at, *, framed=True):
    """Effects of a server text message shared by both parsers."""
    if framed:
        gs.message_history.record_server(kind, text, at, getattr(gs, "_server_packet_seq", None))
    else:
        gs.message_history.record_server(kind, text, at)
    if kind == MESSAGE_LOOK:
        gs.pets.description(text, gs.creatures)
    callback = getattr(gs, "potion_message_callback", None)
    if callback and kind in (25, 26) and len(text) <= 240:
        try:
            callback(kind, text, batch_id=getattr(gs, "_server_packet_seq", None))
        except Exception:
            log.debug("Optional potion observation failed")
    lower = text.lower()
    if "can't throw there" in lower:
        gs.last_cant_throw = time.time()
    if framed and "not reachable" in lower:
        target = gs.attack_target_id
        if target and target >= MONSTER:
            gs.unreachable_creatures[target] = time.time() + 10
            gs.attack_target_id = 0
            log.info("NOT REACHABLE: skipping 0x%08X for 10 s", target)
    _check_pz_message(text, gs)


def _apply_stats(gs, data, at):
    f = _fmt("player_stats")
    gs.hp = _u32(data, at + f.get("hp", 0))
    gs.max_hp = _u32(data, at + f.get("max_hp", 4))
    gs.capacity = _u32(data, at + f.get("capacity", 8))
    gs.experience = struct.unpack_from("<Q", data, at + f.get("experience", 12))[0]
    gs.level = _u16(data, at + f.get("level", 20))
    gs.mana = _u32(data, at + f.get("mana", 23))
    gs.max_mana = _u32(data, at + f.get("max_mana", 27))
    gs.magic_level = data[at + f.get("magic_level", 31)]
    gs.soul = data[at + f.get("soul", 33)]
    gs.stats_updated_at = time.time()
    # Experience gained right after a kill belongs to that kill.
    gained = gs.experience - gs._prev_experience
    if gs._prev_experience > 0 and gained > 0 and gs.kill_log:
        kill = gs.kill_log[-1]
        if time.time() - kill["t"] < 2.0 and "xp" not in kill:
            kill["xp"] = gained
    gs._prev_experience = gs.experience
    log.info("Stats: HP=%s/%s MP=%s/%s Lv=%s XP=%s ML=%s", gs.hp, gs.max_hp, gs.mana, gs.max_mana,
             gs.level, gs.experience, gs.magic_level)
    _dump_stats_debug(gs, None)


# ── message handlers: (data, pos after the opcode, gs, received_at) -> next pos or STOP ──

def _stats(data, pos, gs, received_at):
    size = _fmt("player_stats").get("size", 36)
    if pos + size > len(data):
        return STOP
    _apply_stats(gs, data, pos)
    return pos + size


def _creature_health(data, pos, gs, received_at):
    f = _fmt("creature_health")
    size = f.get("size", 5)
    if pos + size > len(data):
        return STOP
    cid, health = _u32(data, pos + f.get("creature_id", 0)), data[pos + f.get("health", 4)]
    if time.time() > gs._recent_kills_cleanup + 30:  # a death is only seen twice within moments
        gs._recent_kills, gs._recent_kills_cleanup = set(), time.time()
    known = gs.creatures.get(cid)
    if known is not None:  # never create creatures from health updates alone
        was = known.get("health", -1)
        known["health"], known["t"] = health, time.time()
        if health == 0 and was > 0:
            _note_death(gs, cid)
    elif health == 0:
        _note_death(gs, cid)  # killed off screen, e.g. by an area spell
    return pos + size


_STRIP_STEP = {Op.MAP_SLICE_NORTH: (0, -1), Op.MAP_SLICE_EAST: (1, 0),
               Op.MAP_SLICE_SOUTH: (0, 1), Op.MAP_SLICE_WEST: (-1, 0)}


def _creature_move(data, pos, gs, received_at):
    """Thing (6) + destination (5) [+ u16 step time on DBVictory].

    A move directly followed by the map strip of the same direction, or by a
    floor description centred on the destination, is the player's own step.
    """
    size = _fmt("creature_move").get("size", 11)
    end = pos + size
    if end > len(data):
        return STOP
    if size >= 11 and end < len(data):
        old, new = struct.unpack_from("<HHB", data, pos), struct.unpack_from("<HHB", data, pos + 6)
        if _on_map(*old) and _on_map(*new):
            dx, dy, dz = new[0] - old[0], new[1] - old[1], new[2] - old[2]
            strip = _STRIP_STEP.get(data[end])
            own_step = (dz == 0 and abs(dx) <= 1 and abs(dy) <= 1 and strip is not None
                        and ((strip[0] and dx == strip[0]) or (strip[1] and dy == strip[1])))
            floor = (data[end] == _fmt("floor_description").get("opcode") and end + 7 <= len(data)
                     and struct.unpack_from("<HHB", data, end + 1) == new)
            if own_step or floor:
                _set_packet_position(gs, new)
                return STOP  # the strip's tile data follows
    return end


def _channels(data, pos, gs, received_at, *, listing):
    from dbv.brain.state.chat_messages import read_channel_names
    count = 1
    if listing:
        if pos >= len(data):
            return STOP
        count, pos = data[pos], pos + 1
    found = read_channel_names(data, pos, count)
    if found is None:
        return STOP
    end, names = found
    loot = [channel for channel, label in names.items() if label == b"loot"]
    if len(loot) == 1:
        gs.personal_loot_channel = loot[0]
    elif loot or gs.personal_loot_channel in names:
        gs.personal_loot_channel = None
    # Only CHANNELS has a known end; OPEN_CHANNEL may carry a player list after it.
    return end if listing or end == len(data) else STOP


def _close_channel(data, pos, gs, received_at):
    if pos + 2 > len(data):
        return STOP
    if _u16(data, pos) == gs.personal_loot_channel:
        gs.personal_loot_channel = None
    return pos + 2


def _talk(data, pos, gs, received_at):
    from dbv.brain.state.chat_messages import parse_talk
    parsed = parse_talk(data, pos, loot_channel=gs.personal_loot_channel)
    if parsed is None:
        return STOP
    end, message = parsed
    if message["kind"] == "personal_loot":
        from dbv.brain.combat.hunter_knife import loot_species
        species = loot_species(message["text"])
        if species:
            gs.knife_feedback.append(("loot", species))
        return end
    callback = getattr(gs, "alert_chat_callback", None)
    if callback and message["kind"] is not None:
        try:
            callback(message, gs)
        except Exception:
            pass  # notifications never break parsing
    return end


def _text_message(data, pos, gs, received_at):
    f = _fmt("text_message")
    if pos + f.get("header", 3) > len(data):
        return STOP
    kind = data[pos + f.get("type", 0)]
    start = pos + f.get("text", 3)
    end = start + _u16(data, pos + f.get("length", 1))
    if end > len(data):
        return STOP
    text = data[start:end].decode("latin-1", errors="replace")
    # Date the message by when the frame arrived, not by when it was parsed.
    at = time.time() - (time.perf_counter() - received_at) if received_at is not None else time.time()
    _server_text(gs, kind, text, at)
    log.debug("TEXT_MESSAGE type=%s length=%s", kind, len(text))
    return end


def _login(data, pos, gs, received_at):
    """u32 player id, a client-specific header, then the first map description."""
    f, where = _fmt("login_or_pending"), _fmt("position")
    id_size = f.get("player_id_size", 4)
    if pos + id_size > len(data):
        return STOP
    pid = _u32(data, pos)
    if gs.client_player_id:
        pass  # pinned to the verified client character for this login
    elif 0x10000000 <= pid < 0x20000000 or gs.player_id == 0:
        gs.player_id = pid
        log.info("LOGIN: player_id=0x%08X", pid)
    else:
        log.warning("LOGIN: ignoring unlikely player_id=0x%08X (keeping 0x%08X)", pid, gs.player_id)
    pos += id_size
    at = pos + f.get("header_before_position", 4)
    marker = f.get("map_description_marker", int(Op.MAP_DESCRIPTION))
    if at > pos and at + 5 <= len(data) and data[at - 1] == marker:
        xyz = (_u16(data, at + where.get("x", 0)), _u16(data, at + where.get("y", 2)), data[at + where.get("z", 4)])
        if _on_map(*xyz):
            _set_packet_position(gs, xyz)
            _forget_packet_creatures(gs)
            log.info("LOGIN position: %s", xyz)
    return STOP


def _map_description(data, pos, gs, received_at, *, floor):
    """The full view (or, on OTCv8, one floor of it) around the player's position."""
    f = _fmt("map_description")
    if pos + (6 if floor else 5) > len(data):
        return STOP
    xyz = (_u16(data, pos + f.get("position_x", 0)), _u16(data, pos + f.get("position_y", 2)),
           data[pos + f.get("position_z", 4)])
    if not _on_map(*xyz) or (floor and data[pos + 5] != xyz[2]):
        return STOP  # another floor's tiles do not move the player
    _set_packet_position(gs, xyz)
    _forget_packet_creatures(gs)
    log.info("MAP_DESCRIPTION: pos=%s, creatures cleared", xyz)
    return STOP


def _map_strip(data, pos, gs, received_at, *, step):
    if not gs.packet_position_trusted:
        return STOP  # wait for an absolute position after a missed frame
    x, y, z = gs.packet_position if gs.packet_position != (0, 0, 0) else gs.position
    if not _on_map(x, y, z):
        return STOP
    x, y = x + step[0], y + step[1]
    if _on_map(x, y):
        _set_packet_position(gs, (x, y, z), absolute=False)
    return STOP


def _skip(name, default):
    def handler(data, pos, gs, received_at):
        size = _fmt(name).get("size", default)
        return pos + size if pos + size <= len(data) else STOP
    return handler


def _animated_text(data, pos, gs, received_at):
    f = _fmt("animated_text")
    if pos + f.get("header", 8) > len(data):
        return STOP
    end = pos + f.get("text", 8) + _u16(data, pos + f.get("string_length", 6))
    return end if end <= len(data) else STOP


def _creature_speed(data, pos, gs, received_at):
    f = _fmt("creature_speed")
    size = f.get("size", 6)
    if pos + size > len(data):
        return STOP
    if _u32(data, pos + f.get("creature_id", 0)) == gs.player_id:
        gs.speed = _u16(data, pos + f.get("speed", 4))
    return pos + size


def _creature_party(data, pos, gs, received_at):
    size = _fmt("creature_party").get("size", 5)
    if size < 5 or pos + size > len(data):
        return STOP
    cid, shield = struct.unpack_from("<IB", data, pos)
    if cid and cid == (gs.client_player_id or gs.player_id):
        gs.player_party_shield = shield
    return pos + size


def _cooldown(data, pos, gs, received_at, *, single):
    """u8 spell or group id + u32 milliseconds (standard OTClient)."""
    if pos + 5 > len(data) or _u32(data, pos + 1) > 3_600_000:
        return STOP
    if single and data[pos] == HASTE_SPELL:
        gs.haste_cast_at = time.monotonic()
    elif single and data[pos] == BARRIER_SPELL:
        gs.barrier_cast_at = time.monotonic()
    return pos + 5


def _icons(data, pos, gs, received_at):
    size = _fmt("player_icons").get("size", 2)
    if pos + size > len(data):
        return STOP
    before, gs.player_icons = gs.player_icons, _u16(data, pos)
    if gs.player_icons != before:
        log.info("PLAYER_ICONS changed: 0x%04X -> 0x%04X", before, gs.player_icons)
    return pos + size


def _step_back(xyz, step):
    return (xyz[0] - step[0], xyz[1] - step[1], xyz[2])


def _cancel_walk(data, pos, gs, received_at):
    size = _fmt("player_cancel_walk").get("size", 1)
    if pos + size > len(data):
        return STOP
    heading = data[pos]
    if heading > Direction.NORTHWEST:
        return STOP  # bytes inside another message must not undo a step
    gs.cancel_walk_time = when = time.time()
    step, gs._last_walk_delta = gs._last_walk_delta, (0, 0)
    if step != (0, 0):  # undo the step the client already showed
        gs.position = _step_back(gs.position, step)
        if gs.packet_position[0] > 100:
            gs.packet_position = _step_back(gs.packet_position, step)
    if received_at is not None:
        gs.last_walk_cancel = (received_at, tuple(gs.position))
    gs.server_events.append((when, "cancel_walk", {"direction": heading, "pos": list(gs.position)}))
    if not getattr(gs, "_packet_snapshot_detached", False):
        log.info("CANCEL_WALK direction=%s observed_pos=%s", heading, gs.position)
    return pos + size


def _world_light(data, pos, gs, received_at):
    f = _fmt("world_light")
    size = f.get("size", 2)
    if pos + size > len(data):
        return STOP
    gs.world_light_level, gs.world_light_color = data[pos + f.get("level", 0)], data[pos + f.get("color", 1)]
    return pos + size


def _nothing(data, pos, gs, received_at):
    return pos


# DBVictory never sends FLOOR_CHANGE_UP/DOWN (0xBE/0xBF): floor changes arrive as a
# creature move plus map data, and scan_packet reports them from the position change.
_HANDLERS = {
    Op.PLAYER_STATS: _stats,
    Op.CREATURE_HEALTH: _creature_health,
    Op.CREATURE_MOVE: _creature_move,
    Op.CHANNELS: lambda d, p, g, r: _channels(d, p, g, r, listing=True),
    Op.OPEN_CHANNEL: lambda d, p, g, r: _channels(d, p, g, r, listing=False),
    Op.CLOSE_CHANNEL: _close_channel,
    Op.TALK: _talk,
    Op.TEXT_MESSAGE: _text_message,
    Op.LOGIN_OR_PENDING: _login,
    Op.MAP_DESCRIPTION: lambda d, p, g, r: _map_description(d, p, g, r, floor=False),
    **{op: (lambda step: lambda d, p, g, r: _map_strip(d, p, g, r, step=step))(step)
       for op, step in _STRIP_STEP.items()},
    Op.MAGIC_EFFECT: _skip("magic_effect", 6),
    Op.SHOOT_EFFECT: _skip("shoot_effect", 11),
    Op.ANIMATED_TEXT: _animated_text,
    Op.TILE_REMOVE_THING: _skip("tile_remove_thing", 6),
    Op.CLOSE_CONTAINER: _skip("close_container", 1),
    Op.REMOVE_FROM_CONTAINER: _skip("remove_from_container", 2),
    Op.CREATURE_LIGHT: _skip("creature_light", 6),
    Op.CREATURE_SPEED: _creature_speed,
    Op.CREATURE_SKULL: _skip("creature_skull", 5),
    Op.CREATURE_PARTY: _creature_party,
    Op.PLAYER_SKILLS: _skip("player_skills", 14),
    COOLDOWN: lambda d, p, g, r: _cooldown(d, p, g, r, single=True),
    GROUP_COOLDOWN: lambda d, p, g, r: _cooldown(d, p, g, r, single=False),
    Op.PLAYER_ICONS: _icons,
    Op.PLAYER_CANCEL_WALK: _cancel_walk,
    Op.PING: _nothing,
    Op.PONG: _nothing,
    Op.PLAYER_CANCEL_ATTACK: _nothing,
    WORLD_LIGHT: _world_light,
    DBV_CUSTOM_0XCB: _skip("custom_0xcb", 5),
}


def _parse_at(opcode: int, data: bytes, pos: int, gs: GameState, *, received_at=None) -> int:
    """Read one message whose opcode came just before ``pos``; returns where the next starts or STOP."""
    floor = _fmt("floor_description").get("opcode")
    if opcode == floor and opcode is not None and opcode not in (Op.MAP_DESCRIPTION,):
        return _map_description(data, pos, gs, received_at, floor=True)
    handler = _HANDLERS.get(opcode)
    return handler(data, pos, gs, received_at) if handler else STOP


# ── fallback searches after the ordered read stopped ────────────────

def _search_for_floor_position(data: bytes, start: int, gs: GameState, anchor) -> None:
    """Recover a bundled floor refresh after a verified local map update.

    DBV sends several 0x4B floor descriptions with the same central position.
    Require at least three adjacent, ordered floors, including that position's
    floor. An isolated plausible byte pattern is not a position update.
    """
    marker = _fmt("floor_description").get("opcode")
    if marker is None:
        return
    groups = {}
    i = data.find(bytes([marker]), start)
    while 0 <= i and i + 7 <= len(data):
        xyz, floor = struct.unpack_from("<HHB", data, i + 1), data[i + 6]
        if (_on_map(*xyz) and floor < 16 and abs(xyz[0] - anchor[0]) <= 2
                and abs(xyz[1] - anchor[1]) <= 2 and abs(xyz[2] - anchor[2]) <= 1):
            groups.setdefault(xyz, []).append((i, floor))
        i = data.find(bytes([marker]), i + 1)
    candidates = []
    for xyz, headers in groups.items():
        floors = [f for _, f in headers]
        steps = {b - a for a, b in zip(floors, floors[1:])}
        if len(floors) >= 3 and xyz[2] in floors and steps in ({1}, {-1}):
            candidates.append((headers[-1][0], xyz))
    if candidates:
        _set_packet_position(gs, max(candidates)[1])


def _search_for_tile_updates(data: bytes, start: int, gs: GameState) -> None:
    """Note every TILE_TRANSFORM_THING (doors opening and closing) anywhere in the frame.

    Add/remove thing (0x6A/0x6C) are too common in map refreshes to be useful.
    """
    now = time.time()
    while gs.tile_updates and gs.tile_updates[0][0] < now - 5.0:
        del gs.tile_updates[0]  # older changes no longer explain a blocked step
    f = _fmt("tile_transform_thing")
    ox, oy, oz = f.get("x", 1), f.get("y", 3), f.get("z", 5)
    i = data.find(bytes([Op.TILE_TRANSFORM_THING]), start)
    while 0 <= i < len(data) - 5:
        try:
            x, y, z = _u16(data, i + ox), _u16(data, i + oy), data[i + oz]
        except (struct.error, IndexError):
            x = y = z = -1
        if 100 <= x <= 65000 and 100 <= y <= 65000 and 0 <= z <= 15:
            gs.tile_updates.append((now, x, y, z))
            gs.server_events.append((now, "tile_transform_item", {"x": x, "y": y, "z": z}))
        i = data.find(bytes([Op.TILE_TRANSFORM_THING]), i + 1)


def _plausible_stats(data, at, f):
    try:
        hp, max_hp = _u32(data, at + f.get("hp", 0)), _u32(data, at + f.get("max_hp", 4))
        level = _u16(data, at + f.get("level", 20))
        if not (0 < max_hp <= MAX_VALID_HP and hp <= max_hp and 0 < level <= MAX_VALID_LEVEL):
            return False
        mana, max_mana = _u32(data, at + f.get("mana", 23)), _u32(data, at + f.get("max_mana", 27))
        capacity = _u32(data, at + f.get("capacity", 8))
    except (struct.error, IndexError):
        return False
    # DBVictory can report bonus Ki above the nominal maximum (e.g. 119/100).
    return (mana <= MAX_VALID_MANA and max_mana <= MAX_VALID_MANA
            and (mana <= max_mana or f.get("allow_mana_over_max", False))
            and capacity <= MAX_VALID_CAPACITY * f.get("capacity_scale", 1))


def _search_for_stats(data: bytes, start: int, gs: GameState) -> None:
    """Apply every plausible PLAYER_STATS in the rest of the frame, in order (the last wins)."""
    f = _fmt("player_stats")
    size = f.get("size", 36)
    i = start
    while i < len(data) - size:
        if data[i] == Op.PLAYER_STATS and _plausible_stats(data, i + 1, f):
            _stats(data, i + 1, gs, None)
            i += 1 + size
        else:
            i += 1


def _search_for_icons(data: bytes, start: int, gs: GameState) -> None:
    """The first plausible PLAYER_ICONS (u16 below 0x8000) in the rest of the frame."""
    size = _fmt("player_icons").get("size", 2)
    for i in range(start, len(data) - size):
        if data[i] == Op.PLAYER_ICONS:
            icons = _u16(data, i + 1)
            if icons < 0x8000:
                if icons != gs.player_icons:
                    log.info("ICONS found via fallback at offset %d: 0x%04X (was 0x%04X)", i, icons, gs.player_icons)
                gs.player_icons = icons
                return


# ── entry points ────────────────────────────────────────────────────

def scan_packet(data: bytes, gs: GameState, *, received_at: float | None = None) -> bool:
    """Apply one decrypted server frame. Returns whether every message in it was read."""
    # perf_counter, like walk dispatch: on Windows monotonic() cannot order a send
    # and its reply within one 15.6 ms tick.
    if received_at is None:
        received_at = time.perf_counter()
    gs._server_packet_seq = getattr(gs, "_server_packet_seq", 0) + 1
    before = gs.packet_position if gs.packet_position_trusted else (0, 0, 0)
    gs._map_update_seen = False
    pos, saw_icons, anchor, complete = 0, False, None, True
    while pos < len(data):
        opcode = data[pos]
        try:
            following = _parse_at(opcode, data, pos + 1, gs, received_at=received_at)
        except Exception:
            complete = False
            pos += 1
            break
        pos += 1
        if following < 0:
            complete = False
            anchor = gs.position if gs._map_update_seen else None
            break
        saw_icons |= opcode == Op.PLAYER_ICONS
        pos = following
    if anchor is not None:
        # A stair step can bundle a map strip with a full floor refresh.
        _search_for_floor_position(data, pos, gs, anchor)
    after = gs.packet_position
    if (gs._map_update_seen and all(0 < c < 65535 for c in before[:2]) and abs(after[2] - before[2]) == 1
            and max(abs(after[0] - before[0]), abs(after[1] - before[1])) <= 2):
        kind = "floor_change_up" if after[2] < before[2] else "floor_change_down"
        gs.server_events.append((time.time(), kind, {"pos": list(after), "z": after[2]}))
    _search_for_tile_updates(data, 0, gs)  # doors can sit anywhere, even after map data
    if pos < len(data):
        _search_for_stats(data, pos, gs)
        if not saw_icons:
            _search_for_icons(data, pos, gs)
    _prune_creatures(gs, time.time())
    return complete


def _prune_creatures(gs, now):
    """Drop packet-seen creatures not updated for MAX_CREATURE_AGE (at most once a second)."""
    if now < gs._last_prune_time + PRUNE_INTERVAL:
        return
    gs._last_prune_time, oldest = now, now - MAX_CREATURE_AGE
    gs.creatures = {cid: c for cid, c in gs.creatures.items() if c.get("source") == "dll" or c.get("t", 0) >= oldest}


def _parse(opcode: int, reader, gs: GameState) -> None:
    """Single-message parser for callers that hold a PacketReader."""
    if opcode == Op.LOGIN_OR_PENDING:
        pid = reader.read_u32()
        if not gs.client_player_id:
            gs.player_id = pid
        log.info("LOGIN: player_id=%s", gs.player_id)
    elif opcode == Op.CREATURE_HEALTH:
        cid, health = reader.read_u32(), reader.read_u8()
        if cid in gs.creatures:
            gs.creatures[cid].update(health=health, t=time.time())
    elif opcode == Op.PLAYER_STATS:
        _apply_reader_stats(gs, reader.read_bytes(34))
    elif opcode == Op.TEXT_MESSAGE:
        kind, text = reader.read_u8(), reader.read_string()
        _server_text(gs, kind, text, time.time(), framed=False)
        log.info("TEXT_MESSAGE(type=%s): %s", kind, text)


def _apply_reader_stats(gs, body):
    """The fixed u32/u64 layout of the single-message parser."""
    (gs.hp, gs.max_hp, gs.capacity, gs.experience, gs.level, _, gs.mana, gs.max_mana,
     gs.magic_level, _, gs.soul) = struct.unpack_from("<IIIQHBIIBBB", body)
    log.info("Stats: HP=%s/%s MP=%s/%s Lv=%s XP=%s", gs.hp, gs.max_hp, gs.mana, gs.max_mana, gs.level, gs.experience)
    _dump_stats_debug(gs, None)


def parse_server_packet(opcode: int, reader, gs: GameState) -> None:
    """Parse one message from a reader; errors are only logged."""
    try:
        _parse(opcode, reader, gs)
    except Exception as exc:
        log.debug("parse_server_packet error opcode 0x%02X: %s", opcode, exc)
