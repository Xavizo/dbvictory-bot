"""Priority healing rules and bounded passive observations of manual item use."""
import math
import random
import re
import threading
import time
from collections import deque

from protocol import build_use_item_packet, build_use_on_creature_packet

ITEMS = {10161: "Special Health Potion", 10160: "Special Ki Potion", 8465: "Senzu"}
POTIONS = {"health": (10161, "hp", "max_hp"), "ki": (10160, "mana", "max_mana")}
LEGACY_HEALING_ACTIONS = frozenset({"auto_senzu", "mana_recovery_20", "mana_recovery_70"})
DEFAULTS = {"shared_cooldown": 0.0}
for _kind, _threshold in (("health", 60.0), ("ki", 40.0)):
    DEFAULTS.update({f"{_kind}_enabled": True, f"{_kind}_threshold": _threshold,
                     f"{_kind}_jitter": 5.0, f"{_kind}_interval": 1.0,
                     f"{_kind}_cooldown": 0.0, f"{_kind}_mode": "use"})


def _number(value, label, low, high):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not low <= value <= high):
        raise ValueError(f"{label} must be a finite number between {low} and {high}")


def rules_from_config(cfg):
    """Adapt old saved settings in memory; the first explicit save writes rules."""
    if "rules" in cfg:
        return cfg["rules"]
    return [{"id": kind, "enabled": cfg[f"{kind}_enabled"],
             "resource": "hp" if kind == "health" else "ki", "item_id": POTIONS[kind][0],
             **{field: cfg[f"{kind}_{field}"] for field in
                ("threshold", "jitter", "interval", "cooldown", "mode")}}
            for kind in POTIONS]


def rule_cooldown(rule, cfg):
    return cfg["shared_cooldown"] if rule["cooldown"] is None else rule["cooldown"]


def validate_config(values, *, require_cooldowns=False):
    if not isinstance(values, dict):
        raise ValueError("Healing configuration must be an object")
    cfg = {**DEFAULTS, **values}
    _number(cfg["shared_cooldown"], "shared_cooldown", 0, 3600)
    rules = rules_from_config(cfg)
    if not isinstance(rules, list) or len(rules) > 32:
        raise ValueError("rules must be a list with at most 32 entries")
    ids = set()
    fields = {"id", "enabled", "resource", "item_id", "threshold", "jitter", "interval", "cooldown", "mode"}
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != fields:
            raise ValueError("A rule must contain id, enabled, resource, item_id, threshold, jitter, interval, cooldown and mode")
        rid = rule["id"]
        if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", rid) or rid in ids:
            raise ValueError("Rule IDs must be unique, 1–64 letters, digits, underscores or hyphens")
        ids.add(rid)
        if type(rule["enabled"]) is not bool or rule["resource"] not in ("hp", "ki") or rule["mode"] not in ("use", "self"):
            raise ValueError(f"Invalid enabled, resource or mode in rule {rid}")
        if type(rule["item_id"]) is not int or rule["item_id"] not in ITEMS:
            raise ValueError(f"Unsupported healing item in rule {rid}")
        for field, low, high in (("threshold", 1, 100), ("jitter", 0, 30), ("interval", .1, 3600), ("cooldown", 0, 3600)):
            if field != "cooldown" or rule[field] is not None:
                _number(rule[field], f"{rid}.{field}", low, high)
        if require_cooldowns and rule["enabled"] and rule_cooldown(rule, cfg) <= 0:
            raise ValueError(f"Set the verified cooldown for rule {rid} ({ITEMS[rule['item_id']]}) before enabling")
    if "rules" in cfg:
        cfg["rules"] = [dict(rule) for rule in rules]
    return cfg


