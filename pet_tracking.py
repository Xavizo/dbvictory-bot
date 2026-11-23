"""Resolve a manual Look and its server description to one live creature ID."""
import logging
import re
import secrets
import threading
import time
from collections import Counter, defaultdict


LOOK_NAME = re.compile(r"You see ([^\r\n]{1,80}?)\.?", re.IGNORECASE)
MASTER = re.compile(r"Master of ([^\r\n]{1,80}?)\.?", re.IGNORECASE)
log = logging.getLogger(__name__)


class PetTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._slots = {}
        self._captured = 0
        self._map_generation = 0
        self._pending = None
        self._look_started = 0
        self._deadline = 0
        self._names = {}
        self._manual = set()
        self._owners = {}
        self._identified = set()
        self._refs = {}
        self._visible_since = {}
        self._selectable = {}
        self._new_on_tile = Counter()
        self._confirmed_map = None
        self._retired = {}

    def observe_map(self, cells, captured_at, slots, creatures=None):
        """Classify exact map occupants. Private stack identities stay in the engine."""
        counts = Counter(slots.values())
        slots = {slot: key for slot, key in slots.items() if counts[key] == 1}
        positions = {key: slot[:3] for slot, key in slots.items()}
        with self._lock:
            # A creature can remain in an old map frame after its observed death.
            # The next positive sighting with independent, current identity/HP
            # can start a new lifetime for the same numeric ID.
            for key, died in list(self._retired.items()):
                row = (creatures or {}).get(key, {})
                if (row.get("source") == "dll" and row.get("t", 0) > died
                        and abs(captured_at - row["t"]) <= .6 and row.get("health", 0) > 0
                        and (row.get("x"), row.get("y"), row.get("z")) == positions.get(key)):
                    self._retired.pop(key)
                elif not counts[key] and captured_at - died > 2:
                    self._retired.pop(key)
            continuous = 0 <= captured_at - self._captured <= 2
            for key in list(self._names):
                if (isinstance(key, tuple) or key in self._manual) and (counts[key] != 1 or not continuous):
                    del self._names[key]
                    self._owners.pop(key, None)
                    self._manual.discard(key)
            if continuous:
                self._identified.intersection_update(slots.values())
            else:
                self._identified.clear()
            named = {}
            for slot, key in slots.items():
                row = (creatures or {}).get(key)
                if not row or row.get("source") != "dll" or abs(captured_at - row.get("t", 0)) > 1:
                    continue
                if (row.get("x"), row.get("y"), row.get("z")) != slot[:3]:
                    continue
                label = row.get("name")
                if (isinstance(label, str) and label.strip() not in {"", "?"}
                        and len(label) <= 80 and label.isprintable()):
                    named[key] = label.strip()
                    self._identified.add(key)
            previous = self._refs if continuous else {}
            self._visible_since = {key: self._visible_since.get(key, captured_at) if key in previous else captured_at
                                   for key in slots.values()}
            self._refs = {key: previous.get(key) or secrets.token_hex(12) for key in slots.values()}
            self._selectable.clear()
            self._slots = slots
            self._captured = captured_at
            self._map_generation += 1
            if not continuous:
                self._pending = None
            elif self._pending and slots.get(self._pending[0]) != self._pending[1]:
                old_slot, identity, name = self._pending
                new_slot = next((slot for slot, key in slots.items() if key == identity), None)
                # Reordering the stack is common. If the pet moved, keep the
                # Look only while its identity is continuous and the old tile
                # is observed empty, so a replacement can never inherit it.
                origin_empty = any((c["x"], c["y"], c["z"]) == old_slot[:3] and
                                   c.get("occupants") == 0 and c.get("kind") != "unknown" for c in cells)
                if new_slot and (new_slot[:3] == old_slot[:3] or origin_empty):
                    self._pending = (new_slot, identity, name)
                else:
                    self._pending = None
            pets_by_position = defaultdict(list)
            identities = defaultdict(list)
            for slot, key in slots.items():
                identities[slot[:3]].append(key)
                if key in self._names:
                    pets_by_position[slot[:3]].append(key)
            for cell in cells:
                point = (cell["x"], cell["y"], cell["z"])
                occupants = identities.get(point, ())
                if cell.get("occupants") == 1 and len(occupants) == 1:
                    key = occupants[0]
                    if key not in self._names or key in self._manual:
                        self._selectable[point] = key
                        cell["pet_ref"] = self._refs[key]
                        if key in named:
                            cell["pet_candidate_name"] = named[key]
                        if key in self._manual:
                            cell["pet_manual"] = True
                if creatures is not None and occupants:
                    unknown = sum(key not in self._names and key not in self._identified for key in occupants)
                    if unknown:
                        # Display only. An incomplete list says nothing about combat eligibility.
                        cell["unidentified_monsters"] = unknown
                pets = pets_by_position.get(point)
                if pets:
                    types = cell["occupant_types"]
                    types["monster"] -= len(pets)
                    if not types["monster"]:
                        del types["monster"]
                    types["pet"] = len(pets)
                    cell["pet_names"] = [self._names[key] for key in pets]
                    if any(key in self._owners for key in pets):
                        cell["pet_owners"] = [self._owners.get(key) for key in pets]
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
            self._look_started = now
            self._deadline = now + 2

    def invalidate_pending_look(self):
        with self._lock:
            self._pending = None

    def description(self, text, creatures, *, received_at=None):
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
            if received_at is not None and received_at < self._look_started:
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
                    if cid not in self._owners:
                        fresh = row.get("source") == "dll" and abs(time.time() - row.get("t", 0)) <= 1
                        log.info("Pet Look: map_id=%s, fresh_creature=%s, name_read=%s, hp_read=%s",
                                 isinstance(cid, int), fresh, fresh and known not in (None, "", "?"),
                                 fresh and type(row.get("health")) is int and 0 <= row["health"] <= 100)
                    self._owners[cid] = owner_line[1].strip()
                    self._manual.discard(cid)
                    self._confirm(cid, slot[:3], label)
            self._deadline = 0

    def _confirm(self, key, point, name):
        if key not in self._names:
            if isinstance(key, int):
                log.info("Pet confirmed: %r, game ID %d", name, key)
            else:
                log.info("Pet confirmed: %r, visible on map only; no game ID", name)
            if self._confirmed_map != self._map_generation:
                self._confirmed_map = self._map_generation
                self._new_on_tile.clear()
            self._new_on_tile[point] += 1
        self._names[key] = name

    def label_visible(self, point, reference, name):
        """Apply a panel observation to the same single creature still on this tile."""
        if name is not None and (not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or not name.isprintable()):
            raise ValueError("Podaj nazwę peta z gry.")
        with self._lock:
            key = self._selectable.get(point)
            if key is None or self._refs.get(key) != reference or not 0 <= time.time() - self._captured <= 0.8:
                raise ValueError("Stworzenie zmieniło pole lub odczyt wygasł. Wybierz je ponownie.")
            if name is None:
                if key not in self._manual:
                    raise ValueError("To stworzenie nie ma ręcznego oznaczenia.")
                self._manual.remove(key)
                del self._names[key]
                if self._confirmed_map == self._map_generation and self._new_on_tile[point]:
                    self._new_on_tile[point] -= 1
            elif key not in self._names:
                self._manual.add(key)
                self._confirm(key, point, name.strip())
            else:
                raise ValueError("Pet został już rozpoznany.")

    def retire(self, cid, died):
        """A confirmed death closes this identity, including any old owner proof."""
        with self._lock:
            if self._visible_since.get(cid, 0) > died:
                return False
            was_pet = self._names.pop(cid, None) is not None
            self._owners.pop(cid, None)
            self._manual.discard(cid)
            self._identified.discard(cid)
            self._refs.pop(cid, None)
            if self._pending and self._pending[1] == cid:
                self._pending = None
            self._retired[cid] = died
            return was_pet

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
                if key not in self._names and key not in self._retired:
                    by_tile[slot[:3]].append(key)
            return by_tile

    def confirmed_after_map(self, position, generation):
        """Exclude a new confirmation during the tick before the next map read."""
        with self._lock:
            return self._new_on_tile[position] if generation == self._confirmed_map else 0
