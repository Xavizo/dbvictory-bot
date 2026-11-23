"""Passive recording of first contact and subsequent evidence for that individual."""
from hunt_sightings import deaths_since
from cavebot_combat import eligible_monsters
from transition_learning import position


ALL_MONSTERS = {"engage": "visible", "rules": [{"pattern": "*", "action": "attack"}]}


class HuntEncounters:
    def __init__(self):
        self.events = []
        self.active = {}
        self.lost = {}
        self.retired = {}
        self.pending = {}
        self.game = None
        self.scan_at = 0
        self.last_xp = None

    def sample(self, gs, monitor, index, now, xp, discontinuity):
        kills = getattr(gs, "session_kills", 0)
        if gs is not self.game or discontinuity:
            self.game = gs
            self.active.clear()
            self.lost.clear()
            self.retired.clear()
            self.pending.clear()
            self.last_xp = None
            self.kill_count = kills
            self.scan_at = 0
        if xp:
            self.last_xp = now, gs.position[2]
        target = getattr(gs, "attack_target_id", 0)
        if target in self.active:
            self.active[target]["targeted"] = True
        self._confirm_deaths(gs, index, now)
        if now >= self.scan_at:
            self.scan_at = now + .25
            self._observe_creatures(gs, monitor, index, now, target)
        for event_index, (at, targeted, floor) in list(self.pending.items()):
            if floor == gs.position[2] and self.last_xp is not None and self.last_xp[1] == floor and abs(self.last_xp[0]-at) <= 2:
                event = self.events[event_index]
                self.events[event_index] = {**event, "outcome": "target_xp" if targeted else "nearby_xp"}
                del self.pending[event_index]
            elif now-at > 2 or floor != gs.position[2]:
                del self.pending[event_index]

    def _confirm_deaths(self, gs, index, now):
        self.kill_count, deaths = deaths_since(gs, self.kill_count)
        for death in deaths:
            cid = death.get("cid")
            track = self.active.get(cid) or self.lost.get(cid)
            pet = getattr(gs, "pets", None)
            if not track or (death.get("name") and death["name"] != track["name"]):
                continue
            if not track["first_at"] <= death["t"] <= now or now - death["t"] > 2:
                continue
            confirmed_pet = death.get("creature_kind") == "pet"
            if not confirmed_pet and pet and pet.is_pet(cid):
                continue
            self.active.pop(cid, None)
            self.lost.pop(cid, None)
            self.retired[cid] = death["t"]
            if confirmed_pet:
                self.events[track["event"]].update(outcome="pet", contact="ended")
                continue
            pos = [death.get(k) for k in ("x", "y", "z")]
            event = self.events[track["event"]]
            details = dict(death_sample=index, outcome="death", contact="ended")
            if position(pos) and pos[:2] != [0, 0]:
                details["death_pos"] = pos
            self.events[track["event"]] = {**event, **details}
            floor = details.get("death_pos", event.get("last_pos", event["first_pos"]))[2]
            self.pending[track["event"]] = (death["t"], track["targeted"], floor)

    def _observe_creatures(self, gs, monitor, index, now, target):
        snapshot = getattr(monitor, "snapshot", None)
        if snapshot:
            frame = snapshot()
            visible = set()
            self.retired = {cid: died for cid, died in self.retired.items()
                            if now - died <= 2 or cid in getattr(gs, "creatures", {})}
            if getattr(gs, "creatures", None):
                for mob in eligible_monsters(gs, frame, ALL_MONSTERS, now):
                    cid = mob["id"]
                    died = self.retired.get(cid)
                    if died is not None:
                        row = gs.creatures.get(cid, {})
                        if row.get("t", 0) <= died or row.get("health", 0) <= 0:
                            continue
                        self.retired.pop(cid)
                    visible.add(cid)
                    track = self.active.get(cid) or self.lost.pop(cid, None)
                    if not track or track["name"] != mob["name"]:
                        event = dict(name=mob["name"], sample=index, first_pos=list(mob["position"]),
                                     player_pos=list(gs.position), outcome="seen")
                        self.events.append(event)
                        track = dict(name=mob["name"], event=len(self.events)-1, first_at=now, targeted=False)
                    self.active[cid] = track
                    event = self.events[track["event"]]
                    if event.get("contact") == "lost":
                        event["reappearances"] = event.get("reappearances", 0) + 1
                    event.update(contact="visible", last_pos=list(mob["position"]), last_sample=index)
                    track.update(seen=now, targeted=track["targeted"] or cid == target)
            if frame.get("status") == "ready" and not frame.get("stale"):
                for cid, track in tuple(self.active.items()):
                    if cid not in visible and now-track["seen"] >= 3:
                        self.events[track["event"]]["contact"] = "lost"
                        self.lost[cid] = self.active.pop(cid)
