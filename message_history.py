"""Bounded session histories. Rows are appended on their owner's thread."""
import re
import time
from collections import deque
from itertools import islice

from potions import ITEMS, STOCK_REPORT, USE_REFUSAL, next_senzu_stock

CAPACITY = 100_000
INCOMING_HIT = re.compile(
    r"You lose (?P<amount>[0-9]{1,10}) (?P<resource>ki points? blocking an attack by|hitpoints? due to an attack by) "
    r"(?P<attacker>[^\r\n]+?)\.?", re.IGNORECASE,
)


class MessageHistory:
    def __init__(self):
        self.general = deque(maxlen=CAPACITY)
        self.combat = deque(maxlen=CAPACITY)
        self.counts = {"general": 0, "combat": 0}
        self._stock_batch = None
        self._senzu_stock = None
        self._inventory_since = 0.

    def record_server(self, msg_type, text, at, batch=None):
        row = {"at": at, "type": msg_type, "text": text}
        kind = None
        if INCOMING_HIT.fullmatch(text):
            kind = "damage_received"
        elif msg_type == 25 and STOCK_REPORT.fullmatch(text):
            kind = "stock_report"
            self._stock_batch = batch
        elif msg_type == 26:
            refusal = USE_REFUSAL.fullmatch(text)
            if refusal and (refusal[1] or batch is not None and batch == self._stock_batch):
                kind = "item_refused"
        if kind:
            row["kind"] = kind
            self.combat.append(row)
            self.counts["combat"] += 1
        else:
            self.general.append(row)
            self.counts["general"] += 1

    def record_item_attempt(self, item_id, mode, source, gs):
        self.combat.append({"at": time.time(), "kind": "item_attempt", "source": source,
                            "item_id": item_id, "item": ITEMS[item_id], "mode": mode,
                            "hp": gs.hp, "max_hp": gs.max_hp, "ki": gs.mana, "max_ki": gs.max_mana,
                            "stats_at": gs.stats_updated_at})
        self.counts["combat"] += 1

    def invalidate_inventory(self):
        self._inventory_since = time.monotonic()
        self._senzu_stock = None

    def observe_stock(self, kind, text, batch, received_at):
        if received_at is None or received_at < self._inventory_since:
            return
        self._senzu_stock, used_at = next_senzu_stock(self._senzu_stock, kind, text, batch, received_at)
        if used_at is not None:
            wall_offset = time.time() - time.monotonic()
            self.combat.append({"kind": "senzu_stock_decreased", "at": wall_offset + received_at,
                                "prior_report_at": wall_offset + used_at, "item_id": 8465,
                                "remaining": self._senzu_stock[0]})
            self.counts["combat"] += 1

    def merge(self, batch):
        # A worker owns only its newly decoded entries. Never copy the live history.
        self.general.extend(batch.general)
        self.combat.extend(batch.combat)
        self.counts["general"] += len(batch.general)
        self.counts["combat"] += len(batch.combat)

    def page(self, channel, limit=50, before=None):
        if channel not in ("general", "combat"):
            raise ValueError("Wybierz kanał general lub combat.")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Liczba wpisów musi wynosić od 1 do 500.")
        if before is not None and (type(before) is not int or before < 1):
            raise ValueError("Kursor musi być dodatnią liczbą całkowitą.")
        buffer = self.general if channel == "general" else self.combat
        latest = self.counts[channel]
        oldest = latest - len(buffer) + 1
        end = latest if before is None else min(latest, before - 1)
        size = min(limit, max(0, end - oldest + 1))
        offset = latest - end
        rows = []
        if size:
            for i, row in enumerate(islice(reversed(buffer), offset, offset + size)):
                entry = {"id": end - i, **row}
                if row.get("kind") == "damage_received":
                    hit = INCOMING_HIT.fullmatch(row["text"])
                    entry.update(amount=int(hit["amount"]), attacker=hit["attacker"],
                                 resource="ki" if hit["resource"].lower().startswith("ki") else "hp")
                elif row.get("kind") == "stock_report":
                    report = STOCK_REPORT.fullmatch(row["text"])
                    entry.update(reported_stock=int(report[1]), item=report[2])
                rows.append(entry)
        rows.reverse()
        return {"channel": channel, "capacity": buffer.maxlen, "retained": len(buffer),
                "oldest_id": oldest if buffer else None, "latest_id": latest or None,
                "next_before": rows[0]["id"] if size and rows[0]["id"] > oldest else None, "rows": rows}
