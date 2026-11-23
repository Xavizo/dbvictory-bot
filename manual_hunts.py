"""Discover hunting grounds from manual play using the existing passive map frames."""
from collections import Counter, deque
from contextlib import closing
from dataclasses import dataclass
import json
import logging
import sqlite3
from threading import Condition, Thread
import time
from uuid import uuid4

from farming_telemetry import experience_for_level
from hunt_areas import sector
from hunt_sightings import concurrent_firsts, deaths_since, fresh_monster_name, merge_witnesses
from transition_learning import position


log = logging.getLogger(__name__)
QUIET_SECONDS = 90
CHECKPOINT_SECONDS = 20
CHECK_VERSION = 2


@dataclass(slots=True)
class Encounter:
    point: tuple
    origin: tuple
    first_at: float
    last_at: float
    identified: bool = False


class ManualVisits:
    """Owned by the map thread. submit accepts an independent, cumulative snapshot."""
    def __init__(self, game, submit):
        self.game = game
        self.submit = submit
        self.previous = None
        self.previous_xp = None
        self.identities = {}
        self.pruned = 0
        self.generation = -1
        self.kill_count = getattr(game, 'session_kills', 0)
        self.reset_visit()

    def reset_visit(self):
        self.ident = None
        self.started = self.last_gain = self.saved_at = 0
        self.gains = self.xp = 0
        self.dirty = self.returned = False
        self.footprint = {}
        self.anchors = set()
        self.marks = {}
        self.confirmed = Counter()
        self.witnesses = []
        self.pending_marks = {}
        self.trail = deque(maxlen=48)
        self.sequence = 0
        self.last_cell = None

    def accept_sectors(self, sectors):
        for cell in sectors:
            if cell == self.last_cell:
                continue
            self.sequence += 1
            last = self.footprint.get(cell)
            if last is not None and self.sequence-last >= 3:
                self.returned = True
            self.footprint[cell] = self.sequence
            self.last_cell = cell
            self.dirty = True

    def flush(self, final=False):
        if self.gains and (self.dirty or final):
            counts = self.confirmed.copy()
            counts.update(row for cid,row in self.marks.items() if not self.game.pets.is_pet(cid))
            if self.gains < 2 and not counts:
                return
            self.submit(dict(id=self.ident, started=self.started, at=self.last_gain, gains=self.gains,
                             xp=self.xp, seed=self.seed, returned=self.returned, cells=list(self.footprint),
                             witnesses=merge_witnesses([*self.witnesses,*self.recent_witnesses()]),
                             anchors=sorted(self.anchors), sightings=[[p,s,n,c] for (p,s,n),c in counts.items()]), final)
            self.dirty = False

    def finish(self):
        self.flush(True)
        self.reset_visit()
        self.previous = self.previous_xp = None

    def observe(self, frame):
        now, point = frame['captured_at'], frame['center']
        if not position(point) or point[:2] in ([0,0], (0,0)):
            return
        generation = frame['pet_map_generation']
        if generation <= self.generation:
            return
        self.generation = generation
        p = tuple(point)
        cell = sector(p)
        before = self.previous
        if before and (not 0 <= now-before[1] <= 3 or abs(p[2]-before[0][2])>1
                       or max(abs(p[i]-before[0][i]) for i in (0,1))>8):
            self.finish()
            before = None
        if self.ident and now-self.last_gain > QUIET_SECONDS:
            self.flush(True)
            self.reset_visit()
        if not self.trail or self.trail[-1][1] != cell:
            self.trail.append((now,cell,p))
            if (self.gains>=2 and cell in self.footprint and cell != self.last_cell
                    and len(self.trail)<=5 and self.trail[0][0]>=self.last_gain):
                self.accept_sectors(s for _,s,_ in self.trail)
                self.trail.clear()
        level, xp = getattr(self.game,'level',0), getattr(self.game,'experience',0)
        valid = (isinstance(level,int) and 1 <= level < 65536 and isinstance(xp,int)
                 and experience_for_level(level) <= xp < experience_for_level(level+1))
        gain = (xp-self.previous_xp if valid and before and before[0][2]==p[2]
                and self.previous_xp is not None and xp>self.previous_xp else 0)
        self.previous_xp = xp if valid else None
        self.previous = p,now
        self.observe_creatures(frame['pet_map_generation'], cell, now)
        if gain:
            self.observe_gain(p, cell, now, gain)
        if self.dirty and self.gains>=2 and now-self.saved_at >= CHECKPOINT_SECONDS:
            self.flush()
            self.saved_at=now

    def recent_witnesses(self, times=None):
        return concurrent_firsts((*row, self.identities[cid].first_at) for cid,row in self.marks.items()
                                 if (times is None or self.identities[cid].first_at in times)
                                 and not self.game.pets.is_pet(cid))

    def observe_creatures(self, generation, cell, now):
        self.kill_count, deaths = deaths_since(self.game, self.kill_count)
        for death in deaths:
            cid = death.get('cid')
            encounter = self.identities.get(cid)
            if not encounter or encounter.first_at > death.get('t', now):
                continue
            # Preserve the old first contact while a delayed gain is still eligible.
            # The same numeric ID may belong to a different living creature later.
            closed = (cid, encounter.first_at)
            self.identities.pop(cid)
            self.identities[closed] = encounter
            for marks in (self.pending_marks, self.marks):
                if cid in marks:
                    mark = marks.pop(cid)
                    if death.get('creature_kind') != 'pet' and not self.game.pets.is_pet(cid):
                        marks[closed] = mark
        # Only map identities confirmed as named creatures can contribute a mark.
        # Keep their first point even if the battle-list name arrives later.
        for tile, ids in self.game.pets.monster_slots_for_sample(generation).items():
            if tile[2]!=cell[2]:
                continue
            for cid in ids:
                if type(cid) is not int:
                    continue
                entry = self.identities.get(cid)
                if entry is None:
                    entry = self.identities[cid] = Encounter(tile, cell, now, now)
                entry.last_at = now
                if entry.identified or now-entry.first_at>30:
                    continue
                name = fresh_monster_name(self.game,cid,now,tile)
                if name:
                    self.pending_marks[cid] = (entry.first_at,(entry.point,entry.origin,name))
                    entry.identified = True
        if now-self.pruned>=30:
            expired = {self.identities[cid].first_at for cid in self.marks if now-self.identities[cid].last_at>60}
            if expired:
                self.witnesses = merge_witnesses([*self.witnesses,*self.recent_witnesses(expired)])
            for cid,row in list(self.marks.items()):
                if now-self.identities[cid].last_at>60:
                    if not self.game.pets.is_pet(cid):
                        self.confirmed[row]+=1
                    del self.marks[cid]
            self.identities = {i:v for i,v in self.identities.items() if now-v.last_at<600}
            self.pending_marks = {i:v for i,v in self.pending_marks.items() if now-v[0]<=QUIET_SECONDS}
            self.pruned=now

    def crossed_empty_region(self, cell):
        if not self.gains or len(self.trail)<8 or cell in self.footprint:
            return False
        if any(s[2]!=cell[2] for _,s,_ in self.trail):
            return False
        if any(max(abs(cell[0]-s[0]),abs(cell[1]-s[1]))<64 for s in self.footprint if s[2]==cell[2]):
            return False
        marked = {row[1] for _,row in self.pending_marks.values()}
        transit = set()
        for _,s,_ in self.trail:
            if s in self.footprint or s in marked:
                transit.clear()
            else:
                transit.add(s)
            if len(transit)>=8:
                return True
        return False

    def observe_gain(self, p, cell, now, gain):
        if self.crossed_empty_region(cell):
            tail=list(self.trail)[-2:]
            tail_cells={s for _,s,_ in tail}
            tail_marks={i:v for i,v in self.pending_marks.items() if v[1][1] in tail_cells}
            self.flush(True)
            self.reset_visit()
            self.trail.extend(tail)
            self.pending_marks=tail_marks
        if self.ident is None:
            self.ident, self.started, self.seed = uuid4().hex, now, p
        # Keep transit between hunting events, trimming the approach before
        # the first encounter and the trailing journey after the last one.
        since = self.last_gain if self.gains else now-45
        marks = {i:row for i,(t,row) in self.pending_marks.items()
                 if since<=t<=now or (self.gains and row[1] in self.footprint and 0<=now-t<=QUIET_SECONDS)}
        recent = [s for t,s,_ in self.trail if t>=since]
        if not self.gains:
            origins = {row[1] for row in marks.values()}
            first = next((i for i,s in enumerate(recent) if s in origins),len(recent))
            recent = recent[first:]
        self.accept_sectors([*recent,cell])
        self.marks.update(marks)
        self.pending_marks.clear()
        self.anchors.add(cell)
        self.xp+=gain
        self.gains+=1
        self.last_gain=now
        self.dirty=True
        self.trail.clear()


