"""Share a cavebot route with another player as a route pack file.

A route alone cannot be planned on someone else's computer: planning needs the
learned map tiles around it and the doors, ladders, stairs and ropes it passes.
A route pack carries the route together with exactly that, in the same format
as the shipped example routes (config/starter-map.json.gz).

Export keeps only what planning needs: waypoint notes are cleared, rope
recordings lose their names, and hunt grounds drawn by the author are left out.
Import never changes anything the player already has: their own tiles, stairs
and routes win, and the imported route is always added as a new route.
"""
import json
import sqlite3
import time
import uuid
from contextlib import closing

from dbv.brain.cavebot.cavebot_combat import validate_combat
from dbv.brain.navigation.map_atlas import MAX_ANCHORS, validate_links, validate_tags
from dbv.brain.navigation.transition_learning import position

FORMAT = "dbvictory-route"
RADIUS = 8  # tiles kept on every side of the planned path
MAX_DRAFTS = 100
MAX_FILE = 32 * 1024 * 1024  # uncompressed JSON of one shared pack
MAX_TILES = 400_000
MAX_OBSERVATIONS = 20_000
TILE_KEYS = {"x", "y", "z", "kind", "items", "navigation", "transitions", "minimap_color", "web_cut"}
SHARED_RECORDING = "shared"


# ── export ─────────────────────────────────────────────────────────────

def _segment(a, b):
    """Grid points on the straight line between two waypoints on one floor."""
    if a[2] != b[2]:
        return [a, b]
    steps = max(abs(b[0] - a[0]), abs(b[1] - a[1]), 1)
    return [(round(a[0] + (b[0] - a[0]) * i / steps), round(a[1] + (b[1] - a[1]) * i / steps), a[2])
            for i in range(steps + 1)]


def _corridor(centres, radius=RADIUS):
    """Every position within `radius` (Chebyshev) of a centre, per floor."""
    near = set()
    for x, y, z in centres:
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                near.add((x + dx, y + dy, z))
    return near


def _route(atlas, draft_id):
    for draft in atlas.drafts():
        if draft["id"] == draft_id:
            return draft
    raise ValueError("Nie ma takiej trasy w tym profilu.")


def _shared_draft(draft):
    row = {
        "id": uuid.uuid4().hex,
        "name": draft["name"],
        "points": json.dumps(draft["points"]),
        "updated": time.time(),
        "loop": int(bool(draft["loop"])),
        "combat": json.dumps(draft["combat"]),
        "deleted": 0,
        # Waypoint notes are the author's own; the route runs without them.
        "labels": json.dumps([""] * len(draft["points"])),
        "tags": json.dumps(draft.get("tags") or []),
        "links": json.dumps(draft.get("links") or []),
        "terrain": None,
    }
    terrain = draft.get("terrain")
    # Hunt grounds the author drew exist only on their computer.
    if isinstance(terrain, dict) and terrain.get("ground") == "world":
        row["terrain"] = json.dumps(terrain)
    return row


