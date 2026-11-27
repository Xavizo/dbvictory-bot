"""Record a walked route and turn it into an actions map the cavebot can replay.

While recording, the player's own movement and item uses are read from the
client packets passing through the proxy, and floor changes, cancelled steps
and door changes from the server events the game state collects. The result
is saved as JSON under ``recordings/``.

``build_actions_map`` reduces those raw waypoints to a few navigation nodes:
``walk_to`` (let the server path-find to a tile), ``use_item`` (stairs, doors,
ladders) and ``use_item_ex`` (rope, shovel).
"""
import json
import logging
import struct
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from dbv import paths
from dbv.brain.hunts.farming_telemetry import experience_for_level
from dbv.client_bridge.protocol import WALK_OPCODES as _WALK_BY_DIRECTION, ClientOpcode, PacketReader

log = logging.getLogger(name="cavebot")

RECORDINGS_DIR = paths.RECORDINGS_DIR

STEP = {"north": (0, -1), "east": (1, 0), "south": (0, 1), "west": (-1, 0),
        "northeast": (1, -1), "southeast": (1, 1), "southwest": (-1, 1), "northwest": (-1, -1)}
DIR_OFFSET = STEP
WALK_OPCODE_TO_DIR = {opcode: direction.name.lower() for direction, opcode in _WALK_BY_DIRECTION.items()}
WALK_OPCODES = frozenset(WALK_OPCODE_TO_DIR)
# Path bytes of auto-walk packets count 1..8 anticlockwise from east, unlike Direction.
AUTOWALK_DIR_OFFSET = dict(enumerate(
    [STEP[name] for name in ("east", "northeast", "north", "northwest",
                             "west", "southwest", "south", "southeast")], start=1))
_NAME_OF_STEP = {offset: name for name, offset in STEP.items()}

# Item ids seen in DBVClient_v5; other servers number items differently.
ITEM_LABELS = {4560: "Stairs", 4561: "Ground"}
GROUND_ITEM_IDS = frozenset({4561})
DEFAULT_GROUND_ID = 4449
NOT_ON_MAP = 0xFFFF       # x of a use from a container or hotkey
MAX_AUTOWALK_STEPS = 50
MAX_NEW_WALK_STEPS = 4096


def _auto_label(item_id):
    """The known name of an item (a door, a ladder...) or "Use item <id>"."""
    return ITEM_LABELS[item_id] if item_id in ITEM_LABELS else _generic_label(item_id)


def _generic_label(item_id):
    return f"Use item {item_id}"


def _on_map(x, y, z=0):
    return 0 < x < 65535 and 0 < y < 65535 and z < 16


# ── reading client packets ──────────────────────────────────────────

def _read_new_walk(reader: PacketReader, elapsed: float) -> list[dict]:
    """Steps of an OTCv8 new-walk packet, or [] unless the whole path is valid.

    Layout: u32 walk id, u32 prediction id, origin, u8 flags, u16 count, then
    one path byte per step. The origin already includes client prediction, so
    it is used instead of the (possibly lagging) server position.
    """
    try:
        reader.read_u32(), reader.read_u32()
        x, y, z = reader.read_position()
        reader.read_u8()
        count = reader.read_u16()
        if not _on_map(x, y, z) or not 1 <= count <= min(MAX_NEW_WALK_STEPS, reader.remaining):
            return []
        steps = [AUTOWALK_DIR_OFFSET.get(reader.read_u8()) for _ in range(count)]
    except (IndexError, ValueError, struct.error):
        return []
    if None in steps:
        return []
    out = []
    for dx, dy in steps:
        start, x, y = [x, y, z], x + dx, y + dy
        if not _on_map(x, y):
            return []
        out.append({"type": "walk", "direction": _NAME_OF_STEP[(dx, dy)], "pos": [x, y, z],
                    "player_pos": start, "t": elapsed, "source": "new_walk"})
    return out


def _read_autowalk(reader, here):
    """Destination of a click-to-walk path; None for an empty, long or broken one."""
    try:
        count = reader.read_u8()  # steps in the path
        if not 0 < count <= MAX_AUTOWALK_STEPS:
            return None, 0
        x, y, z = here
        for _ in range(count):
            dx, dy = AUTOWALK_DIR_OFFSET.get(reader.read_u8(), (0, 0))  # unknown bytes do not move
            x, y = x + dx, y + dy
    except Exception:
        return None, 0
    return [x, y, z], count