class PotionScheduler:
    """First eligible rule wins. Cooldowns belong to items, never to row IDs."""
    def __init__(self, uniform=random.uniform):
        self.uniform = uniform
        self.thresholds = {}
        self.threshold_config = {}
        self.last_use = {}
        self.last_any = -math.inf
        self.last_gap = {}
        self.last_shared = 0.0
        self.last_stats = {}

    def next_rule(self, cfg, gs, now, wall_time):
        if not (gs.max_hp > 0 and 0 < gs.hp <= gs.max_hp):
            return None
        stamp = getattr(gs, "stats_updated_at", 0)
        if not stamp or not 0 <= wall_time - stamp <= 5:
            return None
        if now - self.last_any < max(cfg["shared_cooldown"], self.last_shared):
            return None
        rules = rules_from_config(cfg)
        active_ids = {r["id"] for r in rules}
        self.thresholds = {k: v for k, v in self.thresholds.items() if k in active_ids}
        self.threshold_config = {k: v for k, v in self.threshold_config.items() if k in active_ids}
        for rule in rules:
            if not rule["enabled"]:
                continue
            rid, item = rule["id"], rule["item_id"]
            signature = (rule["threshold"], rule["jitter"], rule["resource"], item)
            if self.threshold_config.get(rid) != signature or rid not in self.thresholds:
                center, spread = signature[:2]
                self.thresholds[rid] = self.uniform(max(1, center - spread), min(100, center + spread))
                self.threshold_config[rid] = signature
            current, maximum = ("hp", "max_hp") if rule["resource"] == "hp" else ("mana", "max_mana")
            value, cap = getattr(gs, current), getattr(gs, maximum)
            if (cap <= 0 or not math.isfinite(value) or not math.isfinite(cap)
                    or value < 0 or value / cap * 100 >= self.thresholds[rid]):
                continue
            # A second condition for the same item cannot shorten its cooldown.
            item_cooldown = max((rule_cooldown(r, cfg) for r in rules if r["enabled"] and r["item_id"] == item), default=0)
            gap = max(item_cooldown, rule["interval"], self.last_gap.get(item, 0))
            if now - self.last_use.get(item, -math.inf) < gap or stamp <= self.last_stats.get(item, 0):
                continue
            if rule["mode"] == "self" and not getattr(gs, "player_id", 0):
                continue
            return rule
        return None

    def next_potion(self, cfg, gs, now, wall_time):
        """Compatibility for the original two-potion scheduler callers."""
        rule = self.next_rule(cfg, gs, now, wall_time)
        return rule["id"] if rule else None

    def record_item_use(self, item_id, cfg, now, stamp, *, rule=None):
        if item_id not in ITEMS:
            return
        relevant = [r for r in rules_from_config(cfg) if r["item_id"] == item_id]
        cooldown = max((rule_cooldown(r, cfg) for r in relevant if r["enabled"]), default=0)
        interval = rule["interval"] if rule else max((r["interval"] for r in relevant if r["enabled"]), default=0)
        # Preserve any longer pending deadline even across edits/manual attempts.
        remaining = max(0, self.last_use.get(item_id, -math.inf) + self.last_gap.get(item_id, 0) - now)
        shared_remaining = max(0, self.last_any + self.last_shared - now)
        self.last_use[item_id] = self.last_any = now
        self.last_gap[item_id] = max(cooldown, interval, remaining)
        self.last_shared = max(cfg["shared_cooldown"], shared_remaining)
        self.last_stats[item_id] = stamp
        for entry in relevant:
            self.thresholds.pop(entry["id"], None)

    def record_use(self, kind, cfg, now, stamp):
        rule = next(r for r in rules_from_config(cfg) if r["id"] == kind)
        self.record_item_use(rule["item_id"], cfg, now, stamp, rule=rule)


async def run_potions(bot):
    scheduler = bot.state.potion_scheduler
    while True:
        cfg = validate_config(bot.state.settings.get("actions", {}).get("auto_potions", {}), require_cooldowns=True)
        if bot.is_connected:
            gs = bot.state.game_state
            rule = scheduler.next_rule(cfg, gs, time.monotonic(), time.time())
            if rule:
                item_id, stamp = rule["item_id"], gs.stats_updated_at
                trigger = f"HP={gs.hp}/{gs.max_hp} Ki={gs.mana}/{gs.max_mana} threshold={scheduler.thresholds[rule['id']]:.1f}% snapshot_age_ms={max(0,(time.time()-stamp)*1000):.0f}"
                if rule["mode"] == "self":
                    packet = build_use_on_creature_packet(0xFFFF, 0, 0, item_id, 0, gs.player_id)
                else:
                    packet = build_use_item_packet(0xFFFF, 0, 0, item_id, 0, 0)
                bot.state.potion_diagnostics.record_use(item_id, rule["mode"], gs, source="auto")
                await bot.inject_to_server(packet)
                scheduler.record_item_use(item_id, cfg, time.monotonic(), stamp, rule=rule)
                if item_id == 8465 and getattr(bot.state, "playback_active", False):
                    bot.state.playback_senzu_used += 1
                bot.log(f"Rule {rule['id']}: requested {ITEMS[item_id]} ({item_id}); {trigger}; cooldown respected")
        await bot.sleep(0.05)


