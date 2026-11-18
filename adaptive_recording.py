"""Find a representative observed circuit inside a hunting area."""
from collections import Counter
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


def _hunting_stretches(trace, zones, hunting_ground, observations):
    """Keep a short walk back after the last gain, cutting at gaps and long trips."""
    first = None
    travel = 0
    crossings = {}
    for i, point in enumerate(trace):
        here = tuple(point["pos"])
        prev = tuple(trace[i-1]["pos"]) if i else here
        distance = max(abs(here[0]-prev[0]), abs(here[1]-prev[1]))
        ordinary = here[2] == prev[2] and distance <= 3
        if not ordinary and (prev, here) not in crossings:
            pair = [list(prev), list(here)]
            crossings[prev, here] = bool(trace_links(pair, observations, pair))
        travel = 0 if zones[i] in hunting_ground else travel + (distance if ordinary else 1)
        if point.get("break_before") or not (ordinary or crossings[prev, here]) or travel > MAX_TRAVEL_WITHOUT_XP:
            if first is not None:
                yield first, i - 1
            first = None
            travel = 0
        if point.get("xp_gain", 0) > 0:
            if first is None:
                first = i
            travel = 0
    if first is not None:
        yield first, len(trace) - 1


def _best_circuit(trace, zones, first, last):
    """Compare real passes, allowing a different tile in the same room."""
    xp_visits, visits = Counter(), []
    for zone, indices in groupby(range(first, last+1), zones.__getitem__):
        run = list(indices)
        hits = [i for i in run if trace[i].get("xp_gain", 0)]
        visits.append((zone, hits[0] if hits else run[0], hits[-1] if hits else run[-1]))
        if hits:
            xp_visits[zone] += 1
    if len(xp_visits) < 2:
        return len(xp_visits), None
    # Evaluate a small set of frequently fought-in areas as possible entries.
    # Consecutive visits and pairs of passes also cover alternating side rooms.
    best = None
    for anchor, _ in xp_visits.most_common(8):
        previous = []
        for zone, enter, leave in visits:
            if zone != anchor:
                continue
            for start in previous:
                if enter-start < 5:
                    continue
                segment = range(start, enter+1)
                origin = trace[start]["pos"]
                if not any(trace[i]["pos"][2] != origin[2] or
                           max(abs(trace[i]["pos"][k]-origin[k]) for k in (0, 1)) >= ZONE_SIZE
                           for i in segment):
                    continue
                fought = {zones[i] for i in segment if trace[i].get("xp_gain", 0)}
                if len(fought) < 2:
                    continue
                repeated = sum(xp_visits[z] > 1 for z in fought)
                # Prefer coverage across a real return. A short local turn
                # cannot outrank a circuit through more hunting areas.
                score = (len(fought), repeated, -(enter-start))
                if best is None or score > best[0]:
                    best = score, start, enter
            previous = [*previous[-1:], leave]
    return len(xp_visits), best


def _hunt_evidence(trace, observations):
    """Count fulfilled milestones and select the same circuit for preview and stop."""
    zones = [(x//ZONE_SIZE, y//ZONE_SIZE, z) for x, y, z in (pt["pos"] for pt in trace)]
    xp_zones = {zone for zone, sample in zip(zones, trace) if sample.get("xp_gain", 0)}
    hunting_ground = {(x+dx, y+dy, z) for x,y,z in xp_zones
                      for dx in (-1,0,1) for dy in (-1,0,1)}
    best, largest_stretch = None, 0
    for first, last in _hunting_stretches(trace, zones, hunting_ground, observations):
        areas, candidate = _best_circuit(trace, zones, first, last)
        if candidate and (best is None or candidate[0] > best[0]):
            best = candidate
        largest_stretch = max(largest_stretch, areas)
    # A tiny loop inside a long hunt is not a representative proposal.
    if best and (2 * best[0][0] < largest_stretch or 3 * best[0][0] < len(xp_zones)):
        best = None
    status = {"stage": min(2, largest_stretch), "areas": 0, "total_areas": len(xp_zones), "revisited": 0}
    if best:
        _, start, end = best
        # XP immediately before departure or after arrival in the same zone
        # is part of the anchor visit, not an independent second pass.
        left, right = start, end
        while left and zones[left-1] == zones[start]:
            left -= 1
        while right+1 < len(zones) and zones[right+1] == zones[end]:
            right += 1
        inside, outside = set(), set()
        for i, (zone, sample) in enumerate(zip(zones, trace)):
            if sample.get("xp_gain", 0):
                if start <= i <= end:
                    inside.add(zone)
                elif i < left or i > right:
                    outside.add(zone)
        revisited = len(inside & outside)
        corroborated = revisited >= 2 and revisited * 3 >= len(inside)
        status.update(stage=4 if corroborated else 3, areas=len(inside), revisited=revisited)
    return status, best


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
            status, _ = _hunt_evidence(trace, observations)
            progress.update(status)
        except Exception:
            if progress["stage"] is not None:
                logging.getLogger(__name__).exception("Adaptive recording progress unavailable")
            progress["stage"] = None


def hunting_excerpt(recording, observations):
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
    status, best = _hunt_evidence(trace, observations)
    if best is None:
        raise ValueError("Brakuje obiegu obejmującego odwiedzone miejsca z XP. Wróć przez większą część expowiska. Pełny ślad pozostaje w nagraniu.")
    score, start, end = best
    selected = trace[start:end+1]
    first_point = {k: v for k, v in selected[0].items() if k != "break_before"}
    lo, hi = first_point["t"], selected[-1].get("last_t", selected[-1]["t"])
    excerpt = {**recording, "observed_path": [first_point, *selected[1:]],
               "start_position": first_point["pos"],
               "waypoints": [wp for wp in recording.get("waypoints", []) if lo <= wp.get("t", -1) <= hi]}
    evidence = {"areas": score[0], "total_areas": status["total_areas"], "revisited": status["revisited"],
                "samples": len(selected), "total_samples": len(trace),
                "xp": sum(pt.get("xp_gain", 0) for pt in selected)}
    return excerpt, evidence