def _read_use(reader):
    """USE_ITEM: tile, item id, stack position, optional container index."""
    try:
        tile, item_id, stack = reader.read_position(), reader.read_u16(), reader.read_u8()
        index = reader.read_u8() if reader.remaining else 0
    except Exception:
        return None
    return {"type": "use_item", "x": tile[0], "y": tile[1], "z": tile[2], "item_id": item_id,
            "stack_pos": stack, "index": index, "label": _auto_label(item_id)}


def _read_use_with(reader):
    """USE_ITEM_EX: source tile, item, stack, target tile, then (new clients) target item and stack."""
    try:
        source, item_id, stack = reader.read_position(), reader.read_u16(), reader.read_u8()
        target = reader.read_position()
        target_item = reader.read_u16() if reader.remaining >= 3 else None
        target_stack = reader.read_u8() if reader.remaining else 0
    except Exception:
        return None
    return {"type": "use_item_ex", "from_x": source[0], "from_y": source[1], "from_z": source[2],
            "item_id": item_id, "stack_pos": stack, "to_x": target[0], "to_y": target[1],
            "to_z": target[2], "to_stack_pos": target_stack, "target_item_id": target_item,
            "label": _auto_label(item_id)}


def client_waypoints(opcode, reader: PacketReader, here: list, elapsed: float) -> list[dict]:
    """Waypoints for one client packet sent while the player stands at ``here``."""
    if opcode == ClientOpcode.NEW_WALK:
        steps = _read_new_walk(reader, elapsed)
        if steps:
            log.info("[REC] new_walk %d steps %s -> %s", len(steps), steps[0]["player_pos"], steps[-1]["pos"])
        else:
            log.debug("[REC] ignored invalid or empty new_walk")
        return steps
    if opcode in WALK_OPCODE_TO_DIR:
        name = WALK_OPCODE_TO_DIR[opcode]
        dx, dy = STEP[name]
        goal = [here[0] + dx, here[1] + dy, here[2]]
        log.info("[REC] walk %s %s -> %s", name, here[:2], goal[:2])
        return [{"type": "walk", "direction": name, "pos": goal, "player_pos": here, "t": elapsed}]
    if opcode == ClientOpcode.AUTO_WALK:
        goal, count = _read_autowalk(reader, here)
        if goal is None:
            return []
        log.info("[REC] autowalk %d steps %s -> %s", count, here[:2], goal[:2])
        return [{"type": "walk", "direction": "autowalk", "pos": goal, "player_pos": here, "t": elapsed}]
    readers = {ClientOpcode.USE_ITEM: _read_use, ClientOpcode.USE_ITEM_EX: _read_use_with}
    if opcode not in readers:
        return []
    use = readers[opcode](reader)
    if use is None:
        return []
    use.update(pos=here, player_pos=here, t=elapsed)
    log.info("[REC] %s %s", use["type"], use["item_id"])
    return [use]


def _server_waypoint(event, data, here, elapsed):
    if event in ("floor_change_up", "floor_change_down"):
        where = list(data.get("pos", here))
        return {"type": "floor_change", "direction": event.rsplit("_", 1)[1], "pos": where,
                "z": data.get("z", where[2]), "t": elapsed}
    if event == "cancel_walk":
        return {"type": "cancel_walk", "direction": data.get("direction", 0),
                "pos": list(data.get("pos", here)), "t": elapsed}
    if event == "tile_transform_item":
        return {"type": "tile_transform_item", "x": data["x"], "y": data["y"], "z": data["z"], "t": elapsed}
    return None


def _record_server_events(gs, waypoints: list, started_at: float) -> None:
    """Move queued server events into the recording, dropping any from before it began."""
    while True:
        try:
            at, event, data = gs.server_events.popleft()
        except IndexError:
            return
        if at < started_at:
            continue
        waypoint = _server_waypoint(event, data, gs.position, round(at - started_at, 1))
        if waypoint is not None:
            waypoints.append(waypoint)
            if waypoint["type"] != "tile_transform_item":
                log.info("[REC] %s %s pos=%s", waypoint["type"], waypoint["direction"], waypoint["pos"])


