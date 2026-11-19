"""Local lap history. Only finished laps go to disk, outside the automation loop."""
import asyncio
from collections import deque
from contextlib import closing
from itertools import islice
import json
import logging
import sqlite3
import time

from cavebot_reports import Lap
from farming_telemetry import experience_for_level
from route_identity import navigation_revision
from hunt_areas import observed_position
from hunt_sightings import FirstSightings

log = logging.getLogger(__name__)


def archive_path(state):
    atlas = getattr(getattr(state, 'passive_map_monitor', None), 'atlas', None)
    path = getattr(atlas, 'path', None)
    return path.with_name('cavebot-reports-' + path.name) if path is not None else None


def summary(report):
    return {k:v for k,v in report.items() if k not in ('events', 'visited_sectors', 'first_sightings')}


def save_report(path, report):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS laps (id TEXT PRIMARY KEY, started REAL, summary TEXT, events TEXT)')
        db.execute('CREATE INDEX IF NOT EXISTS by_start ON laps(started)')
        db.execute('INSERT OR IGNORE INTO laps VALUES (?,?,?,?)',
                   (report['id'], report['started_at'], json.dumps(summary(report)), json.dumps(report['events'])))
        if report.get('visited_sectors'):
            db.execute('CREATE TABLE IF NOT EXISTS lap_footprints(id TEXT PRIMARY KEY,cells TEXT)')
            db.execute('INSERT OR IGNORE INTO lap_footprints VALUES (?,?)',
                       (report['id'], json.dumps(report['visited_sectors'])))
        if report.get('first_sightings') is not None:
            db.execute('CREATE TABLE IF NOT EXISTS lap_sightings(id TEXT PRIMARY KEY, sightings TEXT)')
            db.execute('INSERT OR IGNORE INTO lap_sightings VALUES (?,?)',
                       (report['id'], json.dumps(report['first_sightings'])))


def read_reports(state, ident=None, before=None):
    path = archive_path(state)
    recent = getattr(state, 'cavebot_reports', ())
    memory = [r for scope,r in list(recent) if scope == path]
    run = getattr(getattr(state, 'adaptive_cavebot', None), 'reporter', None)
    active = run.preview if run is not None else None
    if ident:
        for report in memory:
            if report['id'] == ident:
                return report
    collected = {r['id']:summary(r) for r in memory if before is None or r['started_at'] < before}
    if path and path.exists():
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True,timeout=1)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='laps'").fetchone():
                if ident:
                    row = db.execute('SELECT summary,events FROM laps WHERE id=?', (ident,)).fetchone()
                    if row is None:
                        return None
                    report = dict(json.loads(row[0]), events=json.loads(row[1]))
                    if db.execute("SELECT 1 FROM sqlite_master WHERE name='lap_sightings'").fetchone():
                        sightings = db.execute('SELECT sightings FROM lap_sightings WHERE id=?', (ident,)).fetchone()
                        if sightings:
                            report['first_sightings'] = json.loads(sightings[0])
                    return report
                for (data,) in db.execute('SELECT summary FROM laps WHERE started < ? ORDER BY started DESC LIMIT 21',
                                           (float('inf') if before is None else before,)):
                    item = json.loads(data)
                    collected[item['id']] = item
    if ident:
        return None
    rows = sorted(collected.values(), key=lambda r:r['started_at'], reverse=True)
    return dict(active=active,reports=rows[:20],more=len(rows)>20)


