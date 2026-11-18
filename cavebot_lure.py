"""Passive pursuit evidence from fresh creature positions, never screen counts.

The client does not expose a monster's aggro target. Two separate player steps
away, each followed by a monster step closing that gap, are our conservative
movement-based confirmation. No probe commands are sent to the game.
"""
from collections import deque


def lure_stopping_path(cells, route, edges, group, other_monsters):
    """Choose up to three ordinary route steps, once per gathered group."""
    if len(group) < 2 or len(route) < 2:
        return ()
    origin = tuple(route[0])
    group = set(map(tuple, group))
    if all(gap(origin, p) <= 1 for p in group):
        return ()
    tiles = {(c['x'], c['y'], c['z']): c for c in cells}
    protected = {tuple(e[k]) for e in edges for k in ('entry', 'origin', 'exit')}
    terrain = set()
    vacant = set()
    for p, cell in tiles.items():
        if (p[2] != origin[2] or cell.get('kind') != 'clear' or cell.get('stairs')
                or cell.get('navigation') or p in protected):
            continue
        occupants = cell.get('occupants', 0) - cell.get('occupant_types', {}).get('self', 0)
        if occupants == 0:
            vacant.add(p)
        if occupants == 0 or p in group and occupants == cell.get('occupant_types', {}).get('monster'):
            terrain.add(p)
    if not group <= terrain or origin not in terrain:
        return ()

    def access_slots(center):
        # Evaluate the same known room with the player blocking the chosen tile.
        # A group stranded on the other side of that tile must not be left behind.
        reachable = {next(iter(group))}
        queue = deque(reachable)
        while queue:
            x, y, z = queue.popleft()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    p = (x + dx, y + dy, z)
                    if p != center and p in terrain and p not in reachable:
                        reachable.add(p)
                        queue.append(p)
        if not group <= reachable:
            return 0
        return sum(gap(p, center) == 1 for p in reachable)

    current = access_slots(origin)
    desired = min(8, max(6, len(group) + 2))
    if current >= desired:
        return ()
    best, chosen = current, ()
    path = [origin]
    for point in route[1:4]:
        point = tuple(point)
        if point not in vacant or point in path or gap(path[-1], point) != 1:
            break
        if any(gap(point, m) <= 2 and gap(point, m) < gap(origin, m) for m in other_monsters):
            break
        path.append(point)
        score = access_slots(point)
        if score >= current + 2 and score > best:
            best, chosen = score, tuple(path)
            if score >= desired:
                break
    return chosen


def gap(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


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
        self.tracks = {cid: row for cid, row in self.tracks.items() if cid in live}
        self.following = set()
        for candidate in candidates:
            cid = candidate['id']
            position = tuple(candidate['position'])
            row = self.tracks.get(cid)
            if (row is None or row['name'] != candidate['name'] or
                    position[2] != row['position'][2] or gap(position, row['position']) > 2):
                self.tracks[cid] = dict(name=candidate['name'], position=position,
                                       pending=deque(maxlen=12), evidence=deque(maxlen=2), confirmed=False, last_follow=now)
                continue
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