class PotionDiagnostics:
    """No packets sent or payloads stored. Deltas are observations, not proof of use."""
    def __init__(self):
        self._lock = threading.Lock()
        self.until = 0.0
        self.rows = deque(maxlen=500)
        self.last_use = {}
        self.last_reports = {}
        self.last_response = None
        self.sequence = 0
        self.inventory_epoch = 0

    def set_active(self, enabled):
        with self._lock:
            self.until = time.monotonic() + 300 if enabled else 0
            if enabled:
                self.rows.clear()
                self.last_use.clear()
                self.last_reports.clear()
                self.last_response = None

    def record_use(self, item_id, mode, gs, now=None, source="manual"):
        now = time.monotonic() if now is None else now
        with self._lock:
            if now >= self.until or item_id not in ITEMS:
                return
            previous = self.last_use.get(item_id)
            self.sequence += 1
            self.rows.append({"id":self.sequence,"item_id": item_id, "mode": mode, "source":source,
                              "time": time.strftime("%H:%M:%S")+f".{int(time.time()%1*1000):03}",
                              "gap_seconds": round(now - previous, 2) if previous is not None else None,
                              "status":"pending","response":None,"reported_count":None,"stock_delta":None,
                              "stats_age_ms":round(max(0,time.time()-gs.stats_updated_at)*1000) if getattr(gs,"stats_updated_at",0) else None,
                              "hp_delta": 0, "ki_delta": 0, "_at": now,
                              "_hp": gs.hp, "_ki": gs.mana})
            self.last_use[item_id] = now

    def invalidate_inventory(self):
        """Moving/trading items breaks evidence based on successive stock reports."""
        with self._lock:
            self.inventory_epoch += 1
            self.last_reports.clear()

    def observe_message(self, msg_type, text, batch_id=None, now=None):
        """Only typed server responses; chat and HP deltas never confirm use.

        DBV sends 'Using one of N ...' BEFORE acceptance, sometimes immediately
        followed by exhaustion. A later count of N-1 supports the prior attempt,
        provided there was one matched attempt and no inventory-changing command.
        Multiple outstanding attempts stay ambiguous instead of guessing latency.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            if now >= self.until or msg_type not in (25,26): return
            match=re.fullmatch(r"Using one of (\d{1,9}) (Special Health Potion|Special Ki Potion|Senzu)\.\.\.",text)
            if match and msg_type==25:
                item=next(i for i,n in ITEMS.items() if n==match[2])
                count=int(match[1])
                pending=[r for r in self.rows if r["item_id"]==item and r["status"]=="pending" and 0<=now-r["_at"]<=2]
                if len(pending)!=1:
                    for r in pending:r.update(status="ambiguous",response="Kilka prób przed odpowiedzią serwera.")
                    self.last_reports.pop(item,None);self.last_response=None
                    return
                row=pending[0]
                row.update(status="server_reported",response=text,reported_count=count)
                previous=self.last_reports.get(item)
                if previous:
                    old,old_count,epoch=previous
                    row["stock_delta"]=count-old_count
                    if (epoch==self.inventory_epoch and old_count-count==1 and old["status"]=="server_reported"
                            and sum(r["item_id"]==item and old["id"]<=r["id"]<row["id"] for r in self.rows)==1):
                        old.update(status="consumption_observed",response=f"Liczba w kolejnej odpowiedzi serwera: {old_count} → {count}.")
                self.last_reports[item]=(row,count,self.inventory_epoch)
                self.last_response=(row,batch_id,now)
                return
            rejection = re.fullmatch(r"(?:\[(Senzu)\] )?(You are exhausted(?: \([0-9.]+\))?\.?|You cannot use this object\.|You can not use this object\.|You don't have this item\.)",text)
            if not rejection or msg_type!=26:return
            row=None
            if self.last_response:
                candidate,batch,at=self.last_response
                if (batch_id is not None and batch==batch_id and 0<=now-at<.5
                        and candidate["status"]=="server_reported"
                        and (not rejection[1] or candidate["item_id"]==8465)):
                    row=candidate
            if row is None and rejection[1]:
                pending=[r for r in self.rows if r["item_id"]==8465 and r["status"]=="pending" and 0<=now-r["_at"]<=2]
                if len(pending)==1:row=pending[0]
            if row is not None:
                row.update(status="rejected",response=text)

    def observe_stats(self, gs, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            if now >= self.until:
                return
            for row in self.rows:
                if 0 <= now - row["_at"] <= 2:
                    row["hp_delta"] = gs.hp - row["_hp"]
                    row["ki_delta"] = gs.mana - row["_ki"]

    def snapshot(self):
        with self._lock:
            now=time.monotonic()
            for row in self.rows:
                if row["status"]=="pending" and now-row["_at"]>2:
                    row["status"]="unconfirmed"
            return {"active": time.monotonic() < self.until,
                    "capacity":self.rows.maxlen,"remaining_seconds":max(0,round(self.until-now)),
                    "rows": [{k: v for k, v in row.items() if not k.startswith("_")} for row in self.rows]}
