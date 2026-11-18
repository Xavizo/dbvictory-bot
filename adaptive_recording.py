"""Find a representative observed circuit inside a hunting area."""
from collections import Counter
from itertools import groupby
from math import isfinite

from transition_learning import position


ZONE_SIZE = 5
# Split trips with two or more screens of walking between XP gains. Distance
# is travelled distance, so leaving and returning along the same road counts.
MAX_TRAVEL_WITHOUT_XP = 36


def stays_near_trace(path, trace):
    """Validate the atlas walk, including the return, on each observed floor."""
    visited = {tuple(point["pos"]) for point in trace}
    return all((x, y, z) in visited or any((x+dx, y+dy, z) in visited
               for dx in range(-2, 3) for dy in range(-2, 3)) for x, y, z in path)


def _hunting_stretches(trace, crossings):
    """Yield contiguous index ranges from the first to the last XP evidence."""
    first = last = None
    travel = 0
    for i, point in enumerate(trace):
        here = tuple(point["pos"])
        prev = tuple(trace[i-1]["pos"]) if i else here
        distance = max(abs(here[0]-prev[0]), abs(here[1]-prev[1]))
        ordinary = here[2] == prev[2] and distance <= 3
        crossing = (prev, here) in crossings
        if point.get("break_before") or not (ordinary or crossing):
            if first is not None and last > first:
                yield first, last
            first = last = None
            travel = 0
        travel += distance if ordinary else 1
        if point.get("xp_gain", 0) > 0:
            if first is not None and travel > MAX_TRAVEL_WITHOUT_XP:
                if last > first:
                    yield first, last
                first = None
            if first is None:
                first = i
            last = i
            travel = 0
    if first is not None and last > first:
        yield first, last


def _circuits(trace, zones, first, last):
    """Compare real passes, allowing a different tile in the same room."""
    xp_visits = Counter()
    visits = []
    for zone, indices in groupby(range(first, last+1), zones.__getitem__):
        run = list(indices)
        hits = [i for i in run if trace[i].get("xp_gain", 0)]
        visits.append((zone, hits[0] if hits else run[0], hits[-1] if hits else run[-1]))
        if hits:
            xp_visits[zone] += 1
    if len(xp_visits) < 2:
        return
    # Evaluate a small set of frequently fought-in areas as possible entries.
    # Consecutive visits and pairs of passes also cover alternating side rooms.
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
                # Repeated hunting areas outrank a single excursion with
                # scattered XP. Break ties by coverage, then by pass length.
                score = (repeated, len(fought), -(enter-start))
                yield score, start, enter
            previous = [*previous[-1:], leave]


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
    crossings = {(tuple(start), tuple(row["exit"])) for row in observations
                 if row.get("evidence") == "observed" and position(row.get("exit"))
                 for start in (row.get("entry"), row.get("origin")) if position(start)}
    zones = [(x//ZONE_SIZE, y//ZONE_SIZE, z) for x, y, z in (pt["pos"] for pt in trace)]
    candidates = (candidate for first, last in _hunting_stretches(trace, crossings)
                  for candidate in _circuits(trace, zones, first, last))
    best = max(candidates, default=None, key=lambda row: row[0])
    if best is None:
        raise ValueError("Brakuje powrotu do odwiedzonej okolicy z XP. Odwiedź ponownie sale, w których walczysz. Pełny ślad pozostaje w nagraniu.")
    score, start, end = best
    selected = trace[start:end+1]
    first_point = {k: v for k, v in selected[0].items() if k != "break_before"}
    lo, hi = first_point["t"], selected[-1].get("last_t", selected[-1]["t"])
    excerpt = {**recording, "observed_path": [first_point, *selected[1:]],
               "start_position": first_point["pos"],
               "waypoints": [wp for wp in recording.get("waypoints", []) if lo <= wp.get("t", -1) <= hi]}
    evidence = {"areas": score[1], "samples": len(selected), "total_samples": len(trace),
                "xp": sum(pt.get("xp_gain", 0) for pt in selected)}
    return excerpt, evidence
