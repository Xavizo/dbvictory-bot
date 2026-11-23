"""Hunter Knife 5908 automation using the existing session, map and item actions."""
import asyncio
import logging
from dataclasses import dataclass
import time

from cavebot_walking import distance, walkable
from item_observations import observed_inputs, wait_for_inputs
from hunter_knife import FRESH, KNIFE, SkinningQueue, body_counts, skinned
from command_delivery import CommandReceipt, attempted, confirm
from adaptive_cavebot import tool_location
from protocol import build_move_item_packet, build_use_item_ex_packet

NAME = "auto_hunter_knife"
INPUTS = ("map", "position", "target", "death", "loot", "inventory", "health")
ATTEMPT_VALID_FOR = .55
WAITING_FOR_BODY = "Czekam na własne ciało elementala w zasięgu"


def point(cell):
    return cell["x"], cell["y"], cell["z"]


def safe_drop(cell, origin, source):
    spot = point(cell)
    return (distance(spot, origin) <= 1 and distance(spot, source) == 1 and spot != origin
            and walkable(cell) and not cell.get("navigation") and not cell.get("transitions")
            and not cell.get("server_blocking") and not cell.get("occupants")
            and bool(cell.get("items")) and "ground" in cell["items"][0]["flags"]
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
    receipt: CommandReceipt | None = None
    recent_spell: tuple[str, int] | None = None


class KnifeAutomation:
    def __init__(self, game, controller=None):
        self.game = game
        self.controller = controller
        self.started = time.monotonic()
        self.queue = SkinningQueue()
        self.pending = None
        self.frame = 0
        self.done = 0
        self.unattributed = 0
        self.last_timing_ms = None
        self.last_issue = None
        self.status = "Sprawdzam mapę i wyposażenie"
        self.tool_ready = False
        self.last_map = 0
        self.actionable = False
        self.group_progress_at = None
        self.deferred_tiles = set()

    def panel_status(self):
        return self.status

    def fighting(self):
        target_id = self.game.attack_target_id
        if self.cavebot_running and self.controller.combat.config["enabled"]:
            chosen = self.controller.combat.target or {}
            if chosen.get("health", 0) > 0:
                target_id = chosen.get("id") or target_id
        target = self.game.creatures.get(target_id)
        return bool(target and target.get("health", 0) > 0 and
                    0 <= time.time() - target.get("t", 0) <= 1.5)

    @property
    def cavebot_running(self):
        return self.controller is not None and self.controller.status == "running"

    def hold(self):
        now = time.monotonic()
        if not self.tool_ready or now - self.last_map >= .65:
            return False
        if self.fighting():
            # Only a group of confirmed, usable bodies can briefly defer the next
            # route step in combat. A missing acknowledgement cannot renew it.
            return (self.group_progress_at is not None
                    and now - self.group_progress_at < ATTEMPT_VALID_FOR
                    and (self.actionable or (self.pending is not None and self.pending.drop is None
                                             and distance(self.pending.source, self.game.position) <= 1)))
        return (self.actionable or self.pending is not None
                or self.queue.possible_work(self.game.position, now, include_owned=False))

    def rejected(self, received_at):
        if self.pending and self.pending.sent <= received_at:
            self.abandon("Gra odmówiła użycia na tym ciele")

    def abandon(self, reason):
        if self.pending:
            attempt = self.pending
            self.last_issue = dict(position=attempt.source, reason=reason,
                                   action="uncover" if attempt.drop else "skin")
            recent_spell = getattr(attempt, "recent_spell", None)
            if recent_spell:
                words, elapsed_ms = recent_spell
                self.last_issue.update(spell=words, since_spell_ms=elapsed_ms)
            logging.getLogger(__name__).info("Hunter Knife 5908: %s", self.last_issue)
            self.queue.stacks.pop(attempt.source, None)
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
                stack = self.queue.stacks.get(a.source)
                manual = getattr(self.game, "knife_manual_use_at", None)
                if manual is not None and a.sent <= manual <= now:
                    self.queue.consumed(a.source)
                    self.pending = None
                    self.unattributed += 1
                    self.status = "Ciało zmienione przy jednoczesnym ręcznym użyciu"
                    return
                delivered = a.receipt.dispatched_at if a.receipt else None
                if stack and stack.appeared is not None and stack.assigned is not None:
                    self.last_timing_ms = {
                        "recognition": round(1000 * (stack.assigned - stack.appeared)),
                        "wait_for_use": round(1000 * (a.sent - stack.assigned)),
                        "confirmation": round(1000 * max(0, now - (delivered if delivered is not None else a.sent))),
                    }
                    if delivered is not None:
                        self.last_timing_ms["delivery_wait"] = round(1000 * max(0, delivered - a.sent))
                confirm(a.receipt, "corpse_transformed")
                self.queue.consumed(a.source)
                self.done += 1
                if self.group_progress_at is not None:
                    self.group_progress_at = now
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
        self.actionable = False
        if stamp < self.frame:
            return None
        self.last_map = now
        # Confirm a transformation before the new count invalidates the previous claim.
        self.acknowledge(cells, stamp, now)
        if stamp != self.frame:
            self.deferred_tiles.clear()
            target_id = self.game.attack_target_id
            if self.cavebot_running and self.controller.combat.config["enabled"]:
                target_id = self.controller.combat.sent_target or target_id
            self.queue.observe(cells.values(), now, game=self.game, target_id=target_id, sampled_at=stamp)
            self.frame = stamp
        else:
            self.queue.settle(now)
        if self.pending or not self.tool_ready:
            return None
        fighting = self.fighting()
        candidates = self.candidates(cells, origin, fighting, stamp, now)
        first = next(candidates, None)
        self.actionable = first is not None
        if not self.cavebot_running or not fighting or not self.queue.stacks:
            self.group_progress_at = None
        elif first and self.group_progress_at is None:
            if first.before[FRESH] > 1 or next(candidates, None) is not None:
                self.group_progress_at = now
        return first

    def candidates(self, cells, origin, fighting, stamp, now):
        for p in sorted(self.queue.stacks, key=lambda p: distance(origin, p)):
            if p in self.deferred_tiles or distance(origin, p) > 1:
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
                if fighting:
                    continue
                drop = next((q for q, c in cells.items() if safe_drop(c, origin, p)), None)
                if drop is None:
                    self.status = "Brak miejsca na odsunięcie ciała"
                    continue
            count = sum(i["id"] == item["id"] for i in cell["items"])
            attempt = Attempt(p, item, body_counts(cell["items"]), stamp, now, drop, count)
            spell = getattr(getattr(self.controller, "combat", None), "last_spell_attempt", None)
            if spell and self.cavebot_running:
                # Context for diagnosis only. A spell attempt does not establish an item cooldown.
                elapsed = time.time() - spell["at"]
                if 0 <= elapsed <= 3:
                    attempt.recent_spell = (spell["words"], round(elapsed * 1000))
            yield attempt


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
    generation = getattr(proxy, 'session_generation', None)
    gs.hunter_knife = runtime
    client = None
    try:
        reader = None
        next_refresh = 0
        next_inventory = 0
        location = None
        def enabled():
            return (bot.is_connected and bot.state.game_state is gs and bot.player_id == player_id
                    and bot.state.game_proxy is proxy and getattr(proxy, 'session_generation', None) == generation
                    and not bot.state.automation_paused
                    and bot.state.settings.get("actions", {}).get(NAME, {}).get("enabled", False))
        while enabled():
            cursor = observed_inputs(gs, INPUTS)
            now = time.monotonic()
            sample = fresh_sample(bot)
            if not sample or gs.hp <= 0:
                runtime.status = ("Czekam na odczyt HP postaci" if gs.hp <= 0
                                  else "Czekam na mapę zgodną z pozycją postaci")
                if not sample and now >= next_refresh:
                    next_refresh = now + .25
                    refresh = getattr(getattr(bot.state, "passive_map_monitor", None), "request_refresh", None)
                    if refresh:
                        refresh()
                runtime.tool_ready = False
                await wait_for_inputs(bot, INPUTS, cursor, .12)
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
            if not enabled():
                break
            if location and not runtime.tool_ready:
                runtime.status = WAITING_FOR_BODY
            runtime.tool_ready = bool(location)
            if not location:
                runtime.status = "Brak noża 5908 w wyposażeniu lub otwartym plecaku"
            runtime.controller = getattr(bot.state, "adaptive_cavebot", None)
            a = runtime.next(sample, now)
            if a and location:
                original = tuple(bot.position)
                def current():
                    if (not enabled() or gs.hunter_knife is not runtime or runtime.pending is not a
                            or (a.drop is not None and runtime.fighting())
                            or gs.hp <= 0 or tuple(bot.position) != original or time.monotonic() - a.sent > ATTEMPT_VALID_FOR):
                        return False
                    latest = fresh_sample(bot)
                    if not latest:
                        return False
                    by_pos = {point(c):c for c in latest["cells"]}
                    cell = by_pos.get(a.source)
                    if (not cell or cell.get("kind") == "unknown" or "items" not in cell
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
                if runtime.pending is a:
                    receipt = bot.last_receipt
                    if attempted(receipt):
                        if isinstance(receipt, CommandReceipt):
                            a.receipt = receipt
                    elif enabled():
                        # A local cancellation did not touch the body. Keep its claim;
                        # require a newer observation before selecting this tile again.
                        runtime.pending = None
                        runtime.actionable = False
                        runtime.deferred_tiles.add(a.source)
                        runtime.status = ("Sprawdzam zasięg po kroku" if tuple(bot.position) != original
                                          else "Pole zmieniło się przed użyciem")
                    else:
                        runtime.abandon("Pole zmieniło się przed użyciem")
            await wait_for_inputs(bot, INPUTS, cursor, .1)
    finally:
        if gs.hunter_knife is runtime:
            gs.hunter_knife = None
        if client is not None:
            client.close()
