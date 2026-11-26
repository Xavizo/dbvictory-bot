"""Session-local revision clocks for observations already accepted by the engine."""
import asyncio
import math
from threading import Lock
import time

from transition_learning import position as valid_position


# None denotes a discrete event. Its age never implies that an effect is still active.
MAX_AGE = {
    "health": .5, "position": .5, "map": 2., "creatures": 2., "target": None,
    "walk_cancel": None, "messages": None, "loot": None, "inventory": None,
    "effects": None, "death": None,
}
SERVER_FAMILIES = ("health", "position", "walk_cancel", "messages", "loot", "effects", "death")


class SessionObservations:
    def __init__(self):
        self._lock = Lock()
        self._revision = 0
        self._entries = {}
        self._waiters = {}

    def _revision_for(self, families):
        return max((self._entries[k][0] for k in families if k in self._entries), default=0)

    def revision_for(self, families):
        with self._lock:
            return self._revision_for(families)

    def publish(self, source, families, at=None, *, gap=None):
        at = time.monotonic() if at is None else at
        with self._lock:
            changed = set()
            for family in families:
                if family not in MAX_AGE:
                    raise ValueError(f"Unknown observation family {family}")
                previous = self._entries.get(family)
                # An older completed callback cannot supersede newer evidence.
                if previous and (at < previous[2] or (gap and previous[1] != source)):
                    continue
                if not changed:
                    self._revision += 1
                self._entries[family] = (self._revision, source, at, gap)
                changed.add(family)
            ready = [future for future, wanted in self._waiters.items() if not wanted.isdisjoint(changed)]
            for future in ready:
                del self._waiters[future]
        if ready:
            try:
                current_loop = asyncio.get_running_loop()
            except RuntimeError:
                current_loop = None
            for future in ready:
                loop = future.get_loop()
                if loop is current_loop:
                    self._wake(future)
                else:
                    try:
                        loop.call_soon_threadsafe(self._wake, future)
                    except RuntimeError:
                        if not loop.is_closed():
                            raise

    @staticmethod
    def _wake(future):
        if not future.done():
            future.set_result(None)

    async def wait(self, families, after, timeout):
        wanted = frozenset(families)
        future = asyncio.get_running_loop().create_future()
        with self._lock:
            revision = self._revision_for(wanted)
            if revision > after or timeout <= 0:
                return revision
            self._waiters[future] = wanted
        try:
            async with asyncio.timeout(timeout):
                await future
        except TimeoutError:
            pass
        finally:
            with self._lock:
                self._waiters.pop(future, None)
        return self.revision_for(wanted)

    def snapshot(self, session, *, now=None, connected=True):
        now = time.monotonic() if now is None else now
        with self._lock:
            entries, revision = self._entries.copy(), self._revision
        families = {}
        for key, limit in MAX_AGE.items():
            seq, source, stamp, reason = entries.get(key, (0, None, None, None))
            age = None if stamp is None else max(0., now - stamp)
            if not connected or age is None:
                state = "waiting"
            elif reason:
                state = "gap"
            elif limit is None:
                state = "observed"
            else:
                if source == "server" and key in ("health", "position"):
                    limit = 2.
                state = "fresh" if age <= limit else "stale"
            families[key] = dict(state=state, source=source, revision=seq, reason=reason,
                                 age_ms=None if age is None else round(age * 1000))
        return dict(session_id=session, revision=revision, families=families)


def session_observations(gs):
    # Created on the owner loop on login, before producers start. Detached packet
    # snapshots do not own listeners or publish observations.
    journal = getattr(gs, "observations", None)
    if journal is None:
        gs.observations = journal = SessionObservations()
    return journal


def observation_family_status(state):
    gs = getattr(state, "game_state", None)
    journal = getattr(gs, "observations", None) or SessionObservations()
    return journal.snapshot(getattr(gs, "session_id", 0), connected=bool(getattr(state, "connected", False)))


def publish_player_sample(journal, sample):
    stamp = sample.get("monotonic")
    if not isinstance(stamp, (int, float)) or not math.isfinite(stamp) or stamp < 0:
        return
    pos = sample.get("position")
    health = (all(type(sample.get(k)) is int for k in ("hp", "max_hp", "mana", "max_mana"))
              and sample["max_hp"] > 0 and 0 <= sample["hp"] <= sample["max_hp"]
              and sample["max_mana"] > 0 and 0 <= sample["mana"] <= sample["max_mana"])
    position = valid_position(pos) and (pos[0] != 0 or pos[1] != 0)
    complete = []
    missing = []
    for family, valid in (("health", health), ("position", position)):
        (complete if valid else missing).append(family)
    if type(sample.get("conditions")) is int or type(sample.get("movement_speed")) is int:
        complete.append("effects")
    if complete:
        journal.publish("client_snapshot", complete, stamp)
    if missing:
        journal.publish("client_snapshot", missing, stamp, gap="incomplete")


def publish_semantic_result(gs, result, kills):
    """Consume only the existing observer's published facts. Does not inspect frames."""
    parsed = result.state
    old = result.scalars
    families = []
    if not gs.client_state_required:
        if parsed.__dict__.get("stats_updated_at") != old.get("stats_updated_at"):
            families.append("health")
        if gs.packet_position_trusted and (parsed.packet_position != old.get("packet_position") or
                                           parsed.packet_position_trusted != old.get("packet_position_trusted")):
            families.append("position")
        if parsed.player_icons != old.get("player_icons"):
            families.append("effects")
    if parsed.last_walk_cancel != old.get("last_walk_cancel"):
        families.append("walk_cancel")
    if result.messages or result.potion_messages or parsed.messages or parsed.message_history.combat:
        families.append("messages")
    if any(kind == "loot" for kind, _ in parsed.knife_feedback):
        families.append("loot")
    if kills:
        families.append("death")
        for death in kills:
            cid = death["cid"]
            if gs.pets.retire(cid, death["t"]):
                death["creature_kind"] = "pet"
    if families:
        session_observations(gs).publish("server", families, result.received_at)


def note_observation_gap(gs, reason):
    session_observations(gs).publish("server", SERVER_FAMILIES, gap=reason)
