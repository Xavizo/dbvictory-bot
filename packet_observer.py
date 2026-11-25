"""Bounded passive server observation, isolated from socket forwarding.

One worker decrypts/parses detached state; only the owning event loop publishes.
Keys and payloads stay in memory and are never exposed in diagnostics.
"""
import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
import logging
import struct
import time

from crypto import adler32_checksum, xtea_decrypt
from game_state import GameState, scan_packet

log = logging.getLogger(__name__)

SCALARS = (
    "player_id", "client_player_id", "hp", "max_hp", "mana", "max_mana",
    "level", "experience", "capacity", "magic_level", "soul", "speed",
    "stats_updated_at", "position", "packet_position", "packet_position_trusted",
    "last_map_time", "player_icons", "world_light_level", "world_light_color",
    "attack_target_id", "last_cant_throw", "in_protection_zone", "cancel_walk_time", "last_walk_cancel",
    "_last_walk_delta", "_last_prune_time", "_recent_kills_cleanup",
    "_prev_experience", "_server_packet_seq", "haste_cast_at", "barrier_cast_at", "personal_loot_channel", "player_party_shield",
)


def decrypt_game_packet(data, keys):
    """Same framing as the relay, with an immutable per-job key tuple."""
    if keys is None or not data or len(data) > 65535:
        return None
    try:
        offset = 0
        if len(data) > 4 and struct.unpack_from('<I', data)[0] == adler32_checksum(data[4:]):
            offset = 4
        encrypted = data[offset:]
        if not encrypted or len(encrypted) % 8:
            return None
        plain = xtea_decrypt(encrypted, keys)
        length = struct.unpack_from('<H', plain)[0]
        return plain[2:2+length] if length <= len(plain)-2 else None
    except (ValueError, TypeError, IndexError, struct.error):
        return None


@dataclass(repr=False)
class DetachedScan:
    state: GameState
    scalars: dict
    creatures: dict
    kills: list
    recent_kills: set
    unreachable: dict
    messages: list = field(default_factory=list)
    potion_messages: list = field(default_factory=list)
    received_at: float | None = None
    received_perf: float | None = None
    sequential_complete: bool = True


def detach_state(live, target_id):
    """Called on the owner loop. No live collections or callbacks reach worker."""
    gs = GameState()
    for key in SCALARS:
        if key in live.__dict__:
            gs.__dict__[key] = deepcopy(live.__dict__[key])
    gs.attack_target_id = target_id
    gs.client_state_required = live.client_state_required
    gs.client_snapshot = deepcopy(live.client_snapshot)
    gs._packet_snapshot_time = time.monotonic()
    gs._packet_snapshot_detached = True
    gs.creatures = deepcopy(live.creatures)
    gs.kill_log = deque(deepcopy(list(live.kill_log)[-50:]), maxlen=5000)
    gs._recent_kills = set(live._recent_kills)
    gs.unreachable_creatures = dict(live.unreachable_creatures)
    scan = DetachedScan(gs, {k: deepcopy(gs.__dict__.get(k)) for k in SCALARS},
                        deepcopy(gs.creatures), deepcopy(list(gs.kill_log)),
                        set(gs._recent_kills), dict(gs.unreachable_creatures))
    gs.alert_chat_callback = lambda message, _: scan.messages.append(deepcopy(message))
    gs.potion_message_callback = lambda kind, text, **kw: scan.potion_messages.append((kind,text,kw))
    return scan


def parse_detached(raw, keys, scan):
    plain = decrypt_game_packet(raw, keys)
    if plain is None:
        return None
    scan.sequential_complete = scan_packet(plain, scan.state, received_at=scan.received_perf)
    if len(scan.messages)+len(scan.potion_messages)+len(scan.state.kill_log)-len(scan.kills)>256:
        raise ValueError("observation_result_limit")
    return scan


def publish_server_feedback(live, scan, diagnostics):
    # Use the time of receipt for correlations with local actions. Parsing can
    # finish after a different Look, inventory change, or Cavebot restart.
    usage = getattr(live, "senzu_usage", None)
    for kind, text, kwargs in scan.potion_messages:
        if kind == 25:
            live.pets.description(text, live.creatures, received_at=scan.received_at)
        diagnostics.observe_message(kind, text, now=scan.received_at, **kwargs)
        if usage is not None:
            usage.observe_message(kind, text, kwargs.get("batch_id"), scan.received_at)
    diagnostics.observe_stats(live)


