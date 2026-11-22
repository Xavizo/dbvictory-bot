"""Estimate repeated harvests on confirmed atlas circuits. No live game input."""
from collections import Counter, defaultdict
from contextlib import closing
import heapq
import json
from statistics import median
import sqlite3

from hunt_areas import SIDE, sector


# Conservative planning horizon supplied for these grounds. An offscreen
# interval between sightings is not a measurement of the server respawn timer.
RESPAWN_SECONDS = 60
VIEW_RADIUS = 8


class HuntYield:
    def __init__(self, marks, witnesses, selected):
        self.contacts = Counter()
        sightings = defaultdict(Counter)
        for row in marks:
            point = tuple(row['point'])
            cell = sector(point)
            if cell in selected and tuple(row['observer_cell']) in selected:
                self.contacts[cell] += row['count']
                sightings[cell][point] += row['count']
        # Use an observed tile near the center of the sightings. A witnessed
        # group supplies separate positions for the simultaneously visible mobs.
        packs = {}
        for cell, positions in sightings.items():
            n = sum(positions.values())
            x = sum(p[0]*v for p,v in positions.items())/n
            y = sum(p[1]*v for p,v in positions.items())/n
            packs[cell] = [min(positions,key=lambda p:((p[0]-x)**2+(p[1]-y)**2,-positions[p],p))]
        for group in witnesses:
            if tuple(group['observer_cell']) not in selected:
                continue
            by_cell = defaultdict(list)
            for p in group['points']:
                by_cell[sector(p)].append(tuple(p))
            for cell, points in by_cell.items():
                if cell in packs and len(points)>len(packs[cell]):
                    packs[cell] = points
        self.positions = []
        self.by_sector = {}
        self.slots = {}
        for cell, points in packs.items():
            self.slots[cell] = len(points)
            self.by_sector[cell] = tuple(range(len(self.positions),len(self.positions)+len(points)))
            self.positions.extend(points)
        self.view_cache = {}
        self.return_seconds = {}
        self.visits = 0
        self.xp = self.encounters = 0
        self.reference_laps = 0
        self.reference_xp_per_encounter = None
        self.step_s = .28
        self.fight_s = 1.5

    def add_visit(self, counts, xp):
        total = sum(counts.values())
        local = sum(n for cell, n in counts.items() if cell in self.contacts)
        # Whole-visit XP cannot be allocated to a small, different part of a hunt.
        if total and local * 2 >= total and xp is not None and xp >= 0:
            self.visits += 1
            self.xp += xp
            self.encounters += total

    def visible_slots(self, pos):
        if pos not in self.view_cache:
            x,y,z = pos
            left,top,_ = sector((x-VIEW_RADIUS,y-VIEW_RADIUS,z))
            right,bottom,_ = sector((x+VIEW_RADIUS,y+VIEW_RADIUS,z))
            visible = set()
            for cx in range(left,right+1,SIDE):
                for cy in range(top,bottom+1,SIDE):
                    for i in self.by_sector.get((cx,cy,z),()):
                        px,py,_ = self.positions[i]
                        if max(abs(px-x),abs(py-y)) <= VIEW_RADIUS:
                            visible.add(i)
            self.view_cache[pos] = frozenset(visible)
        return self.view_cache[pos]

    def estimate(self, path, passages):
        # Record only changes that can affect a spawn. Re-entering visibility
        # is the last instant it could have respawned while the player was away.
        schedule = []
        travel = 0.
        seen = self.visible_slots(tuple(path[0]))
        returns, hunted = set(), set()
        for a, b in zip(path, path[1:]):
            cells = self.by_sector.get(sector(a), ())
            if cells and sector(a) != sector(b):
                schedule.append((travel, (), cells))
                hunted.update(cells)
                travel = 0.
            travel += self.step_s * (1.4 if a[0] != b[0] and a[1] != b[1] else 1)
            if (tuple(a),tuple(b)) in passages:
                travel += 1.
            visible = self.visible_slots(tuple(b))
            enter = visible - seen
            if enter:
                schedule.append((travel,enter,()))
                returns.update(enter)
                travel = 0.
            seen = visible
        last = self.by_sector.get(sector(path[-1]), ())
        schedule.append((travel,(),last))
        hunted.update(last)
        recovery = {i:self.return_seconds.get(sector(self.positions[i]), RESPAWN_SECONDS) for i in hunted}
        ready_after, last_outside = {}, {}
        elapsed = 0.
        total = rounds = 0
        measured_at = None
        while measured_at is None or elapsed - measured_at < 1200:
            if measured_at is None and elapsed >= RESPAWN_SECONDS:
                measured_at = elapsed
            gained = 0
            for delay, enter, slots in schedule:
                elapsed += delay
                for i in enter:
                    last_outside[i] = elapsed
                available = [i for i in slots if i not in ready_after or last_outside.get(i,-1) >= ready_after[i]]
                gained += len(available)
                elapsed += self.fight_s * len(available)
                for i in available:
                    ready_after[i] = elapsed + recovery[i]
            if measured_at is not None:
                total += gained
                rounds += 1
        duration = elapsed - measured_at
        rate = 3600 * total / duration
        reward = self.reference_xp_per_encounter
        if reward is None and self.encounters:
            reward = self.xp / self.encounters
        return dict(seconds=round(duration / rounds,1), encounters_hour=round(rate,1),
                    kept_visible=len(hunted-returns),
                    xp_hour=round(rate*reward) if reward is not None and hunted else None)


