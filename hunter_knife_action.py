"""Hunter Knife 5908 automation using the existing session, map and item actions."""
import asyncio
from dataclasses import dataclass
import time

from cavebot_walking import distance, walkable
from hunter_knife import FRESH, KNIFE, SkinningQueue, body_counts, skinned
from command_delivery import attempted
from adaptive_cavebot import tool_location
from protocol import build_move_item_packet, build_use_item_ex_packet

NAME = "auto_hunter_knife"
WAITING_FOR_LOOT = "Czekam na własny Loot"


def point(cell):
    return cell["x"], cell["y"], cell["z"]


def safe_drop(cell, origin, source):
    spot = point(cell)
    return (distance(spot, origin) <= 1 and distance(spot, source) == 1 and spot != origin
            and walkable(cell) and not cell.get("navigation") and not cell.get("transitions")
            and not cell.get("server_blocking") and not cell.get("occupants")
            and bool(cell["items"]) and "ground" in cell["items"][0]["flags"]
            and all("not_moveable" in i["flags"] and "container" not in i["flags"]
                    for i in cell["items"][1:]))


@dataclass
class Attempt:
    source: tuple
    item: dict
    before: dict
    stamp: float
    sent: float
    drop: tuple | None
    before_on_source: int


class KnifeAutomation:
    def __init__(self, game, controller=None):
        self.game = game
        self.controller = controller
        self.started = time.monotonic()
        self.queue = SkinningQueue()
        self.pending = None
        self.frame = 0
        self.done = 0
        self.status = "Sprawdzam mapę i wyposażenie"
        self.tool_ready = False
        self.last_map = 0

    def panel_status(self):
        if self.status != WAITING_FOR_LOOT:
            return self.status
        if self.game.personal_loot_channel is None:
            return "Czekam na rozpoznanie kanału Loot"
        return "Kanał Loot rozpoznany · czekam na własny wpis o elementalu"

    def fighting(self):
        target_id = self.game.attack_target_id
        if self.controller and self.controller.status == "running" and self.controller.combat.config["enabled"]:
            chosen = self.controller.combat.target or {}
            if chosen.get("health", 0) > 0:
                target_id = chosen.get("id") or target_id
        target = self.game.creatures.get(target_id)
        return bool(target and target.get("health", 0) > 0 and
                    0 <= time.time() - target.get("t", 0) <= 1.5)

    def hold(self):
        now = time.monotonic()
        return (self.tool_ready and now - self.last_map < .65 and not self.fighting()
                and (bool(self.pending) or self.queue.possible_work(self.game.position, now)))

    def rejected(self, received_at):
        if self.pending and self.pending.sent <= received_at:
            self.abandon("Gra odmówiła użycia na tym ciele")

    def abandon(self, reason):
        if self.pending:
            self.queue.stacks.pop(self.pending.source, None)
        self.pending = None
        self.status = reason

    def acknowledge(self, cells, stamp, now):
        a = self.pending
        if not a:
            return
        src = cells.get(a.source)
        if src and stamp > a.stamp:
            counts = body_counts(src["items"])
            if not a.drop and skinned(a.before, counts):
                self.queue.consumed(a.source)
                self.done += 1
                self.pending = None
                self.status = "Obrobiono ciało"
                return
            if a.drop:
                dest = cells.get(a.drop)
                kind = a.item["id"]
                if (dest and counts[FRESH] == a.before[FRESH]
                        and sum(i["id"] == kind for i in src["items"]) == a.before_on_source - 1
                        and any(i["id"] == kind for i in dest["items"])):
                    self.pending = None
                    self.status = "Odsłonięto ciało"
                    return
        if now - a.sent >= 1.5:
            self.abandon("Brak potwierdzenia zmiany pola")

    def next(self, sample, now):
        origin = tuple(self.game.position)
        cells = {point(c): c for c in sample["cells"]
                 if c.get("kind") != "unknown" and "items" in c}
        stamp = sample["captured_at"]
        self.last_map = now
        # Confirm a transformation before the new count invalidates the previous claim.
        self.acknowledge(cells, stamp, now)
        if stamp != self.frame:
            self.queue.observe(cells.values(), now)
            self.frame = stamp
        if self.pending or not self.tool_ready or self.fighting():
            return None
        for p in sorted(self.queue.stacks, key=lambda p: distance(origin,p)):
            if distance(origin, p) > 1:
                continue
            cell = cells.get(p)
            if not cell or cell.get("occupants", 0) > cell.get("occupant_types", {}).get("self", 0):
                continue
            choice = self.queue.next_body(p, cell["items"])
            if not choice:
                continue
            action, item = choice
            drop = None
            if action == "uncover":
                drop = next((q for q, c in cells.items() if safe_drop(c, origin, p)), None)
                if drop is None:
                    self.status = "Brak miejsca na odsunięcie ciała"
                    continue
            count = sum(i["id"] == item["id"] for i in cell["items"])
            return Attempt(p, item, body_counts(cell["items"]), stamp, now, drop, count)
        return None