# ── recording ───────────────────────────────────────────────────────



def _sample_recording_position(state):
    """Read the existing atomic sampler; never issue a command or memory read."""
    from dbv.brain.navigation.transition_learning import position
    gs=state.game_state
    now=time.monotonic()
    if not getattr(state,"connected",True):return
    if getattr(gs,"client_state_required",False):
        snapshot=getattr(gs,"client_snapshot",None)
        if not snapshot or not 0<=now-snapshot["monotonic"]<=0.5:return
        pos=snapshot.get("position")
    else:pos=gs.position
    if not position(pos):return
    trace=state.recording_observed_path
    previous_sample=getattr(state,"_recording_sample_time",now)
    state._recording_sample_time=now
    elapsed = time.time() - state.recording_start_time
    if getattr(gs,"client_state_required",False):
        elapsed -= now - snapshot["monotonic"]
    elapsed = max(0, round(elapsed, 3))
    xp_gain = 0
    new_segment = now-previous_sample > 1
    if getattr(state, "recording_mode", "manual") == "adaptive":
        level, xp = gs.level, gs.experience
        if not (1 <= level <= 65535 and experience_for_level(level) <= xp < experience_for_level(level+1)):
            xp = None
        previous = getattr(state, "_recording_xp_sample", None)
        new_segment = new_segment or bool(previous and previous[0] is not gs)
        if (previous and trace and previous[1] is not None and xp is not None and not new_segment and xp > previous[1]
                and trace[-1]["pos"][2] == pos[2]
                and max(abs(trace[-1]["pos"][i]-pos[i]) for i in (0, 1)) <= 3):
            xp_gain = xp - previous[1]
        state._recording_xp_sample = (gs, xp)
    same_position = bool(trace and trace[-1]["pos"]==list(pos) and not new_segment)
    encounters = getattr(state, "_recording_encounters", None)
    if encounters is not None and (same_position or len(trace) < 20000):
        encounters.sample(gs, state.passive_map_monitor, len(trace)-int(same_position), time.time(),
                          xp_gain, new_segment or bool(trace and trace[-1]["pos"][2] != pos[2]))
    if same_position:
        trace[-1]["last_t"] = max(trace[-1].get("last_t",trace[-1]["t"]),elapsed)
        if xp_gain:
            trace[-1]["xp_gain"] = trace[-1].get("xp_gain", 0) + xp_gain
        return
    if len(trace)>=20000:
        state.recording_trace_limited=True
        return
    point={"pos":list(pos),"t":elapsed}
    if xp_gain:
        point["xp_gain"] = xp_gain
    if trace and new_segment:point["break_before"]=True
    trace.append(point)


def _refuse_start(state, name, mode, atlas):
    task = getattr(state, "_action_tasks", {}).get("adaptive_cavebot")
    if task and not task.done():
        return "Zatrzymaj V2 przed nagrywaniem własnej drogi."
    if state.recording_active:  # one recording at a time
        return "Already recording '%s'. Stop it first." % (state.recording_name,)
    if not name or any(not (c.isalnum() or c in "-_") for c in name):
        return "Invalid name. " + "Use alphanumeric, hyphens, underscores only."
    if mode not in ("manual", "adaptive"):
        return "Nieznany tryb nagrywania."
    if mode == "adaptive" and not getattr(state, "connected", False):
        return "Nagrywanie adaptacyjne wymaga zalogowanej postaci."
    if mode == "adaptive" and not getattr(atlas, "scope", None):
        return "Poczekaj na pierwszy odczyt atlasu."
    return None