def log_walk_cancels(live, scan):
    for _, kind, event in scan.state.server_events:
        if kind != 'cancel_walk':
            continue
        history = getattr(live, "movement_history", None)
        if history:
            history.cancelled(event["pos"], event["direction"], scan.received_at)
        # A later local input may already exist by the time the worker publishes.
        recent = next((r for r in reversed(live.client_walk_requests)
                       if 0 <= scan.received_perf-r[0] <= 1.5), None)
        log.info("CANCEL_WALK direction=%s observed_pos=%s last_client_move=%s "
                 "requested_from=%s requested_to=%s request_age_ms=%s",
                 event['direction'], event['pos'], recent[1] if recent else None,
                 recent[2] if recent else None, recent[3] if recent else None,
                 round((scan.received_perf-recent[0])*1000) if recent else None)


def apply_scan(live, scan):
    """Owner-loop merge; packet evidence cannot overwrite newer client reads."""
    parsed = scan.state
    for key, old in scan.scalars.items():
        new = parsed.__dict__.get(key)
        if new == old or live.__dict__.get(key) != old:
            continue
        if key == "player_id" and live.client_player_id:
            continue
        live.__dict__[key] = new
    for cid, old in scan.creatures.items():
        current = live.creatures.get(cid)
        new = parsed.creatures.get(cid)
        # DLL owns its fresher creature state. Never resurrect or delete a creature
        # that disappeared/changed while the worker was busy.
        if current != old or current.get("source") == "dll":
            continue
        if new is None:
            live.creatures.pop(cid, None)
        elif new != old:
            live.creatures[cid] = new
    for key, value in parsed.unreachable_creatures.items():
        if value != scan.unreachable.get(key):
            live.unreachable_creatures[key] = max(value, live.unreachable_creatures.get(key, 0))
    if live._recent_kills == scan.recent_kills:
        live._recent_kills = parsed._recent_kills
    else:
        live._recent_kills.update(parsed._recent_kills - scan.recent_kills)
    old_keys = {(k.get("cid"), k.get("t")) for k in scan.kills}
    new_kills = []
    for kill in parsed.kill_log:
        identity = kill.get("cid"), kill.get("t")
        if identity not in old_keys:
            live.kill_log.append(kill)
            live.session_kills += 1
            new_kills.append(kill)
        elif "xp" in kill:
            for existing in reversed(live.kill_log):
                if (existing.get("cid"), existing.get("t")) == identity:
                    existing.setdefault("xp", kill["xp"])
                    break
    from hunter_knife import publish_feedback
    publish_feedback(live, parsed, new_kills, scan.received_at)
    live.messages.extend(parsed.messages)
    live.tile_updates.extend(parsed.tile_updates)
    for _, kind, event in parsed.server_events:
        if kind == "container_window" and scan.received_perf is not None:
            live.browse_fields.window_changed(event["number"], event["is_field"], scan.received_perf)
    live.server_events.extend(parsed.server_events)
    return new_kills


@dataclass(repr=False)
class Observation:
    raw: bytes
    keys: tuple
    session: int
    generation: int
    state: GameState
    created: float
    received_perf: float
    target_id: int


