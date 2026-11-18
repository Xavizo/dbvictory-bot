"""Reject playback away from the route, instead of skipping/repeating every node."""
MAX_START_DISTANCE = 8


def entry_position(node):
    return node.get("start", node.get("target")) if node.get("type") == "walk_steps" else node.get("target")


def route_entry(nodes, position, *, restart=False):
    if len(position) != 3 or position[0] <= 0 or position[1] <= 0:
        return None, "Player position is unknown. Wait for a current position before starting."
    candidates = []
    # Nearest-point entry is only for starting/resynchronizing playback.
    # Repeating a route must return to its beginning, never its nearest tail.
    for index, node in enumerate(nodes[:1] if restart else nodes):
        target = entry_position(node)
        if target and len(target) == 3 and target[2] == position[2]:
            distance = max(abs(target[0] - position[0]), abs(target[1] - position[1]))
            candidates.append((distance, index, target))
    if not candidates:
        if restart:
            return None, "Route beginning is on another floor or missing. Record a return path before looping."
        return None, f"Route has no points on floor {position[2]}. Move to the recorded route before starting."
    distance, index, target = min(candidates)
    if distance > MAX_START_DISTANCE:
        if restart:
            return None, f"Route beginning is too far away ({distance} tiles). Record a return path within {MAX_START_DISTANCE} tiles before looping."
        return None, f"Route is too far away ({distance} tiles). Move within {MAX_START_DISTANCE} tiles of {tuple(target)}."
    return index, None
