"""Manual, one-item drags selected from the current local map."""
import asyncio
import logging
import time
from dataclasses import dataclass
from uuid import uuid4

from command_delivery import delivery
from protocol import build_move_item_packet
from session_lifetime import SessionLease, task_alive
from session_observations import session_observations

# Dropping onto the equipped backpack is independent of container window order.
EQUIPPED_BACKPACK = (65535, 3, 0)
FIXED = {"ground", "ground_border", "not_moveable"}
TERRAIN = {"ground", "ground_border", "on_bottom", "on_top"}
RESULT_WAIT = 1.2
MOVE_REFUSALS = {
    "sorry, not possible", "not possible", "you cannot move this object",
    "you cannot move that object", "you cannot throw there", "you can't throw there",
    "you cannot take this object", "this container is full", "container is full",
    "you do not have enough capacity", "you don't have enough capacity",
}


def exposed_layer(items):
    """OTClient selects the first ordinary thing in stack order when dragging."""
    for item in sorted(items, key=lambda entry: entry["stack"]):
        if not TERRAIN.isdisjoint(item["flags"]):
            continue
        if "not_moveable" not in item["flags"]:
            return item["stack"]
        # The client falls back to the preceding thing, which may be a creature
        # or fixed terrain. Only an actual movable item may be offered here.
        return next((prev["stack"] for prev in items if prev["stack"] == item["stack"] - 1
                     and FIXED.isdisjoint(prev["flags"])), None)
    return None


def item_count_at(sample, position, item_id):
    for cell in sample["cells"]:
        if (cell["x"], cell["y"], cell["z"]) == position and cell["kind"] not in {"unknown", "no_ground"}:
            return sum(entry["id"] == item_id for entry in cell["items"])
    return None


def recent_refusal(messages, previous):
    for message in reversed(messages):
        if message is previous:
            break
        if isinstance(message.get("type"), int) and message.get("text", "").strip().casefold().rstrip(".!") in MOVE_REFUSALS:
            return message["text"].strip()
    return None


def read_bags(player_id):
    from client_equipment import EquipmentReader
    from client_player_state import ClientPlayerReader
    from passive_map_win32 import ReadOnlyClient

    client = ReadOnlyClient()
    try:
        windows = {}
        inventory = EquipmentReader(ClientPlayerReader(client, player_id), ()).snapshot(open_windows=windows)
        return inventory["slots"].get(EQUIPPED_BACKPACK[1], 0), windows
    finally:
        client.close()


@dataclass(frozen=True)
class GroundItem:
    center: tuple
    position: tuple
    item_id: int
    stack: int
    pickup: bool
    covered: bool
    stackable: bool


def nearby_items(sample):
    center = tuple(sample["center"])
    for cell in sample["cells"]:
        pos = (cell["x"], cell["y"], cell["z"])
        if (pos[2] != center[2] or max(abs(pos[0]-center[0]), abs(pos[1]-center[1])) > 1
                or cell["kind"] in {"unknown", "no_ground"}):
            continue
        items = cell["items"]
        top = exposed_layer(items)
        for item in items:
            if FIXED.isdisjoint(item["flags"]):
                yield GroundItem(center, pos, item["id"], item["stack"],
                                 "pickupable" in item["flags"], item["stack"] != top, "stackable" in item["flags"])


