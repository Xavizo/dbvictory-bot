"""Observe decisions and split laps at the route executor's own counter boundaries."""
from collections import deque
from datetime import datetime
from statistics import median
from uuid import uuid4
from hunt_areas import observed_position, sector


def decision(frame):
    movement = frame.get('movement')
    combat = frame.get('combat')
    group = frame.get('lure') or {}
    if frame.get('block_cause') or movement == 'held' or combat == 'clear_path':
        return 'blocked'
    if movement == 'complete':
        return 'complete'
    if frame.get('manual'):
        return 'manual'
    if group.get('reason') and (combat == 'fight' or group.get('waiting')):
        return group['reason']
    if combat and combat != 'route':
        return combat
    return 'travel'


def difference(first, last):
    if isinstance(first, int) and isinstance(last, int) and last >= first:
        return last - first
    return None


class Lap:
    def __init__(self, frame, start_known, started_at, profile=None):
        self.id = uuid4().hex
        self.started_at = started_at
        self.first = self.prev = frame
        self.start_known = start_known
        self.profile = profile
        self.group_sizes = []
        self.in_fight = False
        self.blocked_ms = 0
        self.unobserved_ms = 0
        self.events = deque(maxlen=2500)
        self.event_count = 0
        self.visited_sectors = set()
        self.still_since = frame['ms']
        self.still = False
        self.last_key = None
        self.observe(frame)

    def event(self, frame, kind, detail=None, monster_position=None):
        group = frame.get('lure') or {}
        self.event_count += 1
        self.events.append(dict(ms=frame['ms'] - self.first['ms'], kind=kind,
                                position=frame.get('position'), waypoint=frame.get('waypoint'),
                                engaged=group.get('engaged'), goal=group.get('goal'),
                                target=frame.get('step_target'), detail=detail))
        if monster_position is not None:
            self.events[-1]['monster_position'] = list(monster_position)

    def interval(self, frame):
        elapsed = max(0, frame['ms'] - self.prev['ms'])
        if elapsed > 3000:
            self.unobserved_ms += elapsed
        elif decision(self.prev) == 'blocked':
            self.blocked_ms += elapsed

    def observe(self, frame):
        if observed_position(frame.get('position')):
            self.visited_sectors.add(sector(frame['position']))
        self.interval(frame)
        code = decision(frame)
        detail = frame.get('block_cause') or frame.get('hold_reason')
        key = (code, detail, frame.get('step_target') if code == 'blocked' else None)
        if key != self.last_key:
            self.event(frame, code, detail)
            self.last_key = key
        if frame.get('position') != self.prev.get('position') or frame['ms'] - self.prev['ms'] > 3000:
            if self.still:
                self.event(frame, 'moved')
            self.still_since, self.still = frame['ms'], False
        elif frame.get('position') and not self.still and frame['ms'] - self.still_since >= 4000:
            next_tile = frame.get('route_next')
            reason = detail or frame.get('reason') or code
            if next_tile is not None:
                reason = f'{reason} Następne pole trasy {tuple(next_tile)}.'
            self.event(frame, 'stationary', reason)
            self.events[-1].update(route_next=next_tile, movement=frame.get('movement'), combat=frame.get('combat'))
            self.still = True
        if frame.get('waypoint') != self.prev.get('waypoint'):
            self.event(frame, 'waypoint')
        in_fight = frame.get('combat') == 'fight'
        if in_fight and not self.in_fight:
            size = (frame.get('lure') or {}).get('engaged')
            if isinstance(size, int) and size > 0:
                self.group_sizes.append(size)
        self.in_fight = in_fight
        self.prev = frame

    def report(self, end, complete, end_reason):
        self.interval(end)
        self.event(end, 'end', end_reason)
        duration = max(0, end['ms'] - self.first['ms'])
        xp = difference(self.first.get('xp'), end.get('xp'))
        senzu = difference(self.first.get('senzu'), end.get('senzu'))
        return dict(id=self.id, route=self.first['route'], revision=self.first.get('revision'),
                    draft_id=self.first.get('draft_id'), visited_sectors=sorted(self.visited_sectors),
                    profile=self.profile, lap=self.first['lap'] + 1, started_at=self.started_at,
                    complete=bool(self.start_known and complete and not self.unobserved_ms),
                    end_reason=end_reason, duration_ms=duration, blocked_ms=self.blocked_ms,
                    unobserved_ms=self.unobserved_ms, xp=xp, senzu=senzu,
                    xp_hour=round(xp*3600000/duration) if xp is not None and duration else None,
                    senzu_million=round(senzu*1e6/xp, 2) if senzu is not None and xp else None,
                    observed_deaths=difference(self.first.get('deaths'), end.get('deaths')),
                    group_median=median(self.group_sizes) if self.group_sizes else None,
                    event_count=self.event_count, events=list(self.events))


def replay_reports(recording):
    """Version 1 captures can begin and end anywhere, including midway through lap zero."""
    if recording.get('format') != 'dbv-route-replay' or recording.get('version') != 1:
        raise ValueError('Nieobsługiwany format nagrania.')
    epoch = datetime.fromisoformat(recording['started_at'].replace('Z', '+00:00')).timestamp()
    current = None
    reports = []
    for frame in recording['frames']:
        active = (frame.get('status') == 'running' and frame.get('connection') == 'in_game' and frame.get('mode') != 'combat'
                  and frame.get('route') and isinstance(frame.get('lap'), int))
        same = (active and current and current.first['route'] == frame['route']
                and current.first.get('revision') == frame.get('revision'))
        boundary = bool(same and frame['lap'] == current.first['lap'] + 1
                        and 0 <= frame['ms'] - current.prev['ms'] <= 3000)
        if current and (not same or current.first['lap'] != frame.get('lap')):
            end = frame if boundary else current.prev
            reports.append(current.report(end, boundary, 'okrążenie' if boundary else 'przerwa w zapisie'))
            current = None
        if active:
            if current is None:
                current = Lap(frame, boundary, epoch + frame['ms']/1000, frame.get('profile'))
            else:
                current.observe(frame)
    if current:
        reports.append(current.report(current.prev, False, 'koniec nagrania'))
    return reports


if __name__ == '__main__':
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description='Przelicz zapisane obiegi poza grą.')
    parser.add_argument('recordings', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, help='Pełne raporty i decyzje w JSON')
    args = parser.parse_args()
    output = {}
    for path in args.recordings:
        rows = replay_reports(json.loads(path.read_bytes()))
        output[path.name] = rows
        for row in rows:
            kind = 'pełne' if row['complete'] else 'fragment'
            print(f"{path.name} | {row['route']} | {row['lap']} ({kind}) | {row['duration_ms']/1000:.1f} s")
    if args.output:
        args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