def start_recording(state, name: str, *, mode: str = "manual") -> str | None:
    """Start recording a route. Returns an error message, or None once recording."""
    atlas = getattr(getattr(state, "passive_map_monitor", None), "atlas", None)
    refusal = _refuse_start(state, name, mode, atlas)
    if refusal:
        return refusal
    adaptive = mode == "adaptive"
    start = state.game_state.position
    waypoints = []
    state.recording_active, state.recording_name, state.recording_mode = True, name, mode
    state.recording_hunt_progress = {"stage": 0} if adaptive else None
    state._recording_xp_sample = None
    state._recording_encounters = None
    if adaptive:
        from dbv.brain.hunts.hunt_encounters import HuntEncounters
        state._recording_encounters = HuntEncounters()
    state.recording_waypoints, state.recording_observed_path = waypoints, []
    state.recording_trace_limited = False
    state._recording_sample_time = time.monotonic()
    state.recording_start_pos = start
    state.recording_start_time = started_at = time.time()
    state._recording_atlas_scope = getattr(atlas, "scope", None)
    _sample_recording_position(state)

    relay = state.game_proxy
    if relay is None:
        _abandon(state)
        return "Game proxy" + " not available."

    def current():
        return state.recording_active and state.recording_start_time == started_at

    def on_client_packet(opcode, reader: PacketReader):
        if current():  # a relay may still hold the callback of an older recording
            elapsed = round(time.time() - started_at, 3)
            waypoints.extend(client_waypoints(opcode, reader, list(state.game_state.position), elapsed))

    stopped = threading.Event()

    def follow_server():
        """Every 50 ms: copy server events into the recording and sample the position."""
        while not stopped.wait(0.05) and current():
            _record_server_events(state.game_state, waypoints, started_at)
            _sample_recording_position(state)

    state._recording_callback = on_client_packet
    relay.register_client_packet_callback(on_client_packet)
    follower = threading.Thread(target=follow_server, daemon=True)
    state._recording_event_stop, state._recording_event_thread = stopped, follower
    follower.start()
    if adaptive:
        from dbv.brain.cavebot.adaptive_recording import watch_hunt_evidence
        # Each worker owns its recording and progress. A late check from an
        # older run cannot publish into the next run, even with the same name.
        threading.Thread(target=watch_hunt_evidence, name="hunt-readiness", daemon=True,
                         args=(_recording_data(state), state.passive_map_monitor,
                               stopped, state.recording_hunt_progress)).start()
    log.info("Recording started: '%s' at %s", name, start)
    return None


def _recording_data(state):
    gs = state.game_state
    data = dict(
        name=state.recording_name,
        created_at=datetime.now(tz=timezone.utc).isoformat(),
        version=2,
        started_at=state.recording_start_time,
        atlas_scope=getattr(state, "_recording_atlas_scope", None),
        action_time_precision_ms=1,
        start_position=[*state.recording_start_pos],
        waypoints=state.recording_waypoints,
        observed_path=state.recording_observed_path,
    )
    encounters = getattr(state, "_recording_encounters", None)
    if encounters:
        data["encounters"] = encounters.events
    data["trace_limited"] = state.recording_trace_limited
    data["position_source"] = "client" if getattr(gs, "client_state_required", False) else "server"
    return data


def _abandon(state):
    """Mark the recording as not running (its data stays until the reset)."""
    state.recording_active = False  # its threads stop at their next check


def _reset_recording(state):
    _abandon(state)
    state.recording_mode = "manual"
    state._recording_encounters = state.recording_hunt_progress = state._recording_xp_sample = None
    state.recording_name = ""
    state.recording_waypoints, state.recording_observed_path = [], []
    state.recording_start_pos, state.recording_start_time = (0, 0, 0), 0


def stop_recording(state, *, discard=False):
    """Stop recording; save the result unless ``discard``. Returns the recording, or None."""
    return _finish_recording(state, discard) if state.recording_active else None


def _finish_recording(state, discard):
    _abandon(state)
    stopped, state._recording_event_stop = getattr(state, "_recording_event_stop", None), None
    if stopped:
        stopped.set()
    follower, state._recording_event_thread = getattr(state, "_recording_event_thread", None), None
    if follower:
        follower.join(timeout=1)
    # Events and a position that arrived after the last poll still belong to this route.
    _record_server_events(state.game_state, state.recording_waypoints, state.recording_start_time)
    _sample_recording_position(state)
    callback, state._recording_callback = getattr(state, "_recording_callback", None), None
    if callback and state.game_proxy:
        state.game_proxy.unregister_client_packet_callback(callback)

    recording = _recording_data(state)
    if getattr(state, "recording_mode", "manual") == "adaptive":
        recording["recording_mode"] = "adaptive"
    name, count = state.recording_name, len(state.recording_waypoints)
    _reset_recording(state)
    if not discard:
        save_recording(recording)  # recordings/<name>.json
        log.info("Recording '%s' saved (%d waypoints)", name, count)
        return recording  # the saved data, for the caller's message
    log.info("Recording '%s' discarded (%d waypoints)", name, count)
    return None


