"""Resolve a manual Look and its server description to one live creature ID."""
import re
import threading
import time
from collections import Counter, defaultdict


LOOK_NAME = re.compile(r"You see ([^\r\n]{1,80}?)\.?", re.IGNORECASE)
MASTER = re.compile(r"Master of ([^\r\n]{1,80}?)\.?", re.IGNORECASE)


class PetTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._slots = {}
        self._captured = 0
        self._map_generation = 0
        self._pending = None
        self._deadline = 0
        self._names = {}
        self._new_on_tile = Counter()
        self._confirmed_map = None

    def observe_map(self, cells, captured_at, slots):
        """Classify exact map occupants. Private stack identities stay in the engine."""
        counts = Counter(slots.values())
        slots = {slot: key for slot, key in slots.items() if counts[key] == 1}
        with self._lock:
            for key in list(self._names):
                if isinstance(key, tuple) and (key not in counts or captured_at - self._captured > 2):
                    del self._names[key]
            self._slots = slots
            self._captured = captured_at
            self._map_generation += 1
            if self._pending and slots.get(self._pending[0]) != self._pending[1]:
                self._pending = None
            if not self._names:
                return self._map_generation
            pets_by_position = defaultdict(list)
            for slot, key in slots.items():
                if key in self._names:
                    pets_by_position[slot[:3]].append(self._names[key])
            for cell in cells:
                pets = pets_by_position.get((cell["x"], cell["y"], cell["z"]))
                if pets:
                    types = cell["occupant_types"]
                    types["monster"] -= len(pets)
                    if not types["monster"]:
                        del types["monster"]
                    types["pet"] = len(pets)
                    cell["pet_names"] = pets
            return self._map_generation

    def look(self, position, stack):
        now = time.monotonic()
        with self._lock:
            slot = (*position, stack)
            cid = self._slots.get(slot)
            # Two outstanding Looks have no reply IDs. Discard overlapping
            # requests until late replies have had time to arrive.
            valid = cid is not None and now >= self._deadline and 0 <= time.time() - self._captured <= .6
            self._pending = (slot, cid, None) if valid else None
            self._deadline = now + 2

    def description(self, text, creatures):
        if len(text) > 240:
            return
        lines = text.strip().splitlines()
        if not lines:
            return
        name = LOOK_NAME.fullmatch(lines[0].strip())
        owner_line = MASTER.fullmatch(lines[-1].strip())
        if not name and not owner_line:
            return
        with self._lock:
            if not self._pending or time.monotonic() > self._deadline:
                return
            slot, cid, previous = self._pending
            self._pending = None
            if self._slots.get(slot) != cid or time.time() - self._captured > .6:
                return
            if len(lines) == 1 and name and previous is None:
                self._pending = (slot, cid, name[1])
                return
            if owner_line and ((len(lines) == 2 and name and previous is None)
                               or (len(lines) == 1 and previous is not None)):
                label = previous or name[1]
                row = creatures.get(cid, {})
                known = row.get("name") if time.time() - row.get("t", 0) < .6 else None
                if known in (None, "", "?") or known.casefold() == label.casefold():
                    if cid not in self._names:
                        if self._confirmed_map != self._map_generation:
                            self._confirmed_map = self._map_generation
                            self._new_on_tile.clear()
                        self._new_on_tile[slot[:3]] += 1
                    self._names[cid] = label
            self._deadline = 0

    def is_pet(self, cid):
        with self._lock:
            return cid in self._names

    def monster_slots_for_sample(self, generation):
        """Internal continuity keys for this map frame, including map-only occupants."""
        with self._lock:
            if generation != self._map_generation:
                return {}
            by_tile = defaultdict(list)
            for slot, key in self._slots.items():
                if key not in self._names:
                    by_tile[slot[:3]].append(key)
            return by_tile

    def confirmed_after_map(self, position, generation):
        """Exclude a new confirmation during the tick before the next map read."""
        with self._lock:
            return self._new_on_tile[position] if generation == self._confirmed_map else 0