def fresh_sample(bot):
    monitor = getattr(bot.state, "passive_map_monitor", None)
    sample = getattr(monitor, "_result", None)
    if (sample and sample.get("status") == "ready" and not sample.get("stale")
            and 0 <= time.time() - sample.get("captured_at", 0) <= .6
            and tuple(sample.get("center", ())) == tuple(bot.position)):
        return sample
    return None


async def run_knife(bot):
    from client_equipment import EquipmentReader
    from client_player_state import ClientPlayerReader
    from passive_map_win32 import ReadOnlyClient

    gs = bot.state.game_state
    runtime = KnifeAutomation(gs, getattr(bot.state, "adaptive_cavebot", None))
    player_id, proxy = bot.player_id, bot.state.game_proxy
    gs.hunter_knife = runtime
    client = None
    try:
        reader = None
        next_inventory = 0
        location = None
        def enabled():
            return (bot.is_connected and bot.state.game_state is gs and bot.player_id == player_id
                    and bot.state.game_proxy is proxy and not bot.state.automation_paused
                    and bot.state.settings.get("actions", {}).get(NAME, {}).get("enabled", False))
        while enabled():
            now = time.monotonic()
            sample = fresh_sample(bot)
            if not sample or gs.hp <= 0:
                runtime.status = "Czekam na postać i świeżą mapę"
                runtime.tool_ready = False
                await bot.sleep(.12)
                continue
            if now >= next_inventory:
                next_inventory = now + 1
                try:
                    if reader is None:
                        if client is None:
                            client = ReadOnlyClient()
                        reader = EquipmentReader(ClientPlayerReader(client, bot.player_id), (KNIFE,))
                    location = tool_location(await asyncio.to_thread(reader.snapshot), (KNIFE,))
                except (OSError, ValueError):
                    location = None
                if location and not runtime.tool_ready:
                    runtime.status = WAITING_FOR_LOOT
                runtime.tool_ready = bool(location)
                if not location:
                    runtime.status = "Brak noża 5908 w wyposażeniu lub otwartym plecaku"
            a = runtime.next(sample, now)
            if a and location:
                original = tuple(bot.position)
                def current():
                    if (not enabled() or gs.hunter_knife is not runtime or runtime.pending is not a or runtime.fighting()
                            or gs.hp <= 0 or tuple(bot.position) != original or time.monotonic() - a.sent > .55):
                        return False
                    latest = fresh_sample(bot)
                    if not latest:
                        return False
                    by_pos = {point(c):c for c in latest["cells"]}
                    cell = by_pos.get(a.source)
                    if (not cell or cell.get("kind") == "unknown"
                            or cell.get("occupants", 0) > cell.get("occupant_types", {}).get("self", 0)
                            or runtime.queue.next_body(a.source, cell["items"]) !=
                               ("uncover" if a.drop else "skin", a.item)
                            or body_counts(cell["items"]) != a.before):
                        return False
                    if a.drop:
                        return a.drop in by_pos and safe_drop(by_pos[a.drop], original, a.source)
                    return tool_location(reader.snapshot(), (KNIFE,)) == location

                if a.drop:
                    payload = build_move_item_packet(a.source, a.item["id"], a.item["stack"], a.drop, 1)
                else:
                    where, _, index = location
                    payload = build_use_item_ex_packet(*where, KNIFE, index, *a.source, a.item["stack"],
                                                       target_item_id=FRESH)
                runtime.pending = a
                runtime.status = "Odsuwam ciało" if a.drop else "Używam Hunter Knife"
                await bot.inject_to_server(payload, guard=current)
                if runtime.pending is a and not attempted(bot.last_receipt):
                    runtime.abandon("Pole zmieniło się przed użyciem")
            await bot.sleep(.1)
    finally:
        if gs.hunter_knife is runtime:
            gs.hunter_knife = None
        if client is not None:
            client.close()
