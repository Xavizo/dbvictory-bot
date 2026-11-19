"""Passive pursuit evidence from fresh creature positions, never screen counts.

The client does not expose a monster's aggro target. Two separate player steps
away, each followed by a monster step closing that gap, are our conservative
movement-based confirmation. No probe commands are sent to the game.
"""
from collections import deque
from combat_reachability import ordinary_terrain


def walking_distances(terrain, start):
    """Shortest same-floor walks through known terrain, including diagonals."""
    distances = {start: 0}
    queue = deque([start])
    while queue:
        point = queue.popleft()
        x, y, z = point
        for dx, dy in ((0,1),(1,0),(0,-1),(-1,0),(1,1),(-1,1),(1,-1),(-1,-1)):
            neighbor = (x+dx, y+dy, z)
            if neighbor in terrain and neighbor not in distances:
                distances[neighbor] = distances[point] + 1
                queue.append(neighbor)
    return distances


def group_needs_time(cells, position, next_point, members, limit):
    """Let a lagging member catch up before a step takes us farther away."""
    if not members or not next_point or gap(position, next_point) != 1 or position[2] != next_point[2]:
        return False
    terrain = {(c['x'], c['y'], c['z']) for c in cells if ordinary_terrain(c)}
    origin, destination = tuple(position), tuple(next_point)
    if destination not in terrain:
        # A route obstruction is handled by navigation, without holding combat.
        return any(gap(origin, member) >= limit and gap(destination, member) > gap(origin, member)
                   for member in members)
    current = walking_distances(terrain, origin)
    following = walking_distances(terrain, destination)
    return any(member in current and member in following and
               current[member] >= limit and following[member] > current[member]
               for member in members)


NEIGHBORS = ((0,1),(1,0),(0,-1),(-1,0),(1,1),(-1,1),(1,-1),(-1,-1))
MAX_PLACEMENT_STEPS = 3


def melee_capacity(terrain, center, group):
    """Count distinct melee slots a pack can reach before its members stop in melee."""
    x,y,z = center
    ring = {(x+dx,y+dy,z) for dx,dy in NEIGHBORS} & terrain
    pinned = ring & group
    slots = ring - pinned
    if not slots or group == pinned:
        return len(pinned)
    # A monster approaching from behind cannot walk through the player or
    # through another melee slot where it would already have stopped to fight.
    outside = terrain - ring - {center}
    waiting = group - pinned
    choices = []
    while waiting:
        start = min(waiting)
        if start not in outside:
            waiting.remove(start)
            continue
        component = walking_distances(outside, start).keys()
        members = waiting.intersection(component)
        accessible = [slot for slot in sorted(slots) if any(
            (slot[0]+dx, slot[1]+dy, z) in component for dx,dy in NEIGHBORS)]
        choices.extend([accessible] * min(len(members), len(accessible)))
        waiting -= members
    # Different sides of a doorway can compete for the same diagonal slot.
    owners = {}
    def assign(member, seen):
        for slot in choices[member]:
            if slot not in seen:
                seen.add(slot)
                if slot not in owners or assign(owners[slot], seen):
                    owners[slot] = member
                    return True
        return False
    for member in range(len(choices)):
        assign(member, set())
    return len(pinned) + len(owners)


def queued_pack_step(terrain, origin, target, group, max_lead):
    """One space-making step when the rear is physically queued behind the pack."""
    before = walking_distances(terrain, origin)
    after = walking_distances(terrain, target)
    if any(m not in before or m not in after for m in group):
        return False
    lagging = [m for m in group if after[m] > max(max_lead,before[m])]
    if not lagging or any(after[m] > max_lead+2 for m in lagging):
        return False
    for x,y,z in lagging:
        for dx,dy in NEIGHBORS:
            p = x+dx,y+dy,z
            if p != origin and p not in group and before.get(p,float('inf')) < before[x,y,z]:
                return False
    return melee_capacity(terrain,target,group) > melee_capacity(terrain,origin,group)


def open_sides(terrain, center):
    x, y, z = center
    return sum((x+dx, y+dy, z) in terrain for dx,dy in NEIGHBORS)


