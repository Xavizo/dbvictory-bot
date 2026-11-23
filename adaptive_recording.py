"""Find a representative observed circuit inside a hunting area."""
from collections import Counter, deque
from bisect import bisect_right
from itertools import groupby
import logging
from math import isfinite

from map_atlas import trace_links
from transition_learning import position


ZONE_SIZE = 5
# Split sustained travel outside the neighbourhood of any recorded XP.
MAX_TRAVEL_WITHOUT_XP = 36


def stays_near_trace(path, trace):
    """Validate the atlas walk, including the return, on each observed floor."""
    visited = {tuple(point["pos"]) for point in trace}
    return all((x, y, z) in visited or any((x+dx, y+dy, z) in visited
               for dx in range(-2, 3) for dy in range(-2, 3)) for x, y, z in path)


def sector(pos):
    x, y, z = pos
    return x // ZONE_SIZE, y // ZONE_SIZE, z


def _hunting_stretches(trace, zones, marks, ground, observations):
    """Trim transit at the ends; never join a gap or an unobserved floor change."""
    boundary = 0
    first = last = None
    travel = 0
    crossings = {}

    def bounds(end):
        start = boundary if all(z in ground for z in zones[boundary:first]) else first
        tail = end-1
        if any(z not in ground for z in zones[last+1:end]):
            core = set(zones[start:last+1])
            tail = last
            for j in range(last+1, end):
                if zones[j] not in ground:
                    break
                if zones[j] in core:
                    tail = j
        return start, tail

    for i, sample in enumerate(trace):
        here = tuple(sample["pos"])
        prev = tuple(trace[i-1]["pos"]) if i else here
        distance = max(abs(here[0]-prev[0]), abs(here[1]-prev[1]))
        ordinary = here[2] == prev[2] and distance <= 3
        if not ordinary and (prev, here) not in crossings:
            pair = [list(prev), list(here)]
            crossings[prev, here] = bool(trace_links(pair, observations, pair))
        travel = 0 if zones[i] in ground else travel + (distance if ordinary else 1)
        if sample.get("break_before") or not (ordinary or crossings[prev, here]) or travel > MAX_TRAVEL_WITHOUT_XP:
            if first is not None:
                yield bounds(i)
            first = last = None
            boundary, travel = i, 0
        if i in marks:
            if first is None:
                first = i
            last = i
    if first is not None:
        yield bounds(len(trace))


def _return_stage(trace, zones, marks, first, last):
    """Check movement away and back; kills may all occur at one gathering point."""
    fought = Counter()
    for zone, run in groupby(range(first, last+1), zones.__getitem__):
        if any(i in marks for i in run):
            fought[zone] += 1
    stage = min(2, len(fought))
    if not fought:
        return stage
    departures, returns = {}, Counter()
    repeated_fights = sum(count >= 2 for count in fought.values())
    for zone, run in groupby(range(first,last+1), zones.__getitem__):
        visit = list(run)
        if zone not in fought:
            continue
        departure = departures.get(zone)
        departures[zone] = visit[-1]
        if departure is None or visit[0]-departure < 5:
            continue
        origin = trace[departure]["pos"]
        away = any(pos[2]!=origin[2] or max(abs(pos[k]-origin[k]) for k in (0,1))>=ZONE_SIZE
                   for pos in (trace[i]["pos"] for i in range(departure+1,visit[0])))
        if away and (len(fought)>=2 or fought[zone]>=2):
            returns[zone] += 1
            stage = 3
            if returns[zone]>=2 or repeated_fights>=2:
                return 4
    return stage


def _hunt_evidence(trace, observations, encounters=()):
    zones = [sector(pt["pos"]) for pt in trace]
    xp = {i for i, pt in enumerate(trace) if pt.get("xp_gain", 0)}
    marks = set(xp)
    for e in encounters:
        if (e.get("outcome") in ("target_xp", "nearby_xp") and type(e.get("sample")) is int
                and 0 <= e["sample"] < len(trace) and trace[e["sample"]]["pos"] == e.get("player_pos")):
            marks.add(e["sample"])
    ground = {(x+dx,y+dy,z) for x,y,z in {zones[i] for i in marks}
              for dx in (-1,0,1) for dy in (-1,0,1)}
    spans = []
    required = set()
    for a,b in _hunting_stretches(trace,zones,marks,ground,observations):
        areas = set(zones[a:b+1])
        required.update(areas)
        spans.append((_return_stage(trace,zones,marks,a,b), areas, a, b))
    stage, areas, start, end = max(spans, key=lambda s:(s[1] == required, s[0], len(s[1])),
                                   default=(0,set(),0,0))
    missing = len(required-areas)
    reward_visits = Counter(zone for zone, run in groupby(range(start,end+1), zones.__getitem__)
                            if any(i in xp for i in run)) if trace else Counter()
    status = dict(stage=min(stage,2) if missing else stage, sectors=len(required), missing_sectors=missing,
                  revisited=sum(count>=2 for count in reward_visits.values()),
                  total_areas=len({zones[i] for i in xp}), areas=len({zones[i] for i in xp if start<=i<=end}))
    return status, (start,end) if status["stage"]>=3 else None