class NearbyItemTest:
    def __init__(self, state):
        self.state = state
        self.lease = SessionLease(state)
        self.tokens = {}
        self.windows = {}
        self.busy = False

    def live_map(self):
        monitor = getattr(self.state, 'passive_map_monitor', None)
        if not self.lease.valid() or not self.lease.proxy.logged_in or not self.lease.player or not monitor:
            raise ValueError("Zaloguj postać, aby odczytać przedmioty.")
        sample = monitor.snapshot()
        if (sample.get("status") != "ready" or sample.get("preview") or sample.get("stale")
                or not 0 <= time.time() - sample.get("captured_at", 0) <= 1
                or tuple(sample.get("center", ())) != tuple(self.lease.game.position)):
            raise ValueError("Czekam na świeżą mapę zgodną z pozycją postaci.")
        return sample

    def snapshot(self):
        try:
            sample = self.live_map()
        except ValueError:
            self.tokens.clear()
            raise
        tokens = {}
        rows = []
        for item in nearby_items(sample):
            token = self.tokens.get(item) or uuid4().hex
            tokens[item] = token
            rows.append(dict(token=token, position=item.position, item_id=item.item_id, stack=item.stack,
                             pickup=item.pickup, covered=item.covered, stackable=item.stackable))
        self.tokens = tokens
        return dict(center=sample["center"], rows=rows)

    async def containers(self):
        self.live_map()
        _, windows = await asyncio.to_thread(read_bags, self.lease.player)
        self.live_map()
        choices = {}
        visible = []
        for number, (identity, size) in sorted(windows.items()):
            key = (number, identity)
            choices[key] = self.windows.get(key) or uuid4().hex
            visible.append(dict(token=choices[key], number=number, size=size))
        self.windows = choices
        return visible

    async def outcome(self, item, destination, before, previous_message):
        source_before = item_count_at(before, item.position, item.item_id)
        feet_before = item_count_at(before, item.center, item.item_id)
        deadline = time.monotonic() + RESULT_WAIT
        refusal = None
        while self.lease.valid() and time.monotonic() < deadline:
            refusal = recent_refusal(getattr(self.lease.game, "messages", ()), previous_message) or refusal
            try:
                sample = self.live_map()
            except ValueError:
                sample = None
            if sample and tuple(sample["center"]) == item.center and sample["captured_at"] > before["captured_at"]:
                source = item_count_at(sample, item.position, item.item_id)
                if destination == "feet":
                    feet = item_count_at(sample, item.center, item.item_id)
                    if feet is not None and feet_before is not None and feet > feet_before:
                        if source is not None and source_before is not None and source < source_before:
                            return "Mapa potwierdza przeniesienie przedmiotu pod postać."
                        if item.stackable:
                            return "Przedmiot pojawił się pod postacią. Liczba sztuk na polu źródłowym nie jest potwierdzona."
                elif source is not None and source_before is not None and source < source_before:
                    return "Przedmiot zniknął z pola źródłowego po próbie włożenia do plecaka."
            if refusal:
                raise ValueError(f"Komunikat gry po próbie przeniesienia: {refusal}")
            await asyncio.sleep(.1)
        if item.stackable:
            return "Wysłano ruch jednej sztuki. Zmiana liczby sztuk w stosie nie została potwierdzona."
        return "Wysłano ruch. Brak potwierdzenia zmiany położenia przedmiotu."

    async def move(self, token, destination, container=None):
        if self.busy:
            raise ValueError("Poczekaj na wynik poprzedniego ruchu.")
        item = next((i for i, t in self.tokens.items() if t == token), None)
        if item is None or item.covered or destination not in ("backpack", "feet"):
            raise ValueError("Wybierz ponownie odsłonięty przedmiot.")
        self.tokens.pop(item)
        self.busy = True
        owner = asyncio.current_task()
        try:
            bag_revision = None
            window_epoch = getattr(self.lease.game, "container_view_epoch", 0)
            def valid():
                if not task_alive(owner) or self.state.automation_paused or self.lease.game.hp <= 0:
                    return False
                try:
                    if item not in nearby_items(self.live_map()):
                        return False
                except ValueError:
                    return False
                return (bag_revision is None or
                        (session_observations(self.lease.game).revision_for(("inventory",)) == bag_revision
                         and getattr(self.lease.game, "container_view_epoch", 0) == window_epoch))

            if not valid():
                raise ValueError("Pozycja, przedmiot lub stan sesji uległy zmianie.")
            if destination == "backpack":
                if not item.pickup:
                    raise ValueError("Tego przedmiotu nie można podnieść do plecaka.")
                bag_revision = session_observations(self.lease.game).revision_for(("inventory",))
                bag, windows = await asyncio.to_thread(read_bags, self.lease.player)
                if container is None:
                    if not bag:
                        raise ValueError("Slot plecaka jest pusty.")
                    target = EQUIPPED_BACKPACK
                else:
                    key = next((key for key, value in self.windows.items() if value == container), None)
                    window = windows.get(key[0]) if key else None
                    if not window or window[0] != key[1]:
                        raise ValueError("Wybrany kontener został zamknięty lub zmieniony. Wybierz go ponownie.")
                    target = (65535, 0x40 | key[0], window[1])
            else:
                target = item.center
                if item.position == target:
                    raise ValueError("Przedmiot już leży pod postacią.")
            if not valid():
                raise ValueError("Dane przedmiotu lub wyposażenia uległy zmianie.")
            before = self.live_map()
            messages = getattr(self.lease.game, "messages", ())
            previous_message = messages[-1] if messages else None
            width = getattr(self.lease.game, "manual_move_count_bytes", 1)
            drag = build_move_item_packet(item.position, item.item_id, item.stack, target, 1, count_bytes=width)
            receipt = await delivery(await self.lease.proxy.inject_to_server(drag, guard=valid))
            logging.getLogger(__name__).info(
                "Manual item move id=%s source=%s layer=%s target=%s count_bytes=%s delivery=%s",
                item.item_id, item.position, item.stack, target, width, getattr(receipt, "status", None))
            if receipt is None or receipt.status == "unknown":
                return "Brak potwierdzenia wysłania. Sprawdź położenie przedmiotu."
            if receipt.status != "dispatched":
                raise ValueError("Ruch nie został wysłany. Sprawdź aktualną listę.")
            try:
                result = await self.outcome(item, destination, before, previous_message)
            except ValueError as exc:
                logging.getLogger(__name__).info("Manual item move id=%s: %s", item.item_id, exc)
                raise
            logging.getLogger(__name__).info("Manual item move id=%s: %s", item.item_id, result)
            return result
        finally:
            self.busy = False