class RunHistory:
    def __init__(self, owner, resumed):
        self.owner = owner
        self.gs = owner.state.game_state
        self.profile = (getattr(owner.state,'settings',{}) or {}).get('selected_combat_profile') or 'Ustawienia trasy'
        store = getattr(owner.state, 'profile_store', None)
        if store and self.profile != 'Ustawienia trasy':
            try:
                self.profile = store.get(self.profile, 'combat')['name']
            except (KeyError, ValueError, OSError):
                pass
        self.revision = navigation_revision(owner.route_draft) if owner.route_draft else None
        self.path = archive_path(owner.state)
        self.lap = None
        self.start_known = not resumed
        self.next_sample = 0.
        self.last_target = None
        self.tracked = {}
        self.seen_contacts = set()
        self.sightings = FirstSightings(self.gs)
        self.sightings_failed = False
        self.last_kills = getattr(self.gs, 'session_kills', 0)
        self.writes = set()

    def observe_map(self, frame):
        if self.sightings_failed:
            return
        try:
            self.sightings.observe(frame)
        except Exception:
            self.sightings_failed = True
            log.exception('Could not record map sightings for this run')

    def frame(self, now):
        o, gs = self.owner, self.gs
        current = next((c['waypoint'] for c in o.checkpoints if c['index']>o.index), o.last_waypoint)
        xp, level = getattr(gs,'experience',0), getattr(gs,'level',0)
        xp_ok = (getattr(gs,'stats_updated_at',0) and level > 0 and
                 experience_for_level(level) <= xp < experience_for_level(level+1))
        usage = getattr(gs, 'senzu_usage', None)
        combat = o.combat
        group = None
        if combat.policy_mode == 'lure':
            group = dict(goal=combat.config['lure_count'],engaged=combat.confirmed_count,
                         waiting=combat.lure_waiting,reason=combat.lure_stop_reason)
        block = o.blockage or {}
        # Navigation retains the previous blockage during combat. Only a current
        # wait from that observation can establish blocked time.
        cause = block.get('cause')
        blocked_now = cause and (cause in o.reason or o.reason.startswith('Brak potwierdzenia kroku'))
        target = block.get('target') if blocked_now else self.last_target
        return dict(ms=round(now*1000),route=o.name,revision=self.revision,draft_id=getattr(o,'draft_id',None),lap=o.laps,
                    position=list(gs.position),waypoint=None if current is None else current+1,
                    reason=o.reason, step_target=target, movement=o.domains.movement_state,combat=combat.phase,
                    lure=group,manual=now<o.manual_until,block_cause=cause if blocked_now else None,
                    hold_reason=o.navigation_hold, xp=xp if xp_ok else None,
                    senzu=usage.total if usage else None,deaths=getattr(gs,'session_kills',None))

    def sample(self, force=False):
        now = time.monotonic()
        if not force and now < self.next_sample:
            return
        self.next_sample = now + .25
        if self.lap is None and (self.owner.index < self.owner.loop_start or
                                 self.owner.domains.movement_state == 'complete'):
            return
        frame = self.frame(now)
        if self.lap is None:
            self.lap = Lap(frame,self.start_known,time.time(),self.profile)
            self.sightings.begin_lap(self.lap.started_at)
        else:
            self.lap.observe(frame)
        combat = self.owner.combat
        members = set(combat.pursuit.following) | combat.bank_members
        if combat.target and combat.target.get('id'):
            members.add(combat.target['id'])
        wall_time = time.time()
        for cid in members:
            self.tracked[cid] = now
            if cid not in self.seen_contacts:
                creature = self.gs.creatures.get(cid,{})
                pos = [creature.get(k) for k in ('x','y','z')]
                if (creature.get('source') == 'dll' and 0 <= wall_time-creature.get('t',0) <= .6
                        and creature.get('name') and observed_position(pos) and pos[2] == frame['position'][2]):
                    self.seen_contacts.add(cid)
                    self.lap.event(frame,'first_contact',creature['name'],pos)
        kills = frame['deaths'] or 0
        if kills > self.last_kills:
            log_rows = getattr(self.gs,'kill_log',())
            for i in range(min(kills-self.last_kills,len(log_rows)),0,-1):
                row = log_rows[-i]
                if row.get('cid') in self.tracked:
                    pos = [row.get(k) for k in ('x','y','z')]
                    known = observed_position(pos) and pos[2] == frame['position'][2]
                    self.lap.event(frame,'death',row.get('name') or None,pos if known else None)
                    self.tracked.pop(row['cid'])
        self.last_kills = kills
        self._lost_contacts(frame, now)

    def _lost_contacts(self, frame, now):
        missing = [cid for cid,stamp in self.tracked.items() if now-stamp>=1.5]
        if not missing:
            return
        from combat_reachability import reachable_tiles
        from adaptive_cavebot import InputsUnavailable
        try:
            sample = self.owner.map_now()
        except InputsUnavailable:
            return
        accessible = None
        cells = {(c['x'],c['y'],c['z']):c for c in sample['cells']}
        wall_time = time.time()
        for cid in missing:
            row = self.gs.creatures.get(cid,{})
            pos = (row.get('x'),row.get('y'),row.get('z'))
            visible = (row.get('source')=='dll' and 0<=wall_time-row.get('t',0)<=.6 and
                       row.get('health',0)>0 and cells.get(pos,{}).get('occupant_types',{}).get('monster')==1)
            kind = 'contact_lost'
            if visible:
                if accessible is None:
                    accessible = reachable_tiles(sample['cells'],frame['position'])
                kind = 'left_group' if pos in accessible else 'unreachable'
            self.lap.event(frame,kind,row.get('name'))
            del self.tracked[cid]

    @property
    def preview(self):
        lap = self.lap
        if lap is None:
            return None
        return dict(route=lap.first['route'],lap=lap.first['lap']+1,profile=self.profile,
                    events=list(islice(reversed(lap.events),250)))

    def interrupted(self, target, outcome, detail=None):
        self.last_target = list(target)
        if self.lap and outcome != 'position_confirmed':
            frame = self.frame(time.monotonic())
            frame['step_target'] = list(target)
            self.lap.event(frame, outcome, detail)

    def lifecycle(self, kind, reason):
        if self.lap:
            self.lap.event(self.frame(time.monotonic()),kind,reason)

    def finish(self, boundary, reason):
        if self.lap is None:
            return
        end = self.frame(time.monotonic())
        report = self.lap.report(end,boundary,reason)
        sightings = self.sightings.end_lap(time.time() if boundary else None)
        if sightings is not None and not self.sightings_failed:
            report['first_sightings'] = sightings
        self.lap = None
        self.tracked.clear()
        self.seen_contacts.clear()
        self.start_known = True
        state = self.owner.state
        if not hasattr(state,'cavebot_reports'):
            state.cavebot_reports = deque(maxlen=20)
        state.cavebot_reports.append((self.path,report))
        if self.path:
            task = asyncio.create_task(asyncio.to_thread(save_report,self.path,report))
            self.writes.add(task)
            task.add_done_callback(self._written)

    def _written(self, task):
        self.writes.discard(task)
        if not task.cancelled() and task.exception():
            log.warning('Could not archive cavebot lap: %s', task.exception())