def storage(atlas):
    return atlas.path.with_name('manual-hunts-'+atlas.path.name)


def discoveries(atlas, ident=None):
    path = storage(atlas)
    if not path.exists():
        return []
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.3)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='grounds'").fetchone():
            return []
        rows = (db.execute('SELECT data FROM grounds WHERE id=?',(ident,)) if ident is not None
                else db.execute('SELECT data FROM grounds ORDER BY updated DESC,id'))
        areas = []
        for raw, in rows:
            area = json.loads(raw)
            if 'check' in area and area['check'].get('version') != CHECK_VERSION:
                area.pop('check')
                area['needs_recheck'] = True
            areas.append(area)
        return areas


def remember_check(atlas, area, result):
    """Cache the summary only if the visit still matches the simulated snapshot."""
    check = {k:result[k] for k in ('covered','total','missing','entrance','reason') if k in result}
    check['version'] = CHECK_VERSION
    try:
        with closing(sqlite3.connect(storage(atlas),timeout=.5)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM grounds WHERE id=?',(area['id'],)).fetchone()
            if row:
                current = json.loads(row[0])
                # Worker snapshots contain tuples; archived snapshots use JSON arrays.
                fields=('cells','sightings','seed')
                if (current.get('check') != check and
                        json.dumps([current[k] for k in fields])==json.dumps([area[k] for k in fields])):
                    current['check'] = check
                    db.execute('UPDATE grounds SET data=? WHERE id=?',(json.dumps(current),area['id']))
    except (OSError,sqlite3.Error) as exc:
        # The simulated route remains useful if the optional cache is busy.
        log.warning('Nie zapisano wyniku symulacji expowiska: %s',exc)


def store_visit(atlas, visit):
    """Associate visits by overlapping hunt evidence. A species name alone never joins regions."""
    with closing(sqlite3.connect(storage(atlas),timeout=1)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS visits(id TEXT PRIMARY KEY, ground TEXT, data TEXT)')
        db.execute('CREATE INDEX IF NOT EXISTS by_ground ON visits(ground)')
        db.execute('CREATE TABLE IF NOT EXISTS anchors(x INT,y INT,z INT,ground TEXT,PRIMARY KEY(x,y,z))')
        db.execute('CREATE TABLE IF NOT EXISTS contacts(x INT,y INT,z INT,ox INT,oy INT,name TEXT,ground TEXT,PRIMARY KEY(x,y,z,ox,oy,name))')
        db.execute('CREATE TABLE IF NOT EXISTS grounds(id TEXT PRIMARY KEY,updated REAL,data TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS visit_witnesses(id TEXT PRIMARY KEY, data TEXT)')
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT ground,data FROM visits WHERE id=?',(visit['id'],)).fetchone()
        matches = {existing[0]} if existing else set()
        for s in visit['anchors']:
            match=db.execute('SELECT ground FROM anchors WHERE x=? AND y=? AND z=?',s).fetchone()
            if match:
                matches.add(match[0])
        contacts = {(*sector(p),s[0],s[1],name) for p,s,name,_ in visit['sightings']}
        for contact in contacts:
            match=db.execute('SELECT ground FROM contacts WHERE x=? AND y=? AND z=? AND ox=? AND oy=? AND name=?',contact).fetchone()
            if match:
                matches.add(match[0])
        ident=min(matches) if matches else 'manual:'+visit['id']
        previous={i:json.loads(db.execute('SELECT data FROM grounds WHERE id=?',(i,)).fetchone()[0]) for i in matches}
        for other in matches-{ident}:
            db.execute('UPDATE visits SET ground=? WHERE ground=?',(ident,other))
            db.execute('UPDATE anchors SET ground=? WHERE ground=?',(ident,other))
            db.execute('UPDATE contacts SET ground=? WHERE ground=?',(ident,other))
            db.execute('DELETE FROM grounds WHERE id=?',(other,))
        db.execute('INSERT OR REPLACE INTO visits VALUES (?,?,?)',(visit['id'],ident,json.dumps(visit)))
        db.executemany('INSERT OR IGNORE INTO anchors VALUES (?,?,?,?)',[(*s,ident) for s in visit['anchors']])
        db.executemany('INSERT OR IGNORE INTO contacts VALUES (?,?,?,?,?,?,?)',[(*s,ident) for s in contacts])
        # Apply the difference from the last checkpoint of this visit.
        # Older visits never need to be read again, even after months of play.
        prior=json.loads(existing[1]) if existing else {}
        old=previous.get(ident,{})
        cells=set(map(tuple,visit['cells']))
        visits=int(not existing)
        xp=visit['xp']-prior.get('xp',0)
        gains=visit['gains']-prior.get('gains',0)
        repeated=visit['returned']
        marks=Counter()
        for row in previous.values():
            cells.update(map(tuple,row['cells']))
            visits+=row['visits']
            gains+=row['gains']
            xp+=row['xp']
            repeated |= row['returned']
            for m in row['sightings']:
                marks[tuple(m['point']),tuple(m['observer_cell']),m['name']]+=m['count']
        for sign,items in ((-1,prior.get('sightings',())),(1,visit['sightings'])):
            for p,s,name,count in items:
                marks[tuple(p),tuple(s),name]+=sign*count
        marks=+marks
        species=Counter()
        for (_,_,name),count in marks.items():
            species[name]+=count
        # If a late Look classified a companion, also remove its last match
        # from the index used to associate the next manual visit.
        old_contacts={(*sector(p),s[0],s[1],n) for p,s,n,_ in prior.get('sightings',())}
        if old_contacts-contacts:
            remaining={(*sector(p),s[0],s[1],n) for p,s,n in marks}
            db.executemany('DELETE FROM contacts WHERE x=? AND y=? AND z=? AND ox=? AND oy=? AND name=? AND ground=?',
                           [(*c,ident) for c in old_contacts-remaining])
        seed=old.get('seed',visit['seed'])
        name=species.most_common(1)[0][0] if species else 'Expowisko'
        eligible=(visits>=2 or repeated) and (bool(marks) or gains>=4)
        data=dict(id=ident,name=f"{name} · {seed[0]}, {seed[1]}, {seed[2]}",seed=seed,visits=visits,
                  gains=gains,xp=xp,cells=sorted(cells),drafts=[],eligible=eligible,returned=repeated,
                  sightings=[dict(kind='first_seen',point=p,observer_cell=s,cell=sector(p),name=n,
                                  count=c,at_monster=True) for (p,s,n),c in sorted(marks.items())])
        # A newer checkpoint may retract a pet from its original group. Keep
        # the completed witnesses of other visits separate from this visit.
        groups = visit.get('witnesses',[])
        if groups or prior.get('witnesses'):
            db.execute('INSERT OR REPLACE INTO visit_witnesses VALUES (?,?)',(visit['id'],json.dumps(groups)))
        if prior.get('witnesses') and json.dumps(groups) != json.dumps(prior['witnesses']):
            rows=db.execute('SELECT w.data FROM visits v JOIN visit_witnesses w ON w.id=v.id WHERE v.ground=?',(ident,))
            data['witnesses']=merge_witnesses(group for raw, in rows for group in json.loads(raw))
        else:
            data['witnesses']=merge_witnesses([*groups,*(g for row in previous.values() for g in row.get('witnesses',()))])
        if eligible and old.get('check',{}).get('version')==CHECK_VERSION and old.get('cells')==[list(s) for s in data['cells']]:
            data['check']=old['check']
        db.execute('INSERT OR REPLACE INTO grounds VALUES (?,?,?)',(ident,visit['at'],json.dumps(data)))
    return data


class DiscoveryWriter:
    """Coalesce snapshots while disk I/O or a route simulation is in progress."""
    def __init__(self):
        self.cv=Condition()
        self.pending={}
        self.closed=False
        self.thread=None

    def submit(self, atlas, visit, final, observations):
        with self.cv:
            self.pending[visit['id']] = atlas,visit,final,observations
            if self.thread is None:
                self.thread=Thread(target=self.run,name='hunt-discovery',daemon=True)
                self.thread.start()
            self.cv.notify()

    def close(self):
        with self.cv:
            self.closed=True
            self.cv.notify()
        if self.thread:
            self.thread.join(timeout=3)

    def run(self):
        checked={}
        while True:
            with self.cv:
                self.cv.wait_for(lambda: self.pending or self.closed)
                if not self.pending:
                    return
                key=next(iter(self.pending))
                atlas,visit,final,records=self.pending.pop(key)
            try:
                area=store_visit(atlas,visit)
                last=checked.get(area['id'],0)
                check=area.get('check',{})
                verified=bool(check.get('total')) and check.get('covered')==check['total']
                due = not last or time.monotonic()-last>=90
                if area['eligible'] and ((not verified and (due or final)) or (final and due)):
                    from hunt_routes import manual_circuit
                    checked[area['id']]=time.monotonic()
                    try:
                        result=manual_circuit(atlas,records,area)
                    except ValueError as exc:
                        result=dict(reason=str(exc))
                    remember_check(atlas,area,result)
            except (OSError,sqlite3.Error) as exc:
                log.warning('Opóźniony zapis wykrytego expowiska: %s',exc)
                with self.cv:
                    if not self.closed:
                        self.pending.setdefault(key,(atlas,visit,final,records))
                        self.cv.wait(timeout=2)
            except Exception:
                log.exception('Nie udało się przeliczyć wykrytego expowiska')