def observed_returns(events, slots):
    """Time to a fresh local contact after a close kill and leaving its view radius.

    Report a per-lap median for the sector. The interval is an encounter
    estimate, never an exact timer or evidence that an offscreen room was empty.
    """
    contacts, cleared = {}, {}
    intervals = defaultdict(list)
    for event in events:
        pos = event.get('position')
        if not pos:
            continue
        here = sector(pos)
        for row in cleared.values():
            origin = row[1]
            if pos[2] != origin[2] or max(abs(pos[0]-origin[0]),abs(pos[1]-origin[1])) > VIEW_RADIUS:
                row[2] = True
        mob = event.get('monster_position')
        kind, name = event['kind'], event.get('detail')
        if kind not in ('first_contact','death') or not mob or not name or sector(mob) != here or here not in slots:
            continue
        key = here, name
        now = event['ms']/1000
        if kind == 'first_contact':
            previous = cleared.pop(key,None)
            if previous and previous[2] and max(abs(a-b) for a,b in zip(previous[1],mob)) <= 2:
                gap = now-previous[0]
                if 0 < gap <= RESPAWN_SECONDS:
                    intervals[here].append(gap)
            contacts[key] = mob
        else:
            first = contacts.pop(key,None)
            if (first and max(abs(a-b) for a,b in zip(first,mob)) <= 2
                    and max(abs(a-b) for a,b in zip(pos,mob)) <= 1):
                cleared[key] = [now,mob,False]
    return {cell:median(gaps) for cell,gaps in intervals.items()}


