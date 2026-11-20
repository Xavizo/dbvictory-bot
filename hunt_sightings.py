"""First identified map tile of each wild creature in a route run."""
from collections import Counter, defaultdict
from threading import Lock

from hunt_areas import sector


def fresh_monster_name(game, cid, captured):
    creature = game.creatures.get(cid, {})
    name = creature.get('name')
    if (name and name != '?' and creature.get('source') == 'dll'
            and abs(creature.get('t', 0) - captured) <= .6):
        return name
    return None


def merge_witnesses(groups):
    """One actual frame per species, origin and 8×8 region. Never sum frames."""
    best = {}
    for group in groups:
        points = group['points']
        key = group['name'], tuple(group['observer_cell']), sector(points[0])
        if key not in best or len(points) > len(best[key]['points']):
            best[key] = group
    return list(best.values())


def concurrent_firsts(rows):
    frames = defaultdict(list)
    for point, origin, name, captured in rows:
        if name:
            frames[captured, name, origin, sector(point)].append(point)
    return merge_witnesses(dict(name=name, observer_cell=origin, points=sorted(points))
                           for (_,name,origin,_),points in frames.items() if len(points)>1)


class FirstSightings:
    def __init__(self, game):
        self.game = game
        self.lock = Lock()
        self.seen = set()
        self.started = None
        self.rows = {}
        self.sampled = False

    def begin_lap(self, started):
        with self.lock:
            if self.started is None:
                self.started = started

    def observe(self, frame):
        # This runs on the passive map thread. Identity and position come from
        # the same published map generation. The battle list supplies names only.
        captured = frame['captured_at']
        center = frame['center']
        slots = self.game.pets.monster_slots_for_sample(frame['pet_map_generation'])
        with self.lock:
            if self.started is not None and captured < self.started:
                return
            in_lap = self.started is not None
            if in_lap:
                self.sampled = True
            for point, occupants in slots.items():
                for cid in occupants:
                    if type(cid) is not int or point[2] != center[2]:
                        continue
                    if cid not in self.seen:
                        self.seen.add(cid)
                        if in_lap:
                            self.rows[cid] = (point, sector(center), None, captured)
                    row = self.rows.get(cid)
                    if row is None or row[2]:
                        continue
                    name = fresh_monster_name(self.game, cid, captured)
                    if name:
                        self.rows[cid] = (row[0], row[1], name, row[3])

    def end_lap(self, next_start=None):
        with self.lock:
            self.started = next_start
            rows, self.rows = self.rows, {}
            sampled, self.sampled = self.sampled, False
        if not sampled:
            return None
        confirmed = [row for cid, row in rows.items() if row[2] and not self.game.pets.is_pet(cid)]
        counts = Counter(row[:3] for row in confirmed)
        marks = {key:dict(point=key[0], observer_cell=key[1], name=key[2], count=count)
                 for key,count in sorted(counts.items())}
        for group in concurrent_firsts(confirmed):
            key = group['points'][0], group['observer_cell'], group['name']
            marks[key]['together'] = group['points']
        return list(marks.values())