# ── files ───────────────────────────────────────────────────────────

def _safe_recording_path(name):
    """recordings/<name>.json, or None when the name would leave that folder."""
    root = RECORDINGS_DIR.resolve()
    candidate = (RECORDINGS_DIR / f"{name}.json").resolve()
    if candidate.is_relative_to(root):
        return candidate
    log.warning("Path traversal attempt blocked: '%s'", name)
    return None


def save_recording(recording):
    """Write a recording to recordings/<its name>.json (the folder is made when missing)."""
    _recordings_folder()
    target = _safe_recording_path(recording["name"])
    if target is not None:
        target.write_text(json.dumps(recording, indent=2), encoding="utf-8")


def _read_json(target: Path):
    return json.loads(target.read_text(encoding="utf-8"))


def load_recording(name):
    """A saved recording, or None when it is missing or unreadable."""
    target = _safe_recording_path(name)
    if target is None or not target.exists():
        return None
    try:
        return _read_json(target)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Failed to load recording '%s': %s", name, exc)
        return None


def _summary(target: Path, data: dict) -> dict:
    return {"name": data.get("name", target.stem), "count": len(data.get("waypoints", [])),
            "mode": data.get("recording_mode", "manual"), "created_at": data.get("created_at", ""),
            "last_stats": data.get("last_stats")}


def _recordings_folder():
    folder = RECORDINGS_DIR
    folder.mkdir(exist_ok=True)
    return folder


def list_recordings():
    """Name, size, mode, date and last run stats of every readable recording, by file name."""
    summaries = []
    for target in sorted(_recordings_folder().glob("*.json")):
        try:
            summaries.append(_summary(target, _read_json(target)))
        except (OSError, json.JSONDecodeError):
            pass  # unreadable files are not listed
    return summaries


def delete_recording(name):
    """Delete recordings/<name>.json. Returns whether there was one."""
    target = _safe_recording_path(name)
    if target is None or not target.exists():
        return False
    target.unlink()
    log.info("Deleted recording '%s'", name)
    return True


def remove_waypoints(name, indices):
    """Drop the waypoints at ``indices`` from a saved recording and save it again."""
    recording = load_recording(name)
    if recording is None:
        return None
    drop = set(indices)
    kept = [wp for i, wp in enumerate(recording.get("waypoints", [])) if i not in drop]
    recording.update(waypoints=kept)
    save_recording(recording)  # the same file, with fewer waypoints
    log.info("Removed %d waypoint(s) from '%s', %d remaining", len(indices), name, len(kept))
    return recording


# ── actions map ─────────────────────────────────────────────────────

_EFFECT_LOOKAHEAD = 15    # waypoints searched for the effect of a use
_COMMANDS = ("walk", "use_item", "use_item_ex")


def _manhattan(a, b):
    """Distance in steps along x and y; z is ignored."""
    return sum(abs(a[k] - b[k]) for k in (0, 1))


def _xyz(point):
    return tuple(point[:3])


def _has_effect_after(waypoints, start, player_z, target_x, target_y, target_z):
    """Whether the use at ``start`` changed something: a floor (stairs, ladder) or the used tile (door).

    The next movement or use command ends the search: what follows belongs to
    it. Position samples do not, since the server may walk toward a used item.
    """
    started = waypoints[start].get("t", 0)
    for later in waypoints[start + 1:start + _EFFECT_LOOKAHEAD]:
        kind = later.get("type", "")
        if kind in _COMMANDS:
            return False
        if later.get("t", 0) < started:
            continue  # history from before the recording
        if kind == "floor_change":
            return later.get("z", later.get("pos", [0, 0, player_z])[2]) != player_z
        if kind == "tile_transform_item" and (later["x"], later["y"], later["z"]) == (target_x, target_y, target_z):
            return True
    return False


def _is_map_click_walk(wp):
    """Whether a use_item is a click on the map to walk there.

    A click on a far tile (or on a known ground item) walks, whatever floor the
    tile shows. Hotkey and container uses (x 0 or 0xFFFF) and uses with a
    container index (doors) are interactions instead.
    """
    if wp.get("type") != "use_item" or wp["x"] in (0, NOT_ON_MAP) or wp.get("index", 0) > 0:
        return False
    return wp.get("item_id") in GROUND_ITEM_IDS or _manhattan((wp["x"], wp["y"], wp["z"]), wp["pos"]) > 1