def _coverage_walk(trace, first, last):
    """Keep a visit in each sector. Erase detours back to the exact same tile
    between first visits. Use the last departure at a splice for its actions.
    """
    occurrences = {}
    last_visit = {}
    for i in range(first, last+1):
        pos = tuple(trace[i]["pos"])
        occurrences.setdefault(pos, []).append(i)
        last_visit[sector(pos)] = i
    deadlines = sorted((i, zone) for zone,i in last_visit.items())
    seen, kept = set(), []
    deadline = 0
    i = first
    while i <= last:
        pos = tuple(trace[i]["pos"])
        seen.add(sector(pos))
        while deadline < len(deadlines) and deadlines[deadline][1] in seen:
            deadline += 1
        limit = deadlines[deadline][0] if deadline < len(deadlines) else last
        same = occurrences[pos]
        departure = same[bisect_right(same, limit)-1]
        kept.append(i)
        if departure > i:
            kept.append(departure)
        i = departure+1
    return kept


def _covering_circuit(trace, first, last, contacts, ordinary_tiles, unavailable):
    """Cover sectors via directed steps actually taken. A recent encounter
    breaks ties between equally near unvisited sectors. Close on observed edges.
    """
    edges = {}
    for i in range(first,last):
        a,b = tuple(trace[i]["pos"]),tuple(trace[i+1]["pos"])
        if a != b and (a,b) not in unavailable:
            edges.setdefault(a,{})[b] = i
            if (a in ordinary_tiles and b in ordinary_tiles and a[2] == b[2]
                    and max(abs(a[k]-b[k]) for k in (0,1)) == 1):
                edges.setdefault(b,{}).setdefault(a,~i)
    start = tuple(trace[first]["pos"])
    remaining = {sector(row["pos"]) for row in trace[first:last+1]} - {sector(start)}
    here, walk = start, []
    while remaining or here != start:
        queue = deque([here])
        parent = {here: None}
        goals = []
        while queue and not goals:
            for _ in range(len(queue)):
                p = queue.popleft()
                if sector(p) in remaining or (not remaining and p == start):
                    goals.append(p)
                    continue
                for q in edges.get(p,{}):
                    if q not in parent:
                        parent[q] = p
                        queue.append(q)
        if not goals:
            # The recording can start on a one-way approach. The atlas checks
            # a separate return leg once the last hunting sector is covered.
            return walk if not remaining else []
        goal = max(goals,key=lambda p:contacts.get(sector(p),-1))
        leg = []
        p = goal
        while p != here:
            a = parent[p]
            leg.append(edges[a][p])
            p = a
        walk.extend(reversed(leg))
        remaining.discard(sector(goal))
        here = goal
        if len(walk) > last-first:
            return []
    return walk


def _replay_edges(recording, edges):
    """Retain action timing relative to each original leg after reordering."""
    trace = recording["observed_path"]
    times = [p["t"] for p in trace]
    actions = {}
    for wp in recording.get("waypoints", []):
        idx = bisect_right(times, wp.get("t",-1))-1
        actions.setdefault(idx,[]).append(wp)
    samples, waypoints = [], []
    elapsed = 0.
    for edge in edges:
        i = edge if edge >= 0 else ~edge
        a,b = (i,i+1) if edge >= 0 else (i+1,i)
        samples.append({"pos":trace[a]["pos"], "t":elapsed})
        if edge >= 0:
            waypoints.extend({**wp,"t":elapsed + (wp["t"]-times[i])} for wp in actions.get(i,()))
        elapsed += max(.001,times[i+1]-times[i])
    samples.append({"pos":trace[b]["pos"],"t":elapsed})
    return samples, waypoints


def watch_hunt_evidence(recording, monitor, stop, progress):
    """Publish only a milestone, off the position sampler and dashboard push loop."""
    while not stop.wait(4):
        try:
            atlas = monitor.atlas
            if atlas is None or atlas.scope != recording["atlas_scope"]:
                progress["stage"] = None
                continue
            trace = list(recording["observed_path"])
            if trace:
                # Only the last sample can still receive XP while standing.
                trace[-1] = trace[-1].copy()
            learner = getattr(monitor, "learner", None)
            observations = list(learner.records.values()) if learner and learner.scope == atlas.scope else []
            if trace and any(p["pos"][2] != trace[0]["pos"][2] for p in trace):
                snapshot = dict(recording, observed_path=trace, waypoints=list(recording["waypoints"]))
                observations = atlas.with_recorded_ropes(observations) + atlas.infer_recorded_ropes(snapshot, observations)
                observations = atlas.recording_step_observations(observations)
            status, _ = _hunt_evidence(trace, observations, list(recording.get("encounters", ())))
            progress.update(status)
        except Exception:
            if progress["stage"] is not None:
                logging.getLogger(__name__).exception("Adaptive recording progress unavailable")
            progress["stage"] = None