def better_pack_tile(current_capacity, current_sides, capacity, sides, steps):
    # If the same number can approach either tile, only a single step
    # may be spent gaining more space around the player.
    return sides >= current_sides and (capacity > current_capacity or (
        steps == 1 and capacity == current_capacity and sides > current_sides))


def pack_stopping_path(space, vacant, route, group, other, max_lead, remaining, visited, allow_queue_step):
    origin = tuple(route[0])
    terrain = space - other
    current = melee_capacity(terrain, origin, group)
    # A monster occupies a tile now, but does not turn it into a permanent wall.
    current_sides = open_sides(space, origin)
    all_fit = current >= min(8, len(group))
    if not remaining or (all_fit and current_sides == 8):
        return ()
    distances = [walking_distances(terrain, p) for p in sorted(group)]
    if any(origin not in d for d in distances):
        return ()
    queue = deque([(origin,)])
    seen = set(visited) | {origin}
    best_rank, chosen = (), ()
    route_prefix = set(map(tuple,route[1:]))
    while queue:
        path = queue.popleft()
        x,y,z = path[-1]
        for dx,dy in NEIGHBORS:
            p = (x+dx,y+dy,z)
            if p in seen or p not in vacant or p in group or p in other:
                continue
            stretches = any(d.get(p, float('inf')) > max(max_lead, d[path[-1]]) for d in distances)
            if stretches and not (allow_queue_step and len(path)==1 and queued_pack_step(terrain,origin,p,group,max_lead)):
                continue
            if any(gap(p,m) <= 2 and gap(p,m) < gap(origin,m) for m in other):
                continue
            seen.add(p)
            capacity = melee_capacity(terrain, p, group)
            if capacity < current:
                continue
            steps = len(path)
            sides = open_sides(space, p)
            if better_pack_tile(current, current_sides, capacity, sides, steps):
                rank = (capacity, sides, -steps, sum(gap(p,m)<=1 for m in group), p in route_prefix,
                        -sum(d[p] for d in distances))
                if rank > best_rank:
                    best_rank, chosen = rank, (*path,p)
            if steps < remaining and not stretches and not all_fit:
                queue.append((*path,p))
    return chosen


def lure_stopping_path(cells, route, edges, group, other_monsters, max_lead=3, *,
                       goal=None, remaining=MAX_PLACEMENT_STEPS, visited=(), allow_queue_step=True):
    """Choose a bounded local improvement for a gathered group on ordinary terrain."""
    if len(group) < 2 or len(route) < 2:
        return ()
    origin = tuple(route[0])
    group = set(map(tuple, group))
    if (goal is None or goal < 8) and all(gap(origin, p) <= 1 for p in group):
        return ()
    other_monsters = set(map(tuple, other_monsters))
    full_pack = goal is not None and goal >= 8
    terrain, vacant = placement_terrain(cells, origin, edges, group | other_monsters if full_pack else group)
    if not group <= terrain or origin not in terrain:
        return ()
    if full_pack:
        return pack_stopping_path(terrain, vacant, route, group, other_monsters, max_lead, remaining, visited, allow_queue_step)

    def access_score(center):
        # Evaluate the same known room with the player blocking the chosen tile.
        # A group stranded on the other side of that tile must not be left behind.
        reachable = walking_distances(terrain - {center}, next(iter(group))).keys()
        if not group <= reachable:
            return 0, 0
        slots = sum(gap(p, center) == 1 for p in reachable)
        room = sum(gap(p, center) == 2 for p in reachable)
        return slots, room

    current, _ = access_score(origin)
    desired = min(8, max(6, len(group) + 2))
    if current >= desired:
        return ()
    initial_distances = walking_distances(terrain, origin)

    def safe_step(point):
        if point not in vacant or point in group or point in other_monsters:
            return False
        if any(gap(point, m) <= 2 and gap(point, m) < gap(origin, m) for m in other_monsters):
            return False
        distances = walking_distances(terrain, point)
        return all(m in initial_distances and distances.get(m, float('inf')) <= max(max_lead, initial_distances[m])
                   for m in group)

    options = []
    path = [origin]
    for point in route[1:4]:
        point = tuple(point)
        if point in path or gap(path[-1], point) != 1 or not safe_step(point):
            break
        path.append(point)
        options.append((tuple(path), True))
    # Never use a side step to bypass an occupied route tile, door or transition.
    first = tuple(route[1])
    if (first not in vacant or first in group or first in other_monsters or gap(origin, first) != 1
            or any(gap(first,m) <= 2 and gap(first,m) < gap(origin,m) for m in other_monsters)):
        return ()
    x, y, z = origin
    for dx, dy in ((0,1),(1,0),(0,-1),(-1,0),(1,1),(-1,1),(1,-1),(-1,-1)):
        side = (x+dx, y+dy, z)
        if side != first and safe_step(side):
            options.append(((origin, side), False))
    best, chosen = (), ()
    for candidate, on_route in options:
        slots, room = access_score(candidate[-1])
        if slots < current + 2:
            continue
        rank = (min(slots, desired), min(room, 8), -len(candidate), on_route)
        if rank > best:
            best, chosen = rank, candidate
    return chosen


