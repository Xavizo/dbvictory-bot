"""Corroborate a passive rope observation with an uninterrupted recorded landing."""
from bisect import bisect_right
from datetime import datetime, timezone
from math import isfinite

from navigation_tools import tool_kind
from transition_learning import TransitionLearner, position


def recorded_rope_edges(recording, observations):
    candidates = [
        row for row in observations
        if row.get("evidence") == "review" and row.get("reason") == "sample_gap"
        and tool_kind(row) == "rope"
    ]
    if not candidates or recording.get("version") != 2 or recording.get("trace_limited"):
        return []
    if recording.get("position_source") not in {"client", "server"}:
        return []
    # The atlas recorder embeds its UTC start in the name. Old recordings
    # have no scope field. Tie the match to this learner's actual observation.
    try:
        start = datetime.strptime(recording["name"], "atlas_%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp()
    except (KeyError, ValueError, TypeError):
        return []
    trace = recording.get("observed_path") or []
    events = recording.get("waypoints") or []
    if len(trace) > 20000 or len(events) > 20000:
        return []
    times = [sample.get("t") for sample in trace]
    if any(type(t) not in (int, float) or not isfinite(t) for t in times):
        return []
    if any(a >= b for a, b in zip(times, times[1:])):
        return []

    # A second request in the same observation window makes attribution ambiguous.
    requests = {}
    for event in events:
        if event.get("type") in {"tile_transform_item", "floor_change"}:
            continue
        t = event.get("t")
        if type(t) not in (int, float) or not isfinite(t):
            return []
        index = bisect_right(times, t)
        if 0 < index < len(trace):
            requests.setdefault(index, []).append(event)

    confirmed = {}
    for index, group in requests.items():
        if len(group) != 1 or group[0].get("type") != "use_item_ex":
            continue
        before, after = trace[index-1:index+1]
        if after.get("break_before") or times[index] - times[index-1] > 1.5:
            continue
        use = group[0]
        origin, dest = before.get("pos"), after.get("pos")
        if min(use["t"] - times[index-1], times[index] - use["t"]) <= 0.05:
            continue  # Action timestamps are rounded to tenths of a second.
        if not position(origin) or not position(dest) or use.get("player_pos") != origin:
            continue
        entry = [use.get(k) for k in ("to_x", "to_y", "to_z")]
        for row in candidates:
            seen = (row.get("first_seen"), row.get("last_seen"))
            if not any(type(t) in (float, int) and abs(t - start - times[index]) <= 4 for t in seen):
                continue
            if (row["origin"] != origin or row["entry"] != entry or row["exit"] != dest
                    or row["tool_id"] != use.get("item_id") or row["item_id"] != use.get("target_item_id")):
                continue
            edge = dict(row, evidence="observed", reason="matched_command", count=1)
            edge["id"] = TransitionLearner._key(edge)
            confirmed[edge["id"]] = edge
    return list(confirmed.values())