def comparison_model(atlas, area, sources, selected, observations, *, manual_sources=()):
    manual = str(area.get('id','')).startswith('manual:')
    visits_from = [area] if manual else manual_sources
    evidence = list(visits_from) + list(sources)
    marks = [m for s in evidence for m in s.get('sightings',())]
    witnesses = [g for s in evidence for g in s.get('witnesses',())]
    model = HuntYield(marks, witnesses, selected)
    if visits_from:
        from manual_hunts import storage
        path = storage(atlas)
        if path.exists():
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                keys = [item['id'] for item in visits_from]
                query = 'SELECT data FROM visits WHERE ground IN ('+','.join('?' for _ in keys)+')'
                for raw, in db.execute(query,keys):
                    row = json.loads(raw)
                    counts = Counter()
                    for p, _, _, n in row['sightings']:
                        counts[sector(p)] += n
                    model.add_visit(counts, row.get('xp'))
    sources = ({d['id'] for d in atlas.drafts() if (d.get('terrain') or {}).get('ground') == area['id']}
               if manual else {s['id'] for s in sources})
    path = atlas.path.with_name('cavebot-reports-'+atlas.path.name)
    if not path.exists() or not sources:
        return model
    steps, fight_times = [], []
    refills = defaultdict(list)
    reference = defaultdict(list)
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'laps','lap_sightings'} <= tables:
            return model
        query = 'SELECT l.summary,s.sightings,l.events FROM laps l JOIN lap_sightings s ON s.id=l.id WHERE json_extract(l.summary,\'$.complete\')=1 AND json_extract(l.summary,\'$.draft_id\') IN (' + ','.join('?' for _ in sources) + ')'
        for summary, raw_sightings, raw_events in db.execute(query, tuple(sources)):
            counts = Counter()
            for row in json.loads(raw_sightings):
                counts[sector(row['point'])] += row['count']
            result = json.loads(summary)
            if not manual:
                model.add_visit(counts, result.get('xp'))
            if result.get('xp') and result.get('duration_ms',0)>0:
                reference[result.get('draft_id'),result.get('revision')].append((result['duration_ms']/1000,result['xp']))
            phase, fighting = 'travel', 0
            events = json.loads(raw_events)
            for cell, gap in observed_returns(events,model.slots).items():
                refills[cell].append(gap)
            if not counts or sum(n for p,n in counts.items() if p in model.contacts)*2 < sum(counts.values()):
                continue
            for a,b in zip(events,events[1:]):
                if a['kind'] in ('travel','lure','fight','blocked','manual'):
                    phase = a['kind']
                dt = (b['ms']-a['ms'])/1000
                if phase == 'fight':
                    fighting += dt
                p,q = a.get('position'),b.get('position')
                if phase == 'travel' and p and q and p[2]==q[2]:
                    gap = max(abs(p[0]-q[0]),abs(p[1]-q[1]))
                    if gap and .08 <= dt/gap <= 1:
                        steps.append(dt/gap)
            if fighting and result.get('observed_deaths'):
                fight_times.append(fighting/result['observed_deaths'])
    model.return_seconds = {cell:median(samples) for cell,samples in refills.items() if len(samples)>=3}
    if steps:
        model.step_s = median(steps)
    if fight_times:
        model.fight_s = median(fight_times)
    # Calibrate movement and yield on a replayable, unchanged reference sketch.
    # Whole-lap XP from a different island or a substantially larger route
    # must not be assigned to a small selected room.
    if reference and model.slots:
        eligible = [(d,reference[d['id'],d['navigation_revision']]) for d in atlas.drafts()
                    if len(reference.get((d['id'],d['navigation_revision']),()))>=3]
        for draft, laps in sorted(eligible,key=lambda item:-len(item[1])):
            authored = draft['points']
            if sum(sector(p) in selected for p in authored) < .85*len(authored):
                continue
            plan = atlas.plan(authored,atlas.with_recorded_ropes(observations),loop=True,links=draft['links'])
            path = plan['path']
            if (not plan['complete'] or not plan['loop_closed'] or len(path)<3
                    or path[0]!=path[-1] or sum(sector(p) in selected for p in path)<.85*len(path)):
                continue
            actions = {(tuple(t['origin']),tuple(t['exit'])) for t in plan['transitions']}
            predicted = model.estimate(path,actions)
            if not predicted['encounters_hour'] or not predicted['seconds']:
                continue
            factor = median(t for t,_ in laps)/predicted['seconds']
            if not .5 <= factor <= 2:
                continue
            model.step_s *= factor
            model.fight_s *= factor
            adjusted = model.estimate(path,actions)
            harvest = adjusted['encounters_hour']*adjusted['seconds']/3600
            if harvest:
                model.reference_xp_per_encounter = median(xp for _,xp in laps)/harvest
                model.reference_laps = len(laps)
            break
    return model


def return_tree(graph, start):
    incoming = defaultdict(list)
    for p, edges in graph.items():
        for q, cost, link in edges:
            incoming[q].append((p, cost, link))
    distance, follow = {start:0}, {}
    queue = [(0,start)]
    while queue:
        cost, q = heapq.heappop(queue)
        if cost != distance[q]:
            continue
        for p, weight, link in incoming[q]:
            new = cost+weight
            if new < distance.get(p, float('inf')):
                distance[p] = new
                follow[p] = (q,link)
                heapq.heappush(queue,(new,p))
    return distance, follow


