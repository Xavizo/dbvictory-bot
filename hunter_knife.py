"""Combat, personal Loot and visible corpse stacks for the single-target Hunter Knife."""
from collections import Counter, defaultdict
from dataclasses import dataclass
import logging
import re

from farming_telemetry import experience_for_level
from hunt_sightings import fresh_monster_name

KNIFE = 5908
FRESH = 9068
SPENT = 8141
ELEMENTALS = frozenset({"fire elemental", "fire elementalis", "mutant fire elemental",
                        "fury mutant fire", "fury mutant fire elemental"})
LOOT_LINE = re.compile(
    r"(?:(?:[01][0-9]|2[0-3]):[0-5][0-9][ \t]+)?"
    r"\[([^\[\]\r\n]{1,64})\]: [^\r\n]+"
)
SETTLE = 1.0
SOLO_SETTLE = .18
EVIDENCE_WINDOW = 1.5
PROVISIONAL_HOLD = .45
MAX_WAIT = 8.0
REWARD_VALID_FOR = 300.0


def loot_species(text):
    match = LOOT_LINE.fullmatch(text.strip())
    if match and match[1].casefold() in ELEMENTALS:
        return match[1].casefold()
    return None


def ownership_denied(text):
    message = text.strip().casefold().rstrip(".!")
    message = message.removeprefix("sorry, ").removeprefix("sorry ")
    return (message in ("you are not owner", "you are not the owner") or
            message.startswith(("you are not owner of this", "you are not the owner of this")))


def publish_feedback(game, parsed, kills, received_at):
    """Publish only semantic server evidence, on the owning game loop."""
    if received_at is None:
        return
    runtime = getattr(game, "hunter_knife", None)
    if runtime and received_at < runtime.started:
        runtime = None
    for kind, species in parsed.knife_feedback:
        if kind == "loot":
            game.knife_loot_count += 1
            if runtime:
                runtime.queue.credit(species, received_at)
    for message in parsed.messages:
        text = message.get("text", "")
        if loot_species(text):
            # A system text can look exactly like Loot; retain only a count
            # until its origin is verified for this client.
            game.knife_candidate_count += 1
        if ownership_denied(text):
            pending = runtime.pending if runtime else None
            auto = bool(pending and not pending.drop and 0 <= received_at - pending.sent <= 1.5)
            manual_at = game.knife_manual_use_at
            manual = manual_at is not None and 0 <= received_at - manual_at <= 2
            if auto or manual:
                game.knife_denials += 1
                logging.getLogger(__name__).info("Hunter Knife 5908: odmowa właściciela (%s)",
                                                "automatyka" if auto else "ręczne użycie")
            if auto:
                runtime.rejected(received_at)
    if runtime:
        for death in kills:
            runtime.queue.death(death, received_at)


def body_counts(items):
    return Counter(item["id"] for item in items if item["id"] in (FRESH, SPENT))


def skinned(before, after):
    return (before - after == Counter({FRESH: 1})
            and after - before == Counter({SPENT: 1}))


def exposed_body_action(items):
    """What can be done to this stack from the current tile observation?"""
    if not any(item["id"] == FRESH and "container" in item.get("flags", ()) for item in items):
        return None
    top = max(items, key=lambda item: item["stack"])
    flags = top.get("flags", ())
    if "container" in flags:
        if top["id"] == FRESH:
            return "skin", top
        if top["id"] in (SPENT, 8506) and "not_moveable" not in flags:
            return "uncover", top
    return None


@dataclass
class OwnedStack:
    fresh: int
    expires: float
    appeared: float | None = None
    assigned: float | None = None
    reward_sample: tuple[str, int] | None = None