def placement_terrain(cells, origin, edges, group):
    protected = {tuple(e[k]) for e in edges for k in ('entry','origin','exit')}
    terrain, vacant = set(), set()
    for cell in cells:
        p = cell['x'],cell['y'],cell['z']
        if (p[2] != origin[2] or cell.get('kind') != 'clear' or cell.get('stairs')
                or cell.get('navigation') or p in protected):
            continue
        occupants = cell.get('occupants',0) - cell.get('occupant_types',{}).get('self',0)
        if occupants == 0:
            vacant.add(p)
        if occupants == 0 or p in group and occupants == cell.get('occupant_types',{}).get('monster'):
            terrain.add(p)
    return terrain, vacant


def gap(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def passage_width(tiles, point, axis):
    """Measure up to four walkable cells across. A narrow result requires two walls."""
    width = 1
    walls = 0
    for sign in (-1, 1):
        x, y, z = point
        for _ in range(3):
            x += sign if axis == 0 else 0
            y += sign if axis == 1 else 0
            tile = tiles.get((x, y, z))
            if ordinary_terrain(tile):
                width += 1
                if width >= 4:
                    return 4
                continue
            if (tile and tile.get('kind') in {'blocked','no_ground'} and not tile.get('navigation')
                    and (not tile.get('occupants') or any('not_walkable' in i.get('flags', ()) for i in tile['items']))):
                walls += 1
            break
    return width if walls == 2 else None


def room_before_corridor(cells, route, edges, members):
    """Stop on the room side of two consecutive narrow cross-sections on this route."""
    if len(route) < 3:
        return False
    here = tuple(route[0])
    tiles = {(c['x'], c['y'], c['z']): c for c in cells if c['z'] == here[2]}
    if not ordinary_terrain(tiles.get(here)):
        return False
    # Even three walkable lanes fill only 15 of these 25 cells in a straight tunnel.
    x, y, z = here
    if sum(ordinary_terrain(tiles.get((x+dx,y+dy,z))) for dx in range(-2,3) for dy in range(-2,3)) <= 15:
        return False
    ahead = []
    previous = here
    for point in map(tuple, route[1:5]):
        tile = tiles.get(point)
        if (not ordinary_terrain(tile) or tile.get('navigation') or gap(previous, point) != 1
                or any(tuple(e['origin']) == previous and tuple(e['exit']) == point for e in edges)):
            break
        ahead.append(point)
        previous = point
    for axis in (0, 1):
        if passage_width(tiles, here, axis) != 4:
            continue
        along = 1 - axis
        previous_narrow = None
        for point in ahead:
            width = passage_width(tiles, point, axis)
            if width is None or width >= 4:
                previous_narrow = None
                continue
            if (previous_narrow and (point[along] - previous_narrow[along])
                    * (previous_narrow[along] - here[along]) > 0):
                terrain, _ = placement_terrain(cells, here, edges, members)
                return here in terrain and members <= terrain and melee_capacity(terrain, here, members) >= len(members)
            previous_narrow = point
    return False


def squared(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def route_approaches(player, monster, path, index, edges=(), lookahead=4):
    """Whether the next ordinary route steps close the gap to this monster.

    Look past one diagonal/sideways bend, but never look through a transition
    or an unacknowledged recovery position. This classifies route intent only;
    it does not authorize walking or substitute for fresh terrain guards.
    """
    if not 0 <= index < len(path) or tuple(path[index]) != tuple(player):
        return False
    before = squared(player, monster)
    previous = player
    for point in path[index + 1:index + 1 + lookahead]:
        if point[2] != player[2] or any(
                tuple(e['origin']) == tuple(previous) and tuple(e['exit']) == tuple(point)
                for e in edges):
            break
        if squared(point, monster) < before:
            return True
        previous = point
    return False


class PursuitTracker:
    RESPONSE_WINDOW = 3.0
    CONTINUITY_WINDOW = 2.0
    SIGHT_GRACE = .6

    def __init__(self):
        self.reset()

    def reset(self):
        self.tracks = {}
        self.player = None
        self.updated = None
        self.sequence = 0
        self.following = set()

    def update(self, player, candidates, now):
        player = tuple(player)
        if self.player is not None and (player[2] != self.player[2] or
                gap(player, self.player) > 1 or not 0 <= now - self.updated <= self.CONTINUITY_WINDOW):
            self.reset()
        previous_player = self.player
        moved = previous_player is not None and player != previous_player
        if moved:
            self.sequence += 1
        live = {c['id'] for c in candidates}
        for cid, row in list(self.tracks.items()):
            if cid in live and not row['missing']:
                continue
            if now - row['seen'] > self.SIGHT_GRACE:
                del self.tracks[cid]
            elif cid not in live and not row['missing']:
                # Keep brief contact history for pacing only. Re-identification
                # still needs new pursuit evidence to count towards the limit.
                if row['confirmed']:
                    row['contact'] = row['seen']
                row.update(missing=True, confirmed=False)
                row['pending'].clear()
                row['evidence'].clear()
        self.following = set()
        for candidate in candidates:
            cid = candidate['id']
            position = tuple(candidate['position'])
            row = self.tracks.get(cid)
            if (row is None or row['name'] != candidate['name'] or
                    position[2] != row['position'][2] or gap(position, row['position']) > 2):
                self.tracks[cid] = dict(name=candidate['name'], position=position,
                                       pending=deque(maxlen=12), evidence=deque(maxlen=2), confirmed=False, last_follow=now,
                                       seen=now, missing=False, contact=now if gap(position, player)<=1 else None)
                continue
            row['seen'] = now
            row['missing'] = False
            if gap(position, player) <= 1:
                row['contact'] = now
            old = row['position']
            pending = row['pending']
            while pending and now - pending[0][2] > self.RESPONSE_WINDOW:
                pending.popleft()
            evidence = row['evidence']
            while evidence and now - evidence[0][1] > self.RESPONSE_WINDOW:
                evidence.popleft()
            if moved and squared(player, old) > squared(previous_player, old):
                pending.append((self.sequence, player, now))
            closes_gap = position != old and squared(position, player) < squared(old, player)
            if closes_gap:
                # Credit at most one player step per changed monster position.
                proof = next((p for p in pending if squared(position, p[1]) < squared(old, p[1])), None)
                if proof is not None:
                    row['contact'] = now
                    evidence.append((proof[0], now))
                    while pending and pending[0][0] <= proof[0]:
                        pending.popleft()
                    if len(evidence) == 2:
                        row['confirmed'] = True
                row['last_follow'] = now
            if row['confirmed']:
                # A confirmed melee follower can stand attacking while we wait.
                # A stopped/fleeing monster farther away loses its confirmation.
                if gap(position, player) <= 1 and not moved:
                    row['last_follow'] = now
                if now - row['last_follow'] > self.RESPONSE_WINDOW:
                    row['confirmed'] = False
                    evidence.clear()
                else:
                    self.following.add(cid)
            row['position'] = position
        self.player = player
        self.updated = now
        return [c for c in candidates if c['id'] in self.following]

    def recent_contacts(self, now):
        return {cid for cid, row in self.tracks.items() if row['contact'] is not None
                and 0 <= now - row['contact'] <= self.RESPONSE_WINDOW}
