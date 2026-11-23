"""Personal Loot and visible corpse stacks for the single-target Hunter Knife."""
from collections import Counter, defaultdict
from dataclasses import dataclass
import re

KNIFE = 5908
FRESH = 9068
SPENT = 8141
ELEMENTALS = frozenset({"fire elemental", "fire elementalis", "mutant fire elemental",
                        "fury mutant fire", "fury mutant fire elemental"})
LOOT_LINE = re.compile(r"\[([^\[\]\r\n]{1,64})\]: [^\r\n]+")
SETTLE = 1.0
MAX_WAIT = 8.0


def loot_species(text):
    match = LOOT_LINE.fullmatch(text.strip())
    if match and match[1].casefold() in ELEMENTALS:
        return match[1].casefold()
    return None


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
        if runtime and text.casefold().startswith(("you are not owner of this", "you are not the owner of this")):
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


class SkinningQueue:
    """Only grant a tile when every fresh body on it has a matching personal kill."""

    def __init__(self):
        self.stacks = {}
        self.baseline = {}
        self.births = []
        self.deaths = []
        self.loot = []
        self.last_event = None

    def possible_work(self, position, now):
        def nearby(p):
            return (all(type(v) is int for v in p) and p[2] == position[2]
                    and max(abs(p[0]-position[0]), abs(p[1]-position[1])) <= 1)
        if any(nearby(p) and now < s.expires for p, s in self.stacks.items()):
            return True
        return any(nearby(p) and 0 <= now-at <= SETTLE + .6 for at, _, p in self.deaths)

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
            return
        point = (row.get("x"), row.get("y"), row.get("z"))
        self.deaths.append((now, name, point))
        self.last_event = now

    def observe(self, cells, now):
        current = {}
        for c in cells:
            if c.get("kind") == "unknown" or "items" not in c:
                continue
            p = (c["x"], c["y"], c["z"])
            count = sum(i["id"] == FRESH for i in c["items"])
            current[p] = count
            if p in self.baseline and count > self.baseline[p]:
                self.births.append((now, p, count - self.baseline[p]))
                self.last_event = now
        self.baseline = current
        self.stacks = {p: s for p, s in self.stacks.items()
                       if now < s.expires and current.get(p, 0) >= s.fresh}
        cutoff = now - MAX_WAIT
        self.births = [b for b in self.births if b[0] >= cutoff]
        self.deaths = [d for d in self.deaths if d[0] >= cutoff]
        self.loot = [l for l in self.loot if l[0] >= cutoff]
        if self.last_event is None or now - self.last_event < SETTLE:
            return
        credit = Counter(name for _, name in self.loot)
        observed = defaultdict(list)
        for _, name, point in self.deaths:
            observed[name].append(point)
        births = Counter()
        for _, point, n in self.births:
            births[point] += n
        grants = Counter()
        times = [v[0] for source in (self.deaths, self.loot, self.births) for v in source]
        if times and max(times) - min(times) <= 1.5 and "" not in observed:
            for name, points in observed.items():
                if credit[name] == len(points):
                    grants.update(points)
        for p, n in grants.items():
            owned = self.stacks[p].fresh if p in self.stacks else 0
            if n == births[p] and current.get(p) == owned + n:
                self.stacks[p] = OwnedStack(owned + n, now + MAX_WAIT)
        self.deaths.clear()
        self.loot.clear()
        self.births.clear()
        self.last_event = None

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