def _simplify_path(points, max_gap=3):
    """Thin a path of (x, y, z, item_id, stack_pos) so kept points are about ``max_gap`` apart.

    The last point is always kept, and so are both points around every floor
    change, so stairs and ramps are never cut out.
    """
    if len(points) < 2:
        return points  # nothing to thin
    kept = [points[0]]
    last = len(points) - 1
    for i in range(1, last + 1):
        at_floor_change = points[i][2] != points[i - 1][2] or (i < last and points[i + 1][2] != points[i][2])
        if i == last or at_floor_change or _manhattan(points[i], kept[-1]) >= max_gap:
            kept.append(points[i])
    return kept


def _add_point(path, point):
    if not path or _xyz(path[-1]) != _xyz(point):
        path.append(point)


def _walk_nodes(path, first, last):
    return [{"type": "walk_to", "target": list(_xyz(p)), "item_id": p[3], "stack_pos": p[4],
             "wp_range": [first, last]} for p in _simplify_path(path)]


def _use_node(wp, index):
    return {"type": "use_item", "target": [wp["x"], wp["y"], wp["z"]], "x": wp["x"], "y": wp["y"],
            "z": wp["z"], "item_id": wp["item_id"], "stack_pos": wp.get("stack_pos", 0),
            "index": wp.get("index", 0), "label": wp.get("label", f"Use item {wp['item_id']}"),
            "player_pos": wp["pos"], "wp_range": [index, index]}


def _use_with_node(wp, index):
    return {"type": "use_item_ex", "target": [wp["to_x"], wp["to_y"], wp["to_z"]],
            "from_x": wp["from_x"], "from_y": wp["from_y"], "from_z": wp["from_z"],
            "item_id": wp["item_id"], "stack_pos": wp.get("stack_pos", 0),
            "to_x": wp["to_x"], "to_y": wp["to_y"], "to_z": wp["to_z"],
            "to_stack_pos": wp["to_stack_pos"] if "to_stack_pos" in wp else 0,
            "label": wp["label"] if "label" in wp else _generic_label(wp["item_id"]),
            "player_pos": wp["pos"], "wp_range": [index, index]}


