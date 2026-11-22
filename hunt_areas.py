"""Named, multi-floor hunting grounds derived from completed route laps."""
from collections import Counter
from contextlib import closing
import json
import sqlite3
from uuid import UUID, uuid4

from transition_learning import position


SIDE = 8


def sector(pos):
    return (pos[0] // SIDE * SIDE, pos[1] // SIDE * SIDE, pos[2])


def checked_sectors(rows):
    if not isinstance(rows,list) or not rows or not all(position(p) and sector(p)==tuple(p) for p in rows):
        raise ValueError('Zaznacz co najmniej jeden sektor na siatce expowiska.')
    return frozenset(map(tuple,rows))


def observed_position(pos):
    return position(pos) and (pos[0] != 0 or pos[1] != 0)


def read_saved(atlas):
    with closing(sqlite3.connect(atlas.path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='hunt_areas'").fetchone():
            return [], {}
        rows = [dict(id=i, version=v, **json.loads(data)) for i, v, data in
                db.execute('SELECT id,version,data FROM hunt_areas ORDER BY rowid')]
        aliases = dict(db.execute('SELECT revision,draft FROM hunt_area_revisions'))
    return rows, aliases


def save_area(atlas, body):
    if not isinstance(body, dict) or body.get('scope') != atlas.scope:
        raise ValueError('Zmieniono atlas. Wczytaj obszary ponownie.')
    area_id, version = body.get('id'), body.get('version')
    name, cells, drafts = body.get('name'), body.get('cells'), body.get('drafts')
    if not isinstance(name, str) or not 0 < len(name.strip()) <= 100:
        raise ValueError('Podaj nazwę obszaru o długości do 100 znaków.')
    if not isinstance(cells, list) or not cells or not all(position(p) and p[0] % SIDE == 0 and p[1] % SIDE == 0 for p in cells):
        raise ValueError('Zaznacz co najmniej jeden sektor na mapie.')
    if not isinstance(drafts, list) or not drafts or not all(isinstance(i, str) for i in drafts):
        raise ValueError('Wybierz szkic dla tego obszaru.')
    if area_id is not None and (not isinstance(area_id,str) or type(version) is not int):
        raise ValueError('Nieprawidłowa wersja obszaru.')
    data = dict(name=name.strip(), cells=sorted(set(map(tuple, cells))), drafts=sorted(set(drafts)))
    with atlas.lock, closing(sqlite3.connect(atlas.path, timeout=1)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS hunt_areas(id TEXT PRIMARY KEY,version INTEGER,data TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS hunt_area_revisions(revision TEXT PRIMARY KEY,draft TEXT)')
        previous = db.execute('SELECT data FROM hunt_areas WHERE id=?',(area_id,)).fetchone()
        retained = json.loads(previous[0])['drafts'] if previous else []
        # Keep old, verifiable revision bindings even after geometry or names change.
        from route_identity import navigation_revision
        from map_atlas import validate_links
        for ident in data['drafts']:
            row = db.execute('SELECT points,links,loop,deleted FROM drafts WHERE id=?',(ident,)).fetchone()
            if not row or (row[3] and ident not in retained):
                raise ValueError('Szkic został usunięty. Odśwież obszary.')
            points = json.loads(row[0])
            revision = navigation_revision(dict(id=ident, points=points, links=validate_links(json.loads(row[1] or '[]'),points), loop=bool(row[2])))
            db.execute('INSERT OR IGNORE INTO hunt_area_revisions VALUES (?,?)',(revision,ident))
        if area_id is None:
            key = body.get('create_key')
            if key is not None and not isinstance(key,str):
                raise ValueError('Nieprawidłowy identyfikator zapisu.')
            area_id = UUID(key).hex if key else uuid4().hex
            existing = db.execute('SELECT data FROM hunt_areas WHERE id=?',(area_id,)).fetchone()
            if existing:
                if existing[0] == json.dumps(data):
                    return area_id
                raise ValueError('Ten zapis już istnieje. Wczytaj obszary ponownie.')
            db.execute('INSERT INTO hunt_areas VALUES (?,1,?)',(area_id,json.dumps(data)))
        elif not db.execute('UPDATE hunt_areas SET data=?,version=version+1 WHERE id=? AND version=?',
                            (json.dumps(data),area_id,version)).rowcount:
            raise ValueError('Obszar zmienił się w innym oknie. Wczytaj go ponownie.')
    return area_id


def _evidence(db, ident, tables, sightings):
    events = json.loads(db.execute('SELECT events FROM laps WHERE id=?',(ident,)).fetchone()[0])
    cells = {sector(e['position']) for e in events if observed_position(e.get('position'))}
    if 'lap_footprints' in tables:
        row = db.execute('SELECT cells FROM lap_footprints WHERE id=?',(ident,)).fetchone()
        if row:
            cells.update(map(tuple,json.loads(row[0])))
    observations = Counter()
    for e in events:
        kind = e.get('kind')
        if kind not in ('death','first_contact') or not e.get('detail'):
            continue
        exact = observed_position(e.get('monster_position'))
        pos = e['monster_position'] if exact else e.get('position')
        if observed_position(pos):
            observations[kind,sector(pos), e['detail'], bool(exact)] += 1
    marks = [dict(kind=kind,cell=p,name=name,count=n,at_monster=exact)
             for (kind,p,name,exact),n in sorted(observations.items())]
    cells.update(tuple(row['observer_cell']) for row in sightings or ())
    return dict(cells=sorted(cells), observations=marks)


def catalogue(atlas):
    from hunt_sightings import merge_witnesses
    areas, aliases = read_saved(atlas)
    drafts = {}
    linked = {i for a in areas for i in a['drafts']}
    archived = [d for d in atlas.drafts(deleted=True) if d['id'] in linked] if linked else []
    archived_ids = {d['id'] for d in archived}
    for d in atlas.drafts()+archived:
        aliases[d['navigation_revision']] = d['id']
        drafts[d['id']] = dict(id=d['id'],name=d['name'],terrain=d.get('terrain'),archived=d['id'] in archived_ids,laps=0,duration_ms=0,blocked_ms=0,
                               xp=0,xp_ms=0,senzu=0,senzu_ms=0,latest=None,sighting_laps=0,sightings=Counter(),witnesses=[])
    path = atlas.path.with_name('cavebot-reports-' + atlas.path.name)
    if path.exists():
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'laps' in tables:
                query = ('SELECT l.id,l.summary,s.sightings FROM laps l LEFT JOIN lap_sightings s ON s.id=l.id'
                         if 'lap_sightings' in tables else 'SELECT id,summary,NULL FROM laps')
                for ident, raw, raw_sightings in db.execute(query+' ORDER BY started DESC'):
                    s = json.loads(raw)
                    d = drafts.get(s.get('draft_id') or aliases.get(s.get('revision')))
                    if d is None or not s['complete'] or s['duration_ms'] <= 0:
                        continue
                    d['laps'] += 1
                    d['duration_ms'] += s['duration_ms']
                    d['blocked_ms'] += s['blocked_ms']
                    for metric in ('xp','senzu'):
                        if s.get(metric) is not None:
                            d[metric] += s[metric]
                            d[metric+'_ms'] += s['duration_ms']
                    sightings = json.loads(raw_sightings) if raw_sightings is not None else None
                    if sightings is not None:
                        d['sighting_laps'] += 1
                        for row in sightings:
                            key = (tuple(row['point']), tuple(row['observer_cell']), row['name'])
                            d['sightings'][key] += row['count']
                        groups = [dict(points=row['together'],name=row['name'],observer_cell=row['observer_cell'])
                                  for row in sightings if row.get('together')]
                        if groups:
                            d['witnesses'] = merge_witnesses([*d['witnesses'],*groups])
                    if d['latest'] is None:
                        d['latest'] = dict(id=ident, started=s['started_at'], **_evidence(db,ident,tables,sightings))
    for d in drafts.values():
        d['sightings'] = [dict(kind='first_seen', cell=sector(point), point=point, observer_cell=origin,
                               name=name, count=count, at_monster=True)
                          for (point,origin,name),count in sorted(d['sightings'].items())]
        for metric in ('xp','senzu'):
            duration = d.pop(metric+'_ms')
            d[metric+'_hour'] = round(d.pop(metric)*3600000/duration) if duration else None
    return dict(scope=atlas.scope,areas=areas,drafts=list(drafts.values()),sector_size=SIDE)
