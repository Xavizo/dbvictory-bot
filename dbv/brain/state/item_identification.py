"""Item identification: report the ID of each item the player looks at.

A passive diagnostic. It reads the client's own LOOK packets, keeps the last few
IDs for the panel, and can echo "Item Id: N" to the game's status bar.
"""
from collections import deque
import time

from dbv.client_bridge.protocol import ClientOpcode, PacketWriter, ServerOpcode

STATUS_BAR = 26  # the line that shows "You are full."
CREATURE_ID = 99  # LOOK sends this "item" for creatures; it is not an item.
INVENTORY_X = 0xFFFF  # positions with this x are inventory or container slots
CAPACITY = 20


def status_message(item_id: int) -> bytes:
    pw = PacketWriter()
    pw.write_u8(ServerOpcode.TEXT_MESSAGE)
    pw.write_u8(STATUS_BAR)
    pw.write_string(f"Item Id: {item_id}")
    return pw.data


class ItemIdentification:
    def __init__(self, enabled: bool = True, clock=time.time):
        self.enabled = enabled
        self.recent = deque(maxlen=CAPACITY)
        self._clock = clock

    def record_client_packet(self, opcode, reader):
        """Return the looked-at item ID, or None when the packet is not a Look at an item."""
        if not self.enabled or opcode != ClientOpcode.LOOK or reader.remaining < 7:
            return None
        x, y, z = reader.read_position()
        item_id = reader.read_u16()
        if item_id in (0, CREATURE_ID):
            return None
        where = "inventory" if x == INVENTORY_X else "map"
        self.recent.append({"item_id": item_id, "where": where,
                            "position": [x, y, z] if where == "map" else None,
                            "time": self._clock()})
        return item_id

    def snapshot(self) -> dict:
        return {"enabled": self.enabled, "recent": list(reversed(self.recent))}