def hunting_excerpt(recording, observations, *, reorder=True, ordinary_tiles=(), unavailable=()):
    """Return a derived recording; keep the original trace and times intact."""
    trace = recording.get("observed_path")
    if not isinstance(trace, list) or not 2 <= len(trace) <= 20000:
        raise ValueError("Do rozpoznania expowiska potrzebny jest zapis pozycji z nagrania.")
    for point in trace:
        if (not isinstance(point, dict) or not position(point.get("pos"))
                or type(point.get("t")) not in (float, int) or not isfinite(point["t"])
                or type(point.get("xp_gain", 0)) is not int or point.get("xp_gain", 0) < 0):
            raise ValueError("Nagranie zawiera nieprawidłową próbkę ruchu lub XP.")
    if not any(point.get("xp_gain", 0) for point in trace):
        raise ValueError("Brak przyrostu XP w nagraniu. Włącz nagrywanie adaptacyjne podczas walki na expowisku.")
    encounters = [e for e in recording.get("encounters", []) if isinstance(e, dict)
                  and type(e.get("sample")) is int and 0 <= e["sample"] < len(trace)
                  and isinstance(e.get("name"), str) and e["name"] and position(e.get("first_pos"))
                  and trace[e["sample"]]["pos"] == e.get("player_pos")
                  and e.get("outcome") in ("seen","death","nearby_xp","target_xp")]
    status, best = _hunt_evidence(trace, observations, encounters)
    if best is None:
        if status["missing_sectors"]:
            raise ValueError(f'Nagranie ma rozłączne odcinki. Brakuje połączenia z {status["missing_sectors"]} sektorami. Pełny ślad pozostaje dostępny.')
        raise ValueError("Brakuje obiegu obejmującego odwiedzone miejsca z XP. Wróć przez większą część expowiska. Pełny ślad pozostaje w nagraniu.")
    start, end = best
    indices = _coverage_walk(trace, start, end)
    selected = [trace[i] for i in indices]
    selected_indices = set(indices)
    times = [p["t"] for p in trace]
    hi = selected[-1].get("last_t", selected[-1]["t"])
    waypoints = [wp for wp in recording.get("waypoints", [])
                 if wp.get("t", -1) <= hi and bisect_right(times, wp.get("t", -1))-1 in selected_indices]
    contacts = {sector(e["player_pos"]): e["sample"] for e in encounters
                if e.get("outcome") in ("target_xp","nearby_xp") and start <= e["sample"] <= end}
    edges = _covering_circuit(trace,start,end,contacts,ordinary_tiles,unavailable) if reorder else []
    reordered = bool(edges and len(edges)+1 < len(selected))
    if reordered:
        selected, waypoints = _replay_edges(recording,edges)
    first_point = {k: v for k, v in selected[0].items() if k != "break_before"}
    anchors = {}
    for sample in selected:
        anchors.setdefault(sector(sample["pos"]), sample["pos"])
    excerpt = {**recording, "observed_path": [first_point, *selected[1:]],
               "start_position": first_point["pos"],
               "coverage_anchors": list(anchors.values()),
               "waypoints": waypoints}
    species = {}
    last_samples = {}
    for event in encounters:
        if not start <= event["sample"] <= end:
            continue
        row = species.setdefault(event["name"], dict(name=event["name"], seen=0, target_xp=0, nearby_xp=0))
        row["seen"] += 1
        sample = event.get("last_sample")
        if type(sample) is int and event["sample"] <= sample <= end and position(event.get("last_pos")):
            point = event["last_pos"]
        else:
            sample, point = event["sample"], event["first_pos"]
        if sample >= last_samples.get(event["name"], -1):
            row["last_seen"] = point
            last_samples[event["name"]] = sample
        if event["outcome"] in ("target_xp", "nearby_xp"):
            row[event["outcome"]] += 1
    evidence = {**status, "reordered":reordered, "samples": len(selected), "total_samples": len(trace),
                "species": sorted(species.values(), key=lambda row: -row["seen"]),
                "xp": sum(pt.get("xp_gain", 0) for pt in trace[start:end+1])}
    return excerpt, evidence
