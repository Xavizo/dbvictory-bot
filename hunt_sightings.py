"""First identified map tile of each wild creature in a route run."""
from collections import Counter
from threading import Lock

from hunt_areas import sector


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
                            self.rows[cid] = (point, sector(center), None)
                    row = self.rows.get(cid)
                    if row is None or row[2]:
                        continue
                    creature = self.game.creatures.get(cid, {})
                    name = creature.get('name')
                    if (name and name != '?' and creature.get('source') == 'dll'
                            and abs(creature.get('t', 0) - captured) <= .6):
                        self.rows[cid] = (row[0], row[1], name)

    def end_lap(self, next_start=None):
        with self.lock:
            self.started = next_start
            rows, self.rows = self.rows, {}
            sampled, self.sampled = self.sampled, False
        if not sampled:
            return None
        counts = Counter(row for cid, row in rows.items() if row[2] and not self.game.pets.is_pet(cid))
        return [dict(point=point, observer_cell=origin, name=name, count=count)
                for (point, origin, name), count in sorted(counts.items())]