class _MapBuilder:
    def __init__(self, waypoints):
        self.waypoints = waypoints
        self.nodes = []
        self.ground_id = None   # item id of the first clicked tile, reused for keyboard walks

    def build(self):
        steps, i = self.waypoints, 0
        while i < len(steps):
            wp = steps[i]
            kind = wp.get("type", "")
            if kind == "walk":
                i = self.keyboard_walks(i)
                continue
            if kind == "use_item" and _is_map_click_walk(wp):
                i = self.map_clicks(i)
                continue
            if kind == "floor_change":
                self.floor_change(wp)
            elif kind == "use_item" and wp.get("x") != NOT_ON_MAP:
                self.nodes.append(_use_node(wp, i))
            elif kind == "use_item_ex":
                self.nodes.append(_use_with_node(wp, i))
            i += 1  # positions, cancelled steps and door changes add no node
        return self.nodes

    def floor_change(self, wp):
        # Stairs are reached exactly, even when the recording ends right after them.
        floor = wp.get("z", wp.get("pos", [0, 0, -1])[2])
        if self.nodes and self.nodes[-1]["type"] == "walk_to" and self.nodes[-1]["target"][2] != floor:
            self.nodes[-1]["exact"] = True

    def _run(self, start, kinds_wanted):
        """Indices of consecutive waypoints from ``start`` that pass ``kinds_wanted``, skipping positions."""
        found, i = [], start
        while i < len(self.waypoints):
            wp = self.waypoints[i]
            if wp.get("type", "") != "position":
                if not kinds_wanted(wp):
                    break
                found.append(i)
            i = i + 1
        return found, i

    def keyboard_walks(self, start):
        found, end = self._run(start, lambda wp: wp.get("type", "") == "walk")
        ground = self.nearby_click_ground(start, end)
        path = []
        for i in found:  # a walk's pos is where the step ends
            _add_point(path, (*_xyz(self.waypoints[i]["pos"]), ground, 1))
        self.nodes += _walk_nodes(path, start, end - 1)
        return end

    def nearby_click_ground(self, start, end):
        for wp in self.waypoints[max(0, start - 3):end + 3]:
            if wp.get("type") == "use_item" and _is_map_click_walk(wp):
                return wp["item_id"]
        return self.ground_id or DEFAULT_GROUND_ID

    def map_clicks(self, start):
        """A run of map clicks: walks, except clicks followed by a floor or tile change."""
        found, end = self._run(start, _is_map_click_walk)
        pending = []
        for i in found:
            wp = self.waypoints[i]
            if wp["item_id"] not in GROUND_ITEM_IDS and _has_effect_after(
                    self.waypoints, i, wp["pos"][2], wp["x"], wp["y"], wp["z"]):
                self.click_walk(pending)
                pending = []
                self.nodes.append(_use_node(wp, i))
            else:
                pending.append(i)
        self.click_walk(pending)
        return end

    def click_walk(self, clicks):
        if not clicks:
            return
        path: list[tuple] = []
        for i in clicks:
            wp = self.waypoints[i]
            _add_point(path, (*_xyz(wp["pos"]), wp["item_id"], wp.get("stack_pos", 1)))
            if self.ground_id is None:
                self.ground_id = wp["item_id"]
        last = self.waypoints[clicks[-1]]
        item = (last["item_id"], last.get("stack_pos", 1))
        goal = (last["x"], last["y"], last["pos"][2])
        # A keyboard step can interrupt click-to-walk before the clicked tile;
        # then end where that step started instead of at the unvisited tile.
        for wp in self.waypoints[clicks[-1] + 1:]:
            if wp.get("type") == "position":
                continue
            origin = wp.get("player_pos")
            if (wp.get("type") == "walk" and isinstance(origin, (list, tuple))
                    and len(origin) == 3 and origin[2] == goal[2]):
                goal = tuple(origin)
            break
        _add_point(path, (*goal, *item))
        self.nodes += _walk_nodes(path, clicks[0], clicks[-1])


def _merge_repeats(nodes):
    """Fold consecutive nodes with the same type and target into the first."""
    merged = []
    for node in nodes:
        previous = merged[-1] if merged else None
        if previous and (node["type"], node["target"]) == (previous["type"], previous["target"]):
            if "wp_range" in previous and "wp_range" in node:
                previous["wp_range"][1] = max(previous["wp_range"][1], node["wp_range"][1])
        else:
            merged.append(node)
    return merged


def _needs_exact_arrival(node, following):
    """A walk right before a use, a floor change or a step list must end on its tile."""
    return node["type"] == "walk_to" and (
        following["type"] in ("use_item", "use_item_ex", "walk_steps")
        or (following["type"] == "walk_to" and following["target"][2] != node["target"][2]))


def build_actions_map(recording):
    """The navigation nodes for a recording's raw waypoints."""
    nodes = _merge_repeats(_MapBuilder(recording.get("waypoints", [])).build())
    for node, following in zip(nodes, nodes[1:]):
        if _needs_exact_arrival(node, following):
            node["exact"] = True
    return nodes  # in route order


def _describe(node):
    where = "({},{},{})".format(*node["target"])
    kind = node["type"]
    if kind == "walk_to":
        return f"walk_to {where}" + (" [exact]" if node.get("exact") else "")
    label = node["label"] if "label" in node else f"item {node['item_id']}"
    if kind == "use_item":
        floor = " *floor change*" if node["target"][2] != node.get("player_pos", node["target"])[2] else ""
        return f"use_item {label} {where}{floor}"
    if kind == "use_item_ex":
        return f"use_item_ex {label} {where}"
    if kind == "walk_steps":
        directions = ", ".join(step["direction"] for step in node.get("steps", []))
        return f"walk_steps {where} *{node.get('label', 'floor change')}* [{directions}]"
    return None


def actions_map_to_text(actions_map):
    """One numbered line per node, with the waypoints it came from, for the log preview."""
    lines: list[str] = []
    for number, node in enumerate(actions_map, start=1):
        text = _describe(node)
        if text is not None:
            span = node.get("wp_range")
            lines.append(f"{number}. {text}" + (f" [wp {span[0] + 1}-{span[1] + 1}]" if span else ""))
    return "\n".join(map(str, lines))