class CorpseWitnesses:
    """Link a missing death description to the same creature in consecutive map frames."""

    def __init__(self):
        self.previous = {}
        self.last_frame = None
        self.bodies = {}
        self.tracker = None

    def observe(self, game, generation, stamp, now, baseline, current, monsters):
        self.tracker = getattr(game, "pets", None)
        previous_frame = self.last_frame
        floor = game.position[2] if game is not None else None
        self.last_frame = (game, generation, stamp, floor)
        previous, self.previous = self.previous, {}
        if generation is None or self.tracker is None:
            self.bodies.clear()
            return

        slots = self.tracker.monster_slots_for_sample(generation, require_current=True)
        if slots is None:
            self.bodies.clear()
            self.last_frame = None
            return
        consecutive = (previous_frame is not None and previous_frame[0] is game
                       and previous_frame[1] == generation - 1 and previous_frame[3] == floor
                       and 0 < stamp - previous_frame[2] <= .6)
        present = {cid for occupants in slots.values() for cid in occupants}
        self.bodies = {cid: witness for cid, witness in self.bodies.items()
                       if consecutive and cid not in present and now - witness[0] <= EVIDENCE_WINDOW + SETTLE
                       and current.get(witness[1], 0) == witness[3]}
        if consecutive:
            for p, (cid, name) in previous.items():
                if (cid in present or self.tracker.is_pet(cid) or monsters.get(p)
                        or p not in baseline or p not in current or current[p] != baseline[p] + 1):
                    continue
                row = game.creatures.get(cid, {})
                if (row.get("health", 0) > 0 and row.get("t", 0) > previous_frame[2]
                        and tuple(row.get(k) for k in ("x", "y", "z")) != p):
                    continue
                self.bodies[cid] = now, p, name, current[p]

        for p, occupants in slots.items():
            if len(occupants) != 1 or monsters.get(p) != 1 or p[2] != game.position[2]:
                continue
            cid = occupants[0]
            if type(cid) is int and game.creatures.get(cid, {}).get("health", 0) > 0:
                name = (fresh_monster_name(game, cid, stamp, p) or "").casefold()
                if name in ELEMENTALS:
                    self.previous[p] = cid, name

    def resolve(self, death):
        at, name, p, cid = death
        witness = self.bodies.get(cid)
        if witness and abs(witness[0] - at) <= .6:
            _, point, species, _ = witness
            if name in ("", species) and p in (point, (0, 0, 0), (None, None, None)):
                return at, species, point, cid
        return death


