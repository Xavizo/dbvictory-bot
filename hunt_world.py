"""World-wide hunting sectors and first sightings, independent of sketch names."""
from collections import Counter, defaultdict
from contextlib import closing
from functools import lru_cache
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
