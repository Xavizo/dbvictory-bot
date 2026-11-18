"""Corroborate a passive rope observation with an uninterrupted recorded landing."""
from bisect import bisect_right
from datetime import datetime, timezone
from math import isfinite

from navigation_tools import tool_kind
from transition_learning import TransitionLearner, position


def recorded_rope_edges(recording, observations, scope=None, tiles=None):
    candidates = [
        row for row in observations
        if row.get("evidence") == "review" and row.get("reason") == "sample_gap"
        and tool_kind(row) == "rope"
    ]
    scoped = bool(scope and scope == recording.get("atlas_scope") and tiles is not None)
    if not (candidates or scoped) or recording.get("version") != 2:
        return []
    if recording.get("position_source") not in {"client", "server"}:
        return []
    # The atlas recorder embeds its UTC start in the name. Old recordings
    # have no scope field. Tie the match to this learner's actual observation.
    if recording.get("atlas_scope") is not None:
        if scope != recording["atlas_scope"]:
            return []
        start = recording.get("started_at")
        if type(start) not in (float, int) or not isfinite(start):
            return []
    else:
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
    if any(a > b for a, b in zip(times, times[1:])):
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
        if 0 < index < len(trace) and times[index] - t <= 1.5:
            requests.setdefault(index, []).append(event)

    confirmed = {}
    for index, group in requests.items():
        if len(group) != 1 or group[0].get("type") != "use_item_ex":
            continue
        before, after = trace[index-1:index+1]
        if index>=2 and times[index-2]==times[index-1] or index+1<len(times) and times[index+1]==times[index]:
            continue
        last_seen = before.get("last_t", times[index-1])
        if (type(last_seen) not in (float, int) or not times[index-1] <= last_seen <= times[index]
                or after.get("break_before") or times[index] - last_seen > 1.5
                or times[index] - group[0]["t"] > 1.5):
            continue
        use = group[0]
        origin, dest = before.get("pos"), after.get("pos")
        rounding = 0.0005 if recording.get("action_time_precision_ms") == 1 else 0.05
        if min(use["t"] - times[index-1], times[index] - use["t"]) <= rounding:
            continue
        if not position(origin) or not position(dest) or use.get("player_pos") != origin:
            continue
        entry = [use.get(k) for k in ("to_x", "to_y", "to_z")]
        matched = False
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
            matched = True
        if matched or not scoped or not position(entry):
            continue
        # For new, scoped recordings the object itself provides an independent
        # check if the passive learner never saw the command. Limit this to a
        # known rope spot, the exact targeted item, and a local upward landing.
        item = use.get("target_item_id")
        edge = dict(origin=origin, entry=entry, exit=dest, action="use_with",
                    item_id=item, tool_id=use.get("item_id"), direction="up",
                    count=1, evidence="observed", reason="matched_command",
                    first_seen=start+times[index], last_seen=start+times[index])
        if tool_kind(edge) != "rope" or max(abs(entry[k]-dest[k]) for k in (0, 1)) > 1:
            continue
        tile = tiles.get(tuple(entry), {})
        if not any(f.get("kind") == "rope_spot" and f.get("item_id") == item for f in tile.get("navigation", [])):
            continue
        edge["entry_items"] = [i["id"] for i in tile.get("items", [])]
        if item in edge["entry_items"]:
            edge["id"] = TransitionLearner._key(edge)
            confirmed[edge["id"]] = edge
    return list(confirmed.values())