class SkinningQueue:
    """Only grant a tile when every fresh body on it has a matching personal kill."""

    def __init__(self):
        self.stacks = {}
        self.baseline = {}
        self.births = []
        self.interrupted_births = set()
        self.witnesses = CorpseWitnesses()
        self._observed_deaths = []
        self.loot = []
        self.last_event = None
        self.target = None
        self.last_xp = None
        self.xp_gains = []
        self.solo_rewards = {}
        self.position = None
        self.other_player_at = float("-inf")
        self.last_settled_at = float("-inf")
        self.group_wait_until = float("-inf")
        self.other_death_at = float("-inf")
        self.last_combat_sample = None
        self.from_combat = 0
        self.unassigned_bodies = 0
        self.combat_claims = {}

    @property
    def deaths(self):
        # Keep the original row. A reappearing actor can invalidate a tentative tile match.
        return [self.witnesses.resolve(d) for d in self._observed_deaths]

    def possible_work(self, position, now, *, include_owned=True):
        def nearby(p):
            return (all(type(v) is int for v in p) and p[2] == position[2]
                    and max(abs(p[0]-position[0]), abs(p[1]-position[1])) <= 1)
        if tuple(position) == self.position and now <= self.group_wait_until:
            return True
        if include_owned and any(nearby(p) and now < s.expires for p, s in self.stacks.items()):
            return True
        return (any(nearby(p) and 0 <= now-at <= PROVISIONAL_HOLD
                    and p not in self.stacks and ((self.target and cid == self.target[0]) or any(n == name for _, n in self.loot))
                    for at, name, p, cid in self.deaths)
                or any(target and nearby(p) and p not in self.stacks and 0 <= now-at <= PROVISIONAL_HOLD
                       for at, p, _, target in self.births))

    def personal_loot(self, text, now):
        name = loot_species(text)
        if name:
            self.credit(name, now)

    def credit(self, name, now):
        self.loot.append((now, name))
        self.last_event = now

    def death(self, row, now):
        cid = row.get("cid")
        name = (row.get("name") or "").casefold()
        if name == "?":
            name = ""
        if (row.get("creature_kind") == "pet" or (self.witnesses.tracker and self.witnesses.tracker.is_pet(cid))
                or (name and name not in ELEMENTALS)):
            self.other_death_at = now
            return
        point = (row.get("x"), row.get("y"), row.get("z"))
        self._observed_deaths.append((now, name, point, cid))
        self.last_event = now

    def observe(self, cells, now, *, game=None, target_id=None, sampled_at=None, map_generation=None):
        previous = self.target
        self.target = None
        current = {}
        monsters = {}
        target_pos = None
        on_screen = set()
        if game is not None:
            xp = getattr(game, "experience", 0)
            self.position = tuple(game.position)
            floor = game.position[2]
            if getattr(game, "player_party_shield", 0):
                self.other_player_at = now  # Shared XP cannot identify the owner.
            if self.last_combat_sample and (self.last_combat_sample[1] != floor
                                            or now - self.last_combat_sample[0] > 1):
                # Claims cannot survive an unobserved change in the tile stack.
                self.stacks.clear()
                self.baseline.clear()
                self.births.clear()
                self._observed_deaths.clear()
                self.combat_claims.clear()
                self.loot.clear()
                self.xp_gains.clear()
                self.solo_rewards.clear()
                self.last_xp = None
                previous = None
            level = getattr(game, "level", None)
            if level is not None and not (type(level) is int and 1 <= level < 65536 and type(xp) is int
                                          and experience_for_level(level) <= xp < experience_for_level(level + 1)):
                # Do not bridge a gap or erase learned rewards on one impossible stats sample.
                xp = None
                self.xp_gains.clear()
            elif self.last_xp and xp > self.last_xp:
                self.xp_gains.append((now, xp - self.last_xp))
            elif self.last_xp and xp < self.last_xp:
                self.xp_gains.clear()
                self.solo_rewards.clear()
            self.last_xp = xp
            self.last_combat_sample = (now, floor)
            target = game.creatures.get(target_id, {})
            on_screen = {cid for cid, creature in game.creatures.items()
                         if creature.get("health", 0) > 0 and abs(sampled_at - creature.get("t", 0)) <= .6}
            pet = getattr(game, "pets", None)
            if (target_id in on_screen and (target.get("name") or "").casefold() in ELEMENTALS
                    and not (pet and pet.is_pet(target_id))):
                target_pos = (target.get("x"), target.get("y"), target.get("z"))
        for c in cells:
            if c.get("kind") == "unknown" or "items" not in c:
                continue
            p = (c["x"], c["y"], c["z"])
            count = sum(i["id"] == FRESH for i in c["items"])
            current[p] = count
            occupants = c.get("occupant_types", {})
            if occupants.get("monster"):
                monsters[p] = occupants["monster"]
            if occupants.get("player") or occupants.get("unknown"):
                self.other_player_at = now
            if p == target_pos and occupants.get("monster") == 1:
                self.target = (target_id, p, now)
            if p in self.baseline and count > self.baseline[p]:
                candidate = (previous[0] if previous and previous[1] == p
                             and now - previous[2] <= 1 and not occupants.get("monster") else None)
                self.births.append((now, p, count - self.baseline[p], candidate))
                self.last_event = now
        if (not self.target and previous and now - previous[2] <= 1 and previous[1] in current
                and target_pos in (None, previous[1])):
            self.target = previous
        self.witnesses.observe(game, map_generation, sampled_at, now, self.baseline, current, monsters)
        self.baseline = current
        self.stacks = {p: s for p, s in self.stacks.items()
                       if now < s.expires and current.get(p, 0) >= s.fresh}
        cutoff = now - MAX_WAIT
        self.xp_gains = [g for g in self.xp_gains if g[0] >= cutoff]
        self.births = [b for b in self.births if b[0] >= cutoff]
        # A later look at the same tile cannot identify the same corpse after a gap.
        self.interrupted_births.intersection_update((t, p) for t, p, _, _ in self.births)
        self.interrupted_births.update((t, p) for t, p, _, _ in self.births if p not in current)
        self._observed_deaths = [d for d in self._observed_deaths if d[0] >= cutoff]
        self.loot = [l for l in self.loot if l[0] >= cutoff]
        self.combat_claims = {k: v for k, v in self.combat_claims.items() if k[0] >= cutoff}
        if self.births and self.xp_gains:
            self._claim_combat_bodies(current, game.creatures if game is not None else {}, now)
        self.settle(now)

    def settle(self, now):
        """Resolve independent species without separating deaths that share a tile."""
        self.group_wait_until = float("-inf")
        if self.last_event is None:
            return
        # A Loot entry names a species, while a new corpse identifies only its
        # tile. Death observations connect those two kinds of evidence.
        neighbors = {name: set() for _, name in self.loot}
        neighbors.update((point, set()) for _, point, _, _ in self.births)
        for _, name, point, _ in self.deaths:
            neighbors.setdefault(name, set()).add(point)
            neighbors.setdefault(point, set()).add(name)

        unresolved = set(neighbors)
        # An unnamed death or an unaccounted new corpse could belong to any
        # species. Preserve the original shared correlation window in that case.
        ambiguous = "" in neighbors or any(not neighbors[p] for _, p, _, _ in self.births)
        while unresolved:
            if ambiguous:
                group = unresolved.copy()
                unresolved.clear()
            else:
                group = set()
                pending = [unresolved.pop()]
                while pending:
                    key = pending.pop()
                    group.add(key)
                    linked = unresolved.intersection(neighbors[key])
                    unresolved.difference_update(linked)
                    pending.extend(linked)
            self._settle_group(group, now)
        self.last_event = max((r[0] for rows in (self.deaths, self.loot, self.births) for r in rows),
                              default=None)

    def _settle_group(self, group, now):
        deaths = [d for d in self.deaths if d[1] in group]
        personal = [l for l in self.loot if l[1] in group]
        appeared = [b for b in self.births if b[1] in group]
        times = [row[0] for rows in (deaths, personal, appeared) for row in rows]
        deadline = min(times) + EVIDENCE_WINDOW
        deaths = [d for d in deaths if d[0] <= deadline]
        personal = [l for l in personal if l[0] <= deadline]
        appeared = [b for b in appeared if b[0] <= deadline]
        quiet = now - max(t for t in times if t <= deadline)
        if quiet < SOLO_SETTLE:
            return
        credit = Counter(name for _, name in personal)
        observed = Counter(name for _, name, _, _ in deaths)
        positions = Counter(point for _, _, point, _ in deaths)
        births = Counter()
        first_seen = {}
        for at, point, n, _ in appeared:
            births[point] += n
            first_seen[point] = min(at, first_seen.get(point, at))
        complete = bool(deaths) and "" not in observed and credit == observed and births == positions
        interrupted = {p for t, p, _, _ in appeared if (t, p) in self.interrupted_births}
        claimed = Counter(p for at, p in self.combat_claims if p in group and at <= deadline)
        group_gains = []
        if not personal and not claimed and not interrupted and births and births == positions:
            group_gains = self._solo_group_gains(deaths, appeared, births, now)
        if not complete and now <= deadline:
            if group_gains:
                self.group_wait_until = max(self.group_wait_until, deadline)
            return
        if quiet < SETTLE and (self.other_player_at >= now - EVIDENCE_WINDOW
                               or self.other_death_at >= now - EVIDENCE_WINDOW
                               or any(not name or not cid for _, name, _, cid in deaths)):
            return
        # An observed death may have fallen beyond the readable map. At expiry
        # a visible tile can still be used if all personal credits match and
        # every locally observed body is accounted for by an identified death.
        matched = "" not in observed and credit == observed and not (births - positions)
        if group_gains:
            self.xp_gains = [g for g in self.xp_gains if g not in group_gains]
        for p, n in births.items():
            remaining = n - claimed[p]
            if remaining <= 0 or p not in self.baseline or p in interrupted:
                continue
            owned = self.stacks[p].fresh if p in self.stacks else 0
            available = max(0, self.baseline[p] - owned)
            if (matched or group_gains) and positions[p] == n and available == remaining:
                self.stacks[p] = OwnedStack(owned + remaining, now + MAX_WAIT, first_seen[p], now)
                if group_gains:
                    self.from_combat += remaining
            else:
                self.unassigned_bodies += min(remaining, available)
        self._observed_deaths = [d for d in self._observed_deaths
                                 if d[0] > deadline or self.witnesses.resolve(d)[1] not in group]
        self.last_settled_at = max(self.last_settled_at, max(t for t in times if t <= deadline))
        self.loot = [l for l in self.loot if l[0] > deadline or l[1] not in group]
        self.births = [b for b in self.births if b[0] > deadline or b[1] not in group]
        self.combat_claims = {k: v for k, v in self.combat_claims.items() if k[0] > deadline or k[1] not in group}

    def _solo_group_gains(self, deaths, appeared, births, now):
        """Use a locally measured XP reward only for one isolated, fully observed wave."""
        count = len(deaths)
        if not 2 <= count <= 8 or not self.position:
            return []
        names = {d[1] for d in deaths}
        victims = {d[3] for d in deaths}
        if len(names) != 1 or None in victims or len(victims) != count:
            return []
        reward, samples, calibrated = self.solo_rewards.get(next(iter(names)), (0, 0, 0))
        if samples < 2 or now - calibrated > REWARD_VALID_FOR:
            return []
        start = min(min(d[0] for d in deaths), min(b[0] for b in appeared))
        end = max(max(d[0] for d in deaths), max(b[0] for b in appeared))
        blocked_since = start - EVIDENCE_WINDOW
        victim_tiles = {(cid, p) for _, _, p, cid in deaths}
        if (end - start > 1 or max(self.other_player_at, self.other_death_at, self.last_settled_at) >= blocked_since
                or not any((cid, p) in victim_tiles for _, p, _, cid in appeared)
                or any(t >= blocked_since for t, _ in self.loot)
                or any(d not in deaths for d in self.deaths if d[0] >= blocked_since)
                or any(b not in appeared for b in self.births if b[0] >= blocked_since)):
            return []
        x, y, z = self.position
        if any(p[2] != z or max(abs(p[0]-x), abs(p[1]-y)) > 1
               or p in self.stacks or self.baseline.get(p) != n for p, n in births.items()):
            return []
        gains = [g for g in self.xp_gains if start - 1 <= g[0] <= end + 1]
        if sum(amount for _, amount in gains) != count * reward or any(amount % reward for _, amount in gains):
            return []
        return gains

    def _claim_combat_bodies(self, current, creatures, now):
        # Match before releasing the Loot batch. Late matching Loot cannot grant
        # the same body again, even after it was already skinned.
        used_targets = set(self.combat_claims.values())
        for at, p, count, cid in self.births:
            if (now-at < SOLO_SETTLE or (at, p) in self.interrupted_births or not cid or count != 1
                    or creatures.get(cid, {}).get("health", 0) > 0
                    or cid in used_targets
                    or self.other_player_at >= at - 1.5 or abs(self.other_death_at - at) <= 1.5
                    or any(abs(t-at) <= 1.5 and dead_id != cid for t, _, _, dead_id in self.deaths)
                    or sum(n for t, _, n, _ in self.births if abs(t-at) <= 1.5) != 1):
                continue
            gains = [g for g in self.xp_gains if abs(g[0]-at) <= 1]
            gain = gains[0] if gains else None
            owned = self.stacks[p].fresh if p in self.stacks else 0
            if gain is None or current.get(p) != owned + 1:
                continue
            self.combat_claims[at, p] = cid
            self.xp_gains.remove(gain)
            used_targets.add(cid)
            stack = self.stacks[p] = OwnedStack(owned + 1, now + MAX_WAIT, at, now)
            names = {name for t, name, pos, victim in self.deaths
                     if victim == cid and pos == p and abs(t-at) <= 1 and name in ELEMENTALS}
            if len(gains) == 1 and len(names) == 1 and not owned:
                stack.reward_sample = (names.pop(), gain[1])
            self.from_combat += 1

    def next_body(self, point, items):
        stack = self.stacks.get(point)
        if not stack or body_counts(items)[FRESH] != stack.fresh:
            return None
        return exposed_body_action(items)

    def consumed(self, point, *, confirmed=False):
        stack = self.stacks.get(point)
        if stack:
            if confirmed and stack.reward_sample:
                name, amount = stack.reward_sample
                previous, samples, calibrated = self.solo_rewards.get(name, (0, 0, 0))
                repeat = amount == previous and stack.assigned - calibrated <= REWARD_VALID_FOR
                self.solo_rewards[name] = (amount, samples + 1 if repeat else 1, stack.assigned)
                stack.reward_sample = None
            stack.fresh -= 1
            if not stack.fresh:
                del self.stacks[point]
