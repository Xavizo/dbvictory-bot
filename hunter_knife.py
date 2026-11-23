"""Combat, personal Loot and visible corpse stacks for the single-target Hunter Knife."""
from collections import Counter, defaultdict
from dataclasses import dataclass
import logging
import re

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


@dataclass
class OwnedStack:
    fresh: int
    expires: float
    appeared: float | None = None
    assigned: float | None = None


class SkinningQueue:
    """Only grant a tile when every fresh body on it has a matching personal kill."""

    def __init__(self):
        self.stacks = {}
        self.baseline = {}
        self.births = []
        self.deaths = []
        self.loot = []
        self.last_event = None
        self.target = None
        self.last_xp = None
        self.xp_gains = []
        self.other_player_at = float("-inf")
        self.other_death_at = float("-inf")
        self.last_combat_sample = None
        self.from_combat = 0
        self.combat_claims = {}

    def possible_work(self, position, now, *, include_owned=True):
        def nearby(p):
            return (all(type(v) is int for v in p) and p[2] == position[2]
                    and max(abs(p[0]-position[0]), abs(p[1]-position[1])) <= 1)
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
        name = (row.get("name") or "").casefold()
        if name and name not in ELEMENTALS:
            self.other_death_at = now
            return
        point = (row.get("x"), row.get("y"), row.get("z"))
        self.deaths.append((now, name, point, row.get("cid")))
        self.last_event = now

    def observe(self, cells, now, *, game=None, target_id=None, sampled_at=None):
        previous = self.target
        self.target = None
        current = {}
        target_pos = None
        on_screen = set()
        if game is not None:
            xp = getattr(game, "experience", 0)
            floor = game.position[2]
            if self.last_combat_sample and (self.last_combat_sample[1] != floor
                                            or now - self.last_combat_sample[0] > 1):
                # Claims cannot survive an unobserved change in the tile stack.
                self.stacks.clear()
                self.baseline.clear()
                self.births.clear()
                self.deaths.clear()
                self.combat_claims.clear()
                self.loot.clear()
                self.xp_gains.clear()
                self.last_xp = None
                previous = None
            if self.last_xp and xp > self.last_xp:
                self.xp_gains.append(now)
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
            if occupants.get("player"):
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
        self.baseline = current
        self.stacks = {p: s for p, s in self.stacks.items()
                       if now < s.expires and current.get(p, 0) >= s.fresh}
        cutoff = now - MAX_WAIT
        self.xp_gains = [t for t in self.xp_gains if t >= cutoff]
        self.births = [b for b in self.births if b[0] >= cutoff]
        self.deaths = [d for d in self.deaths if d[0] >= cutoff]
        self.loot = [l for l in self.loot if l[0] >= cutoff]
        self.combat_claims = {k: v for k, v in self.combat_claims.items() if k[0] >= cutoff}
        if self.births and self.xp_gains:
            self._claim_combat_bodies(current, game.creatures if game is not None else {}, now)
        self.settle(now)

    def settle(self, now):
        """Apply personal credits to the last observed stack without recounting a map frame."""
        if self.last_event is None:
            return
        current = self.baseline
        # Keep unmatched evidence for the entire correlation window. Closing
        # this batch must leave later kills and credits for their own batch.
        times = [row[0] for rows in (self.births, self.deaths, self.loot) for row in rows]
        if not times:
            self.last_event = None
            return
        deadline = min(times) + EVIDENCE_WINDOW
        deaths = [d for d in self.deaths if d[0] <= deadline]
        personal = [l for l in self.loot if l[0] <= deadline]
        appeared = [b for b in self.births if b[0] <= deadline]
        quiet = now - max(t for t in times if t <= deadline)
        if quiet < SOLO_SETTLE:
            return
        credit = Counter(name for _, name in personal)
        observed = defaultdict(list)
        for _, name, point, _ in deaths:
            observed[name].append(point)
        births = Counter()
        for _, point, n, _ in appeared:
            births[point] += n
        complete = (credit == Counter(name for _, name, _, _ in deaths)
                    and births == Counter(p for _, _, p, _ in deaths))
        if not complete and now <= deadline:
            return
        if quiet < SETTLE and (self.other_player_at >= now - EVIDENCE_WINDOW
                               or self.other_death_at >= now - EVIDENCE_WINDOW
                               or any(not name or not cid for _, name, _, cid in deaths)):
            return
        grants = Counter()
        if "" not in observed:
            for name, points in observed.items():
                if credit[name] == len(points):
                    grants.update(points)
        claimed = Counter(p for at, p in self.combat_claims if at <= deadline)
        for p, n in grants.items():
            remaining = n - claimed[p]
            owned = self.stacks[p].fresh if p in self.stacks else 0
            if remaining > 0 and n == births[p] and current.get(p) == owned + remaining:
                first_seen = next(at for at, pos, _, _ in appeared if pos == p)
                self.stacks[p] = OwnedStack(owned + remaining, now + MAX_WAIT, first_seen, now)
        self.deaths = [d for d in self.deaths if d[0] > deadline]
        self.loot = [l for l in self.loot if l[0] > deadline]
        self.births = [b for b in self.births if b[0] > deadline]
        self.combat_claims = {k: v for k, v in self.combat_claims.items() if k[0] > deadline}
        self.last_event = max((t for t in times if t > deadline), default=None)

    def _claim_combat_bodies(self, current, creatures, now):
        # Match before releasing the Loot batch. Late matching Loot cannot grant
        # the same body again, even after it was already skinned.
        used_targets = set(self.combat_claims.values())
        for at, p, count, cid in self.births:
            if (now-at < SOLO_SETTLE or not cid or count != 1 or creatures.get(cid, {}).get("health", 0) > 0
                    or cid in used_targets
                    or self.other_player_at >= at - 1.5 or abs(self.other_death_at - at) <= 1.5
                    or any(abs(t-at) <= 1.5 and dead_id != cid for t, _, _, dead_id in self.deaths)
                    or sum(n for t, _, n, _ in self.births if abs(t-at) <= 1.5) != 1):
                continue
            gain = next((t for t in self.xp_gains if abs(t-at) <= 1), None)
            owned = self.stacks[p].fresh if p in self.stacks else 0
            if gain is None or current.get(p) != owned + 1:
                continue
            self.combat_claims[at, p] = cid
            self.xp_gains.remove(gain)
            used_targets.add(cid)
            self.stacks[p] = OwnedStack(owned + 1, now + MAX_WAIT, at, now)
            self.from_combat += 1

    def next_body(self, point, items):
        stack = self.stacks.get(point)
        if not stack or body_counts(items)[FRESH] != stack.fresh:
            return None
        for item in sorted(items, key=lambda i: i["stack"], reverse=True):
            flags = item.get("flags", ())
            if item["id"] == FRESH and "container" in flags:
                return "skin", item
            if item["id"] in (SPENT, 8506) and "container" in flags and "not_moveable" not in flags:
                return "uncover", item
            # A decoration, unrelated item or incomplete stack above the corpse
            # cannot be moved on the authority of a Loot notification.
            return None
        return None

    def consumed(self, point):
        stack = self.stacks.get(point)
        if stack:
            stack.fresh -= 1
            if not stack.fresh:
                del self.stacks[point]