def export(atlas, observations, draft_id, server):
    """Build a route pack for one saved route. `observations` are the learner's records."""
    if atlas.error:
        raise ValueError(atlas.error)
    draft = _route(atlas, draft_id)
    points = [tuple(p) for p in draft["points"]]
    observations = list(observations)
    try:
        plan = atlas.plan(draft["points"], observations, loop=draft["loop"], links=draft["links"])
        path = [tuple(p) for p in plan["path"]]
        complete = bool(plan["complete"])
    except (ValueError, KeyError, TypeError):
        path, complete = [], False
    centres = set(path) | set(points)
    pairs = list(zip(points, points[1:])) + ([(points[-1], points[0])] if draft["loop"] else [])
    for a, b in pairs:
        centres.update(_segment(a, b))
    wanted = _corridor(centres)

    with atlas.lock, closing(sqlite3.connect(atlas.path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
        tiles = []
        for z in sorted({p[2] for p in centres}):
            xs = [p[0] for p in centres if p[2] == z]
            ys = [p[1] for p in centres if p[2] == z]
            rows = db.execute("SELECT x,y,z,data,seen FROM tiles WHERE z=? AND x BETWEEN ? AND ? AND y BETWEEN ? AND ?",
                              (z, min(xs) - RADIUS, max(xs) + RADIUS, min(ys) - RADIUS, max(ys) + RADIUS))
            for x, y, _, raw, seen in rows:
                if (x, y, z) in wanted:
                    data = {k: v for k, v in json.loads(raw).items() if k in TILE_KEYS}
                    tiles.append([x, y, z, json.dumps(data, separators=(",", ":")), seen])
        ropes = db.execute("SELECT id,edge FROM recorded_ropes").fetchall()

    kept = {(t[0], t[1], t[2]) for t in tiles}
    linked = {link["id"] for link in draft["links"]}

    def needed(row):
        return (row.get("id") in linked or tuple(row.get("origin") or ()) in kept
                or tuple(row.get("entry") or ()) in kept)

    shared_observations = sorted(
        ([row["id"], json.dumps(row, ensure_ascii=False, separators=(",", ":"))]
         for row in observations if needed(row)), key=lambda r: r[0])
    shared_ropes = sorted(
        [identity, SHARED_RECORDING, edge] for identity, edge in ropes if needed(json.loads(edge)))
    return {
        "format": FORMAT,
        "version": 1,
        "server": server,
        "scope": atlas.scope,
        "exported": time.time(),
        "complete": complete,
        "drafts": [_shared_draft(draft)],
        "tiles": tiles,
        "observations": shared_observations,
        "recorded_ropes": shared_ropes,
    }


def file_name(name):
    """A safe file name for the route, e.g. "Saiyan - Vegeta East.dbvroute"."""
    clean = "".join(c if c.isalnum() or c in " -_." else "_" for c in name).strip(" .") or "trasa"
    return clean[:60] + ".dbvroute"


# ── import ─────────────────────────────────────────────────────────────

def _bad():
    return ValueError("To nie jest prawidłowy plik trasy DBVictory.")


def _checked_tiles(rows):
    if not isinstance(rows, list) or len(rows) > MAX_TILES:
        raise _bad()
    result = []
    for row in rows:
        if (not isinstance(row, list) or len(row) != 5 or not position(row[:3])
                or not isinstance(row[3], str) or type(row[4]) not in (int, float)):
            raise _bad()
        try:
            data = json.loads(row[3])
        except ValueError:
            raise _bad() from None
        if (not isinstance(data, dict) or not set(data) <= TILE_KEYS or [data.get(k) for k in "xyz"] != row[:3]
                or not isinstance(data.get("kind"), str)):
            raise _bad()
        result.append((row[0], row[1], row[2], json.dumps(data, separators=(",", ":")), float(row[4])))
    return result


def _checked_observations(rows, learner):
    if not isinstance(rows, list) or len(rows) > MAX_OBSERVATIONS:
        raise _bad()
    result = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 2 or not isinstance(row[1], str):
            raise _bad()
        try:
            data = json.loads(row[1])
            learner.validate(data)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise _bad() from None
        if data["id"] != row[0]:
            raise _bad()
        result.append(data)
    return result


def _checked_ropes(rows, learner):
    if not isinstance(rows, list) or len(rows) > MAX_OBSERVATIONS:
        raise _bad()
    result = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 3 or not isinstance(row[0], str) or not isinstance(row[2], str):
            raise _bad()
        try:
            edge = json.loads(row[2])
        except ValueError:
            raise _bad() from None
        if not isinstance(edge, dict) or edge.get("id") != row[0] or not position(edge.get("origin")) \
                or not position(edge.get("exit")):
            raise _bad()
        result.append((row[0], SHARED_RECORDING, json.dumps(edge)))
    return result


def _checked_drafts(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_DRAFTS:
        raise _bad()
    result = []
    for row in rows:
        try:
            points = json.loads(row["points"])
            name = row["name"]
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                raise ValueError()
            if not isinstance(points, list) or not 2 <= len(points) <= MAX_ANCHORS or not all(position(p) for p in points):
                raise ValueError()
            links = validate_links(json.loads(row.get("links") or "[]"), points)
            tags = validate_tags(json.loads(row.get("tags") or "[]"))
            combat = validate_combat(json.loads(row.get("combat") or "{}"))
            terrain = json.loads(row["terrain"]) if row.get("terrain") else None
            if terrain is not None and (not isinstance(terrain, dict) or terrain.get("ground") != "world"):
                terrain = None
        except (ValueError, KeyError, TypeError, AttributeError):
            raise _bad() from None
        result.append(dict(name=name.strip(), points=points, loop=bool(row.get("loop", 1)), links=links,
                           tags=tags, combat=combat, terrain=terrain))
    return result


def read(pack):
    """Check the shape of a pack (a dict) before anything is written."""
    if (not isinstance(pack, dict) or pack.get("version") != 1 or not isinstance(pack.get("server"), str)
            or pack.get("format", FORMAT) != FORMAT):
        raise _bad()
    return pack


def _unique(name, taken):
    if name not in taken:
        return name
    for n in range(2, 1000):
        candidate = f"{name[:72]} ({n})"
        if candidate not in taken:
            return candidate
    return f"{name[:60]} {uuid.uuid4().hex[:8]}"


def import_pack(atlas, learner, pack, server):
    """Add a shared route and its map. Returns the new routes' IDs. Never overwrites."""
    pack = read(pack)
    if atlas.error:
        raise ValueError(atlas.error)
    if learner is None or learner.scope != atlas.scope:
        raise ValueError("Mapa czeka na połączenie. Zaloguj się i spróbuj ponownie.")
    if pack["server"] != server:
        raise ValueError("Ta trasa pochodzi z innego serwera gry. Mapa i trasy nie pasują do tego serwera.")
    drafts = _checked_drafts(pack.get("drafts"))
    tiles = _checked_tiles(pack.get("tiles", []))
    observations = _checked_observations(pack.get("observations", []), learner)
    ropes = _checked_ropes(pack.get("recorded_ropes", []), learner)
    with atlas.lock:
        with closing(sqlite3.connect(atlas.path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            count = db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0]
        if count + len(drafts) > MAX_DRAFTS:
            raise ValueError("Osiągnięto limit 100 szkiców w tym profilu (razem z usuniętymi).")
        # Learned stairs and doors first: a failure there leaves the atlas untouched.
        learner.add_shared(observations)
        created = []
        with closing(sqlite3.connect(atlas.path, timeout=2)) as db, db:
            added = db.executemany("INSERT OR IGNORE INTO tiles (x,y,z,data,seen) VALUES (?,?,?,?,?)", tiles).rowcount
            db.executemany("INSERT OR IGNORE INTO recorded_ropes (id,recording,edge) VALUES (?,?,?)", ropes)
            taken = {name for (name,) in db.execute("SELECT name FROM drafts WHERE deleted=0")}
            for draft in drafts:
                draft_id = uuid.uuid4().hex
                name = _unique(draft["name"], taken)
                taken.add(name)
                db.execute("INSERT INTO drafts (id,name,points,updated,loop,combat,deleted,labels,tags,links,terrain) "
                           "VALUES (?,?,?,?,?,?,0,?,?,?,?)",
                           (draft_id, name, json.dumps(draft["points"]), time.time(), int(draft["loop"]),
                            json.dumps(draft["combat"]), json.dumps([""] * len(draft["points"])),
                            json.dumps(draft["tags"]), json.dumps(draft["links"]),
                            json.dumps(draft["terrain"]) if draft["terrain"] else None))
                created.append(draft_id)
            stored = dict(db.execute("SELECT id,edge FROM recorded_ropes"))
        atlas.total += max(added, 0)
        atlas.floors.update(t[2] for t in tiles)
        atlas.recorded_ropes.update((k, json.loads(v)) for k, v in stored.items() if k not in atlas.recorded_ropes)
    return created
