"""World-wide hunting sectors and first sightings, independent of sketch names."""
from collections import Counter, OrderedDict, defaultdict
from contextlib import closing
from functools import lru_cache
from threading import Lock
import hashlib
import json
import sqlite3

from hunt_areas import SIDE, observed_position, sector


def _stamp(path):
    def stat(p):
        try:
            s = p.stat()
            return s.st_mtime_ns, s.st_size
        except FileNotFoundError:
            return 0, 0
    return stat(path), stat(path.with_name(path.name+'-wal'))


def _reader(path):
    return closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.5))


def _collect(rows, cells, sightings):
    for row in rows:
        p = row.get('point')
        if observed_position(p) and row.get('name') and row.get('count',0)>0:
            sightings[tuple(p),row['name']] += row['count']
            origin = row.get('observer_cell')
            if observed_position(origin):
                cells.add(tuple(origin))


@lru_cache(maxsize=2)
def _manual(path, stamp):
    cells, seen = set(), Counter()
    if stamp[0][1]:
        with _reader(path) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='grounds'").fetchone():
                for raw, in db.execute('SELECT data FROM grounds'):
                    row=json.loads(raw)
                    cells.update(map(tuple,row['cells']))
                    _collect(row.get('sightings',()),cells,seen)
    return cells, seen


@lru_cache(maxsize=2)
def _laps(path, stamp):
    cells, seen = set(), Counter()
    if stamp[0][1]:
        with _reader(path) as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'laps' in tables:
                complete="json_extract(l.summary,'$.complete')=1"
                if 'lap_footprints' in tables:
                    for raw, in db.execute('SELECT f.cells FROM laps l JOIN lap_footprints f ON f.id=l.id WHERE '+complete):
                        cells.update(map(tuple,json.loads(raw)))
                # Legacy reports without a footprint still have confirmed player positions.
                missing=' AND NOT EXISTS (SELECT 1 FROM lap_footprints f WHERE f.id=l.id)' if 'lap_footprints' in tables else ''
                for raw, in db.execute('SELECT l.events FROM laps l WHERE '+complete+missing):
                    cells.update(sector(e['position']) for e in json.loads(raw) if observed_position(e.get('position')))
                if 'lap_sightings' in tables:
                    for raw, in db.execute('SELECT s.sightings FROM laps l JOIN lap_sightings s ON s.id=l.id WHERE '+complete):
                        _collect(json.loads(raw),cells,seen)
    return cells, seen


def world_zones(atlas, since=None):
    manual=atlas.path.with_name('manual-hunts-'+atlas.path.name)
    laps=atlas.path.with_name('cavebot-reports-'+atlas.path.name)
    visit_stamp,lap_stamp=_stamp(manual),_stamp(laps)
    with _reader(atlas.path) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        areas=db.execute('SELECT id,version FROM hunt_areas ORDER BY id').fetchall() if 'hunt_areas' in tables else []
        drafts=db.execute('SELECT id,updated FROM drafts WHERE terrain IS NOT NULL AND deleted=0 ORDER BY id').fetchall()
        walked = db.execute('SELECT COUNT(*) FROM visited_sectors').fetchone()[0] if 'visited_sectors' in tables else 0
        revision=hashlib.sha256(repr((atlas.scope,visit_stamp,lap_stamp,areas,drafts,walked)).encode()).hexdigest()[:24]
        result=dict(scope=atlas.scope,revision=revision,side=SIDE)
        if revision==since:
            return result
        cells=set(db.execute("SELECT x,y,z FROM visited_sectors")) if walked else set()
        if areas:
            for raw, in db.execute('SELECT data FROM hunt_areas'):
                cells.update(map(tuple,json.loads(raw)['cells']))
        if drafts:
            for raw, in db.execute('SELECT terrain FROM drafts WHERE terrain IS NOT NULL AND deleted=0'):
                cells.update(map(tuple,json.loads(raw)['sectors']))
    visited,manual_seen=_manual(manual,visit_stamp)
    covered,lap_seen=_laps(laps,lap_stamp)
    cells.update(visited,covered)
    totals, peaks = Counter(), {}
    for (point, name), count in (manual_seen + lap_seen).items():
        key = sector(point), name
        totals[key] += count
        peaks[key] = max(peaks.get(key, (0, point)), (count, point))
    species=defaultdict(list)
    spawns = []
    for (area, name), count in sorted(totals.items()):
        species[area].append((name, count))
        spawns.append(dict(point=peaks[area, name][1], name=name, count=count))
    result.update(cells=sorted(cells),habitats=[dict(position=p,species=names) for p,names in species.items()],
                  spawns=spawns)
    return result


# Only panels that request deltas keep recent views. A cache miss sends a full
# snapshot, so a reconnect or an evicted revision always recovers.
_zone_indexes = OrderedDict()
_zone_indexes_lock = Lock()


def _sector_index(snapshot):
    index = {tuple(pos): [True, None, []] for pos in snapshot["cells"]}
    for habitat in snapshot["habitats"]:
        index.setdefault(tuple(habitat["position"]), [False, None, []])[1] = habitat
    for spawn in snapshot["spawns"]:
        index.setdefault(sector(spawn["point"]), [False, None, []])[2].append(spawn)
    return index


def world_zone_update(atlas, since=None):
    if not since or not since.startswith("delta:"):
        return world_zones(atlas, since)
    requested = since[len("delta:"):]
    snapshot = world_zones(atlas, requested or None)
    if "cells" not in snapshot:
        return snapshot
    key = (str(atlas.path), atlas.scope)
    index = _sector_index(snapshot)
    with _zone_indexes_lock:
        previous = _zone_indexes.get((*key, requested))
        current_key = (*key, snapshot["revision"])
        _zone_indexes[current_key] = index
        _zone_indexes.move_to_end(current_key)
        while len(_zone_indexes) > 4:
            _zone_indexes.popitem(last=False)
    if previous is None:
        return snapshot
    changed = sorted(pos for pos in previous.keys() | index.keys() if previous.get(pos) != index.get(pos))
    rows = [(pos, index[pos]) for pos in changed if pos in index]
    return dict(scope=atlas.scope, side=snapshot["side"], revision=snapshot["revision"],
                base_revision=requested, changed=changed,
                cells=[pos for pos, row in rows if row[0]],
                habitats=[row[1] for _, row in rows if row[1]],
                spawns=[mark for _, row in rows for mark in row[2]])
