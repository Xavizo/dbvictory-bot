"""Manual, one-item drags selected from the current local map."""
import asyncio
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


def backpack_id(player_id):
    from client_equipment import EquipmentReader
    from client_player_state import ClientPlayerReader
    from passive_map_win32 import ReadOnlyClient

    client = ReadOnlyClient()
    try:
        inventory = EquipmentReader(ClientPlayerReader(client, player_id), ()).snapshot()
        return inventory["slots"].get(EQUIPPED_BACKPACK[1], 0)
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


def nearby_items(sample):
    center = tuple(sample["center"])
    for cell in sample["cells"]:
        pos = (cell["x"], cell["y"], cell["z"])
        if (pos[2] != center[2] or max(abs(pos[0]-center[0]), abs(pos[1]-center[1])) > 1
                or cell["kind"] in {"unknown", "no_ground"}):
            continue
        items = cell["items"]
        top = max((item["stack"] for item in items), default=-1)
        for item in items:
            if FIXED.isdisjoint(item["flags"]):
                yield GroundItem(center, pos, item["id"], item["stack"],
                                 "pickupable" in item["flags"], item["stack"] != top)


class NearbyItemTest:
    def __init__(self, state):
        self.state = state
        self.lease = SessionLease(state)
        self.tokens = {}
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
                             pickup=item.pickup, covered=item.covered))
        self.tokens = tokens
        return dict(center=sample["center"], rows=rows)

    async def move(self, token, destination):
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
            def valid():
                if not task_alive(owner) or self.state.automation_paused or self.lease.game.hp <= 0:
                    return False
                try:
                    if item not in nearby_items(self.live_map()):
                        return False
                except ValueError:
                    return False
                return (bag_revision is None or
                        session_observations(self.lease.game).revision_for(("inventory",)) == bag_revision)

            if not valid():
                raise ValueError("Pozycja, przedmiot lub stan sesji uległy zmianie.")
            if destination == "backpack":
                if not item.pickup:
                    raise ValueError("Tego przedmiotu nie można podnieść do plecaka.")
                bag_revision = session_observations(self.lease.game).revision_for(("inventory",))
                if not await asyncio.to_thread(backpack_id, self.lease.player):
                    raise ValueError("Slot plecaka jest pusty.")
                target = EQUIPPED_BACKPACK
            else:
                target = item.center
                if item.position == target:
                    raise ValueError("Przedmiot już leży pod postacią.")
            if not valid():
                raise ValueError("Dane przedmiotu lub wyposażenia uległy zmianie.")
            drag = build_move_item_packet(item.position, item.item_id, item.stack, target, 1)
            receipt = await delivery(await self.lease.proxy.inject_to_server(drag, guard=valid))
            if receipt is None or receipt.status == "unknown":
                return "Brak potwierdzenia wysłania. Sprawdź położenie przedmiotu."
            if receipt.status != "dispatched":
                raise ValueError("Ruch nie został wysłany. Sprawdź aktualną listę.")
            return "Wysłano próbę przeniesienia 1 szt. " + ("do plecaka." if destination == "backpack" else "pod postać.")
        finally:
            self.busy = False