def compare_routes(atlas, observations, ground, graph, goals, full_walk, full, model):
    from hunt_routes import shortest, simulated, varied_route
    from hunt_corridors import repeat_count
    start = full_walk[0][0]
    home_cost, home = return_tree(graph, start)
    pool = {}
    def add(walk):
        walk = walk.copy()
        p = walk[-1][0]
        while p != start:
            p, link = home[p]
            walk.append((p,link))
        if len(walk) < 3:
            return
        key = tuple(walk)
        if key not in pool:
            path = [p for p,_ in walk]
            passages = {(a,b) for (a,_),(b,action) in zip(walk,walk[1:]) if action}
            pool[key] = model.estimate(path, passages)
    # Sample the coverage sweep and independent excursions towards dense packs.
    # The reverse tree preserves direction and the exact action for each return.
    for portion in (.08,.18,.33,.5,.75):
        add(full_walk[:max(2, round(len(full_walk)*portion))])
    hotspots = sorted((p for p in goals if p != start and goals[p]>0),
                      key=lambda p:(-model.slots[sector(p)]/(20+home_cost[p]),-goals[p],p))
    for hub in hotspots[:4]:
        outward = [(start,None),*shortest(start,{hub},graph.__getitem__)]
        add(outward)
        nearby = set(hotspots)-{hub}
        here = hub
        for count in range(min(8,len(nearby))):
            leg = shortest(here,nearby,graph.__getitem__,goals)
            outward.extend(leg)
            here = outward[-1][0]
            nearby.difference_update(p for p,_ in leg)
            if count in (0,2,4,7) or not nearby:
                add(outward)
            if not nearby:
                break
    # A long hunt can contain a compact circuit covering its known packs
    # without sweeping every empty side room.
    if hotspots:
        nearby = set(hotspots)
        walk, here = [(start,None)], start
        milestones = {max(1,round(len(nearby)*fraction)) for fraction in (.25,.5,.75,1)}
        while nearby:
            leg = shortest(here,nearby,graph.__getitem__,goals)
            walk.extend(leg)
            here = walk[-1][0]
            previous = len(hotspots)-len(nearby)
            nearby.difference_update(p for p,_ in leg)
            if any(previous < mark <= len(hotspots)-len(nearby) for mark in milestones):
                add(walk)
    def estimate(route):
        actions = {(tuple(t['origin']),tuple(t['exit'])) for t in route['passages']}
        return model.estimate(route['path'],actions)
    full_metrics = estimate(full)
    # Validate only the best candidates; the shared sketch simulator can
    # substitute an equal-cost diagonal. Score the actual simulated path again.
    compact = min(RESPAWN_SECONDS, full_metrics['seconds']*.55)
    def short_score(m):
        return (m['encounters_hour'] > 0, m['seconds'] <= compact,
                m['encounters_hour'] if m['seconds'] <= compact else -m['seconds'], -m['seconds'])
    def yield_score(m):
        return (m['encounters_hour'], -abs(m['seconds']-RESPAWN_SECONDS), -m['seconds'])
    verified = [(full, full_metrics)]
    attempted = set()
    for score in (short_score, yield_score):
        found = 0
        for key in sorted(pool,key=lambda k:score(pool[k]),reverse=True):
            if key in attempted:
                continue
            attempted.add(key)
            try:
                route = simulated(atlas,observations,key,ground.excluded,ground.allowed)
            except ValueError:
                continue
            if route['path'][0]!=route['path'][-1] or len(route['path'])<3:
                continue
            route = varied_route(atlas,observations,ground,list(key),set(hotspots),route)
            route['covered'] = len(set(map(sector,route['path'])) & set(map(tuple,full['selected'])))
            verified.append((route,estimate(route)))
            found += 1
            if found >= 2:
                break
    variants = []
    for kind, (route,metrics) in [('short',max(verified,key=lambda v:short_score(v[1]))),
                                 ('respawn',max(verified,key=lambda v:yield_score(v[1]))),
                                 ('full',verified[0])]:
        variants.append(dict(kind=kind,metrics=dict(metrics,repeated_segments=repeat_count(route['path'])),
                             geometry={k:route[k] for k in ('points','links','path','covered','passages','transitions')}))
    for i, variant in enumerate(variants):
        for other in variants[:i]:
            if (other['geometry']['path'] == variant['geometry']['path'] and
                    other['geometry']['links'] == variant['geometry']['links']):
                variant['same_as'] = other.get('same_as',other['kind'])
                break
    return dict(variants=variants, visits=model.visits, respawn_seconds=RESPAWN_SECONDS,
                step_seconds=round(model.step_s,2),fight_seconds=round(model.fight_s,2),
                observed_return_sectors=len(model.return_seconds),reference_laps=model.reference_laps,
                view_radius=VIEW_RADIUS)
