"""Passive recording of first contact and subsequent evidence for that individual."""
from itertools import islice

from cavebot_combat import eligible_monsters
from transition_learning import position


ALL_MONSTERS = {"engage": "visible", "rules": [{"pattern": "*", "action": "attack"}]}


class HuntEncounters:
    def __init__(self):
        self.events = []
        self.active = {}
        self.pending = {}
        self.game = None
        self.scan_at = 0
        self.last_xp = None

    def sample(self, gs, monitor, index, now, xp, discontinuity):
        kills = getattr(gs, "session_kills", 0)
        if gs is not self.game or discontinuity:
            self.game = gs
            self.active.clear()
            self.pending.clear()
            self.last_xp = None
            self.kill_count = kills
            self.scan_at = 0
        if xp:
            self.last_xp = now
        target = getattr(gs, "attack_target_id", 0)
        if target in self.active:
            self.active[target]["targeted"] = True
        if now >= self.scan_at:
            self.scan_at = now + .25
            snapshot = getattr(monitor, "snapshot", None)
            if snapshot and getattr(gs, "creatures", None):
                for mob in eligible_monsters(gs, snapshot(), ALL_MONSTERS, now):
                    cid = mob["id"]
                    track = self.active.get(cid)
                    if not track or track["name"] != mob["name"]:
                        event = dict(name=mob["name"], sample=index, first_pos=mob["position"],
                                     player_pos=list(gs.position), outcome="seen")
                        self.events.append(event)
                        track = self.active[cid] = dict(name=mob["name"], event=len(self.events)-1, targeted=False)
                    track.update(seen=now, targeted=track["targeted"] or cid == target)
            self.active = {cid: row for cid, row in self.active.items() if now-row["seen"] <= 3}
        count = max(0, kills-self.kill_count)
        self.kill_count = kills
        for death in islice(reversed(getattr(gs, "kill_log", ())), count):
            cid = death.get("cid")
            track = self.active.pop(cid, None)
            pet = getattr(gs, "pets", None)
            if not track or (pet and pet.is_pet(cid)) or (death.get("name") and death["name"] != track["name"]):
                continue
            if not 0 <= now-death["t"] <= 2:
                continue
            pos = [death.get(k) for k in ("x", "y", "z")]
            event = self.events[track["event"]]
            details = dict(death_sample=index, outcome="death")
            if position(pos) and pos[:2] != [0, 0] and pos[2] == gs.position[2]:
                details["death_pos"] = pos
            self.events[track["event"]] = {**event, **details}
            self.pending[track["event"]] = (death["t"], track["targeted"], gs.position[2])
        for event_index, (at, targeted, floor) in list(self.pending.items()):
            if floor == gs.position[2] and self.last_xp is not None and abs(self.last_xp-at) <= 2:
                event = self.events[event_index]
                self.events[event_index] = {**event, "outcome": "target_xp" if targeted else "nearby_xp"}
                del self.pending[event_index]
            elif now-at > 2 or floor != gs.position[2]:
                del self.pending[event_index]
