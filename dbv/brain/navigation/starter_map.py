"""Example routes that run on a fresh installation.

config/starter-map.json.gz is a route pack: cavebot routes together with what
planning them needs, namely the learned map tiles around their waypoints and
the doors, ladders, stairs and ropes they pass through. Without the map a
route cannot be planned, even one already tested elsewhere.

The pack is added once to each atlas scope of the server it was learned on.
Everything is inserted only where the player has nothing yet: their own tiles,
transitions and routes always win, and a deleted example stays deleted.
"""
import gzip
import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path

from dbv import paths

APPLIED = "starter-map.json"
log = logging.getLogger(__name__)


def load(path=None):
    pack = json.loads(gzip.decompress(Path(path or paths.STARTER_MAP).read_bytes()))
    if not isinstance(pack, dict) or pack.get("version") != 1 or not isinstance(pack.get("server"), str):
        raise ValueError("unsupported starter map")
    return pack


def _applied(directory):
    try:
        doc = json.loads((directory / APPLIED).read_text(encoding="utf-8"))
        return set(doc.get("scopes", [])) if isinstance(doc, dict) else set()
    except (OSError, ValueError):
        return set()


def _mark(directory, scopes):
    temp = directory / (APPLIED + ".tmp")
    temp.write_text(json.dumps({"version": 1, "scopes": sorted(scopes)}, indent=2), encoding="utf-8")
    temp.replace(directory / APPLIED)


def _add_to_atlas(directory, scope, pack):
    from dbv.brain.navigation.map_atlas import MapAtlas
    atlas = MapAtlas(directory, scope)  # creates or upgrades the schema
    if atlas.error:
        raise RuntimeError(atlas.error)
    columns = ("id", "name", "points", "updated", "loop", "combat", "deleted", "labels", "tags", "links", "terrain")
    with closing(sqlite3.connect(atlas.path, timeout=5)) as db, db:
        db.executemany("INSERT OR IGNORE INTO tiles (x,y,z,data,seen) VALUES (?,?,?,?,?)", pack["tiles"])
        db.executemany("INSERT OR IGNORE INTO recorded_ropes (id,recording,edge) VALUES (?,?,?)", pack["recorded_ropes"])
        for draft in pack["drafts"]:
            row = {**draft, "deleted": 0}
            db.execute(f"INSERT OR IGNORE INTO drafts ({','.join(columns)}) VALUES ({','.join('?' * len(columns))})",
                       [row.get(name) for name in columns])


def _add_transitions(directory, scope, pack):
    path = Path(directory) / f"map_transitions-{scope[:16]}.sqlite3"
    with closing(sqlite3.connect(path, timeout=5)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS store (scope TEXT PRIMARY KEY, revision INTEGER NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS observations (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        row = db.execute("SELECT scope FROM store").fetchone()
        if row is None:
            db.execute("INSERT INTO store VALUES (?,1)", (scope,))
        elif row[0] != scope:
            raise ValueError("transition store belongs to another scope")
        added = db.executemany("INSERT OR IGNORE INTO observations (id,data) VALUES (?,?)", pack["observations"]).rowcount
        if added:
            db.execute("UPDATE store SET revision=revision+1 WHERE scope=?", (scope,))


def apply(directory, scope, server, path=None):
    """Add the starter pack to `scope` once. Never raises; returns whether it was added."""
    try:
        directory = Path(directory)
        if scope in _applied(directory):
            return False
        pack = load(path)
        if pack["server"] != server:
            return False
        directory.mkdir(parents=True, exist_ok=True)
        _add_transitions(directory, scope, pack)
        _add_to_atlas(directory, scope, pack)
        _mark(directory, _applied(directory) | {scope})
        log.info("Added %d example routes with their map to scope %s", len(pack["drafts"]), scope[:16])
        return True
    except Exception:
        log.warning("Could not add the example routes", exc_info=True)
        return False