class PacketObserver:
    def __init__(self, get_state, get_session, is_connected, on_result, on_gap,
                 *, max_packets=64, max_bytes=2*1024*1024, max_age=.5,
                 worker=parse_detached):
        if max_packets < 1 or max_bytes < 1 or max_age <= 0:
            raise ValueError("Invalid observer bounds")
        self.get_state, self.get_session, self.is_connected = get_state, get_session, is_connected
        self.on_result, self.on_gap, self.worker = on_result, on_gap, worker
        self.max_bytes, self.max_age = max_bytes, max_age
        self.queue = asyncio.Queue(maxsize=max_packets)
        self.queued_bytes = 0
        self.generation = 0
        self.closed = False
        self.processing = False
        self.offered = self.accepted = self.applied = self.dropped = self.failures = 0
        self.partial = 0
        self.next_coverage_log = time.monotonic()+60
        self.next_gap_log = 0.
        self.last_lag_ms = self.max_lag_ms = 0
        self.last_problem = None
        self.last_problem_at = None
        self.last_applied = None
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="packet-observer")
        self.task = asyncio.create_task(self._run(), name="packet-observer")

    def _discard_pending(self):
        count = 0
        while not self.queue.empty():
            self.queue.get_nowait(); self.queue.task_done(); count += 1
        self.queued_bytes = 0
        self.dropped += count

    def invalidate(self):
        self.generation += 1
        self._discard_pending()

    def _gap(self, reason):
        self.invalidate()
        self.last_problem, self.last_problem_at = reason, time.monotonic()
        if self.last_problem_at >= self.next_gap_log:
            self.next_gap_log = self.last_problem_at + 10
            # On overload this method runs in the relay tap. Defer file logging.
            asyncio.get_running_loop().call_soon(log.warning, "Server observation gap: %s", reason)
        # Synchronous, local-only callback; never sends a command or closes sockets.
        try:
            self.on_gap(reason)
        except Exception:
            self.failures += 1

    def offer(self, raw, keys, session):
        """Non-awaiting relay tap. Overflow drops analysis copies, never traffic."""
        if self.closed or not self.is_connected() or session != self.get_session():
            return False
        self.offered += 1
        if self.task.done():
            self.dropped += 1
            self._gap("worker_stopped")
            return False
        if not keys or not raw or len(raw) > 65535 or len(raw) > self.max_bytes:
            self.dropped += 1
            self._gap("invalid_frame")
            return False
        if self.queue.full() or self.queued_bytes + len(raw) > self.max_bytes:
            self._gap("overload")
        gs = self.get_state()
        item = Observation(bytes(raw), tuple(keys), session, self.generation, gs,
                           time.monotonic(), time.perf_counter(), gs.attack_target_id)
        self.queue.put_nowait(item)
        self.queued_bytes += len(raw)
        self.accepted += 1
        return True

    def _current(self, item):
        return (not self.closed and self.is_connected() and item.session == self.get_session()
                and item.generation == self.generation and item.state is self.get_state())

    async def _run(self):
        loop = asyncio.get_running_loop()
        while True:
            item = await self.queue.get()
            self.queued_bytes -= len(item.raw)
            try:
                if not self._current(item):
                    self.dropped += 1
                    continue
                if time.monotonic()-item.created > self.max_age:
                    self.dropped += 1; self._gap("stale"); continue
                # Exactly one executor future at a time: no hidden executor backlog.
                if len(item.state.creatures)>4096:
                    raise ValueError("observation_context_limit")
                scan = detach_state(item.state, item.target_id)
                scan.received_at = item.created
                scan.received_perf = item.received_perf
                self.processing = True
                result = await loop.run_in_executor(self.pool, self.worker, item.raw, item.keys, scan)
                if not self._current(item):
                    self.dropped += 1
                    continue
                lag = time.monotonic()-item.created
                self.last_lag_ms = round(lag*1000, 1)
                self.max_lag_ms = max(self.max_lag_ms, self.last_lag_ms)
                if lag > self.max_age or result is None:
                    self.dropped += 1; self._gap("stale" if lag > self.max_age else "decode_failed"); continue
                kills = apply_scan(item.state, result)
                if not result.sequential_complete:
                    self.partial += 1
                try:
                    self.on_result(item.state, result, kills)
                    log_walk_cancels(item.state, result)
                except Exception:
                    # Optional diagnostics must not invalidate correctly parsed
                    # state, flush pending frames, or stop other automation.
                    self.failures += 1
                    self.last_problem = "notification_failed"
                    self.last_problem_at = time.monotonic()
                self.applied += 1
                self.last_applied = time.monotonic()
            except asyncio.CancelledError:
                self.dropped += 1
                raise
            except Exception:
                self.dropped += 1
                self.failures += 1
                self._gap("analysis_failed")
            finally:
                self.processing = False
                self.queue.task_done()
                now = time.monotonic()
                if now >= self.next_coverage_log:
                    self._log_coverage()
                    self.next_coverage_log = now+60

    def _log_coverage(self):
        log.info("Server observation totals: offered=%d accepted=%d applied=%d partial_among_applied=%d "
                 "dropped=%d failures=%d pending=%d last_problem=%s",
                 self.offered, self.accepted, self.applied, self.partial, self.dropped, self.failures,
                 self.queue.qsize(), self.last_problem)

    async def close(self):
        self.closed = True
        self.invalidate()
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.pool.shutdown(wait=False, cancel_futures=True)
        self._log_coverage()

    def snapshot(self):
        degraded = self.last_problem_at is not None and time.monotonic()-self.last_problem_at < 5
        return {"status": "closed" if self.closed else "error" if self.task.done() else "degraded" if degraded else "active",
                "pending": self.queue.qsize(), "pending_bytes": self.queued_bytes, "processing": self.processing,
                "capacity": self.queue.maxsize, "offered": self.offered, "accepted": self.accepted, "applied": self.applied,
                "dropped": self.dropped, "failures": self.failures, "partial_among_applied": self.partial,
                "lag_ms": self.last_lag_ms, "max_lag_ms": self.max_lag_ms,
                "last_problem": self.last_problem,
                "last_applied_age_seconds": round(time.monotonic()-self.last_applied, 2) if self.last_applied else None}
