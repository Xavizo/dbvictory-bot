"""Session-local, read-only movement diagnostics. Inputs are normalized events."""
from collections import deque
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import threading
import time
from uuid import uuid4

from dbv.brain.cavebot.floor_step_witness import FloorStepWitness
from dbv.paths import LOCAL_DATA_DIR


HISTORY_SECONDS = 300
AFTER_SECONDS = 30
UNCONFIRMED_SECONDS = 2.5
MAX_EVENTS = 32768
REPORT_DIRECTORY = LOCAL_DATA_DIR / "movement-reports"
log = logging.getLogger(__name__)


def terrain(sample, target):
    if not target or not sample or sample.get("status") != "ready":
        return None
    x, y, z = target
    cells = sample.get("cells", ())
    origin = sample.get("origin")
    width = sample.get("width", 0)
    if origin and width:
        index = (y-origin[1])*width + x-origin[0]
        cells = cells[index:index+1] if 0 <= index < len(cells) else ()
    for cell in cells:
        if (cell["x"], cell["y"], cell["z"]) == (x,y,z):
            return cell.get("kind")
    return None


class MovementHistory:
    def __init__(self, directory=REPORT_DIRECTORY, *, clock=time.monotonic):
        self.directory = Path(directory)
        self.clock = clock
        self.start = clock()
        self.wall_start = time.time()
        self.lock = threading.Lock()
        self.ring = deque(maxlen=MAX_EVENTS)
        self.lost_at = None
        self.position = self.pending = self.incident = None
        self.floor_witness = FloorStepWitness()
        self.floor_steps = deque(maxlen=16)
        self.last_frame = None
        self.coverage = None
        self.last_gap = None
        self.last_web_blockage = None
        self.web_watch = None
        self.completed = deque()
        self.unwritten_reports = 0
        self.closed = False
        self.latest = self.error = None
        self.wake = threading.Event()
        self.worker = None

    def start_worker(self):
        self.worker = threading.Thread(target=self._run, name="movement-reports", daemon=True)
        self.worker.start()

    def _ms(self, now):
        return round((now-self.start)*1000)

    def _prune(self, now):
        cutoff = self._ms(now-HISTORY_SECONDS)
        while self.ring and self.ring[0]["ms"] < cutoff:
            self.ring.popleft()

    def _event(self, now, kind, **values):
        self._seal_if_due(now)
        self._prune(now)
        if len(self.ring) == MAX_EVENTS:
            self.lost_at = self.ring[0]["ms"]
        row = dict(ms=self._ms(now), event=kind, **values)
        step = self.floor_witness.feed(row)
        if step:
            self.floor_steps.append(step)
        self.ring.append(row)
        if self.incident:
            self.incident["tail"].append(row)
            self.incident["tail_count"] += 1

    def take_floor_steps(self, active):
        with self.lock:
            steps = list(self.floor_steps) if active else []
            self.floor_steps.clear()
            if not active:
                self.floor_witness.clear()
            return steps

    def interrupt_floor_step(self):
        with self.lock:
            if self.floor_witness.request:
                self._event(self.clock(), "interaction")

    def _snapshot(self, now, trigger):
        self._prune(now)
        return dict(format="dbv-movement-1", id=uuid4().hex, session_started_at=self.wall_start,
                    trigger=trigger, trigger_ms=self._ms(now), pre_seconds=HISTORY_SECONDS,
                    pre_truncated=self.lost_at is not None and self.lost_at >= self._ms(now-HISTORY_SECONDS),
                    events=list(self.ring))

    def _trigger(self, now, reason):
        if self.incident is None:
            self.incident = dict(report=self._snapshot(now, reason), tail=deque(maxlen=MAX_EVENTS), tail_count=0)
        self.incident["deadline"] = now + AFTER_SECONDS

    def _seal_if_due(self, now, *, closing=False):
        incident = self.incident
        if incident and (closing or now >= incident["deadline"]):
            end = min(now, incident["deadline"])
            incident["report"].update(end_ms=self._ms(end),
                                      end_reason="logout" if end < incident["deadline"] else "quiet",
                                      missing_tail_events=incident["tail_count"]-len(incident["tail"]))
            # A full or inaccessible disk must not grow an unbounded queue during play.
            if len(self.completed) >= 4:
                self.completed.popleft()
                self.unwritten_reports += 1
            self.completed.append(incident)
            self.incident = None

    def request(self, source, origin, target, sample=None):
        with self.lock:
            if self.closed:
                return
            now = self.clock()
            origin = tuple(origin) if origin else None
            target = tuple(target) if target else None
            kind = terrain(sample, target)
            self._event(now, "walk_request", source=source, origin=origin, target=target, terrain=kind)
            # Repeated keypresses at the same position must not postpone the timeout.
            since = now
            if (self.pending and self.pending["origin"] == origin and self.pending["source"] == source
                    and not (self.pending["position_seen"] or self.pending["map_seen"])):
                since = self.pending["since"]
            self.pending = dict(source=source, origin=origin, target=target, terrain=kind, since=since,
                                requested=now, wall_requested=time.time(), position_seen=False, map_seen=False) if origin else None

    def player_position(self, pos):
        with self.lock:
            p = self.pending
            if self.closed or not p or p["position_seen"] or tuple(pos) == p["origin"]:
                return
            now = self.clock()
            p["position_seen"] = True
            self._event(now, "position_after_request", position=tuple(pos), target=p["target"],
                        elapsed_ms=round((now-p["requested"])*1000))
            if p["map_seen"]:
                self.pending = None

    def stop_request(self):
        with self.lock:
            if not self.closed and self.pending:
                self._event(self.clock(), "walk_stopped")
                self.pending = None

    def frame(self, sample, observer=None):
        if sample.get("status") != "ready" or not sample.get("center"):
            return
        with self.lock:
            if self.closed:
                return
            now = self.clock()
            self.last_frame = now
            if self.web_watch:
                target, started, wall_started = self.web_watch
                if now-started > AFTER_SECONDS:
                    self.web_watch = None
                elif sample.get('captured_at', 0) > wall_started:
                    cell = next((c for c in sample.get('cells', ()) if (c['x'], c['y'], c['z']) == target), None)
                    if cell and cell.get('kind') not in ('unknown', None):
                        ids = [i['id'] for i in cell.get('items', ())]
                        if 189 in ids and 183 not in ids and 182 not in ids:
                            self._event(now, 'web_cut_observed', target=target, elapsed_ms=round((now-started)*1000))
                            self.web_watch = None
            pos = tuple(sample["center"])
            pending = self.pending
            # Map annotation can finish after a newer movement request was issued.
            if pending and sample.get("captured_at", pending["wall_requested"]) >= pending["wall_requested"]:
                kind = terrain(sample, pending["target"])
                if kind != pending["terrain"]:
                    self._event(now,"target_terrain",target=pending["target"],terrain=kind)
                    pending["terrain"] = kind
                if not pending["map_seen"] and pos != pending["origin"]:
                    pending["map_seen"] = True
                    self._event(now, "map_after_request", position=pos, target=pending["target"],
                                elapsed_ms=round((now-pending["requested"])*1000))
                    if pending["position_seen"]:
                        self.pending = None
            if self.position != pos:
                self._event(now, "map_position", position=pos)
                self.position = pos
                if self.last_web_blockage and self.last_web_blockage[0]!=pos:
                    self.last_web_blockage=None
            if observer is not None:
                counts = (observer.dropped, observer.partial, observer.failures)
                if counts != self.coverage:
                    self._event(now,"observer_totals",dropped=counts[0],partial=counts[1],failures=counts[2])
                    self.coverage = counts

    def body_move(self, entry, drop, before, after):
        with self.lock:
            if self.closed:
                return
            now = self.clock()
            phase = 'body_move_requested' if after is None else 'body_move_observed'
            self._event(now, phase, target=tuple(entry), destination=tuple(drop),
                        bodies_before=before, bodies_after=after)
            self._trigger(now, phase)

    def web_tool(self, source, target, tool, target_id, target_layer, cell):
        """Compare semantic tool selections on a known web, including clicks on the ground beneath it."""
        if tool not in (8899, 8900) or not any(i.get('id') in (182, 183) for i in (cell or {}).get('items', ())):
            return
        with self.lock:
            if self.closed:
                return
            now = self.clock()
            target = tuple(target)
            self._event(now, 'web_tool_use', source=source, target=target, tool_id=tool,
                        target_item=target_id, target_layer=target_layer)
            self.web_watch = (target, now, time.time())
            self._trigger(now, 'web_tool_use')

    def web_blocked(self,origin,target,cell,reason):
        items=tuple((i['id'],i.get('stack'),tuple(i.get('flags',()))) for i in (cell or {}).get('items',()))
        if not any(i in (182,183,189) for i,_,_ in items):
            return
        key=(tuple(origin),tuple(target),items,reason)
        with self.lock:
            if self.closed or key==self.last_web_blockage:
                return
            self.last_web_blockage=key
            now=self.clock()
            self._event(now,'web_obstruction',origin=tuple(origin),target=tuple(target),items=items,reason=reason)
            self._trigger(now,'web_obstruction')

    def cancelled(self, position, direction, received):
        with self.lock:
            if self.closed:
                return
            now = self.clock()
            self._event(now, "server_cancel", position=tuple(position), direction=direction,
                        received_ms=self._ms(received))
            if self.pending and self.pending["requested"] <= received:
                self.pending = None
            self._trigger(now, "server_cancel")

    def observation_gap(self, reason):
        with self.lock:
            if self.closed:
                return
            now = self.clock()
            # Keep the first occurrence per second; frame totals include repetitions.
            key = (reason, int(now))
            if self.last_gap != key:
                self._event(now, "observation_gap", reason=reason)
                self.last_gap = key

    def route_result(self, stage, outcome, target, elapsed_ms):
        with self.lock:
            if self.closed:
                return
            now = self.clock()
            self._event(now, stage, outcome=outcome, target=tuple(target), elapsed_ms=round(elapsed_ms,1))
            if stage == "route_position":
                if outcome in {"position_unconfirmed", "position_cancelled", "position_displaced", "position_observation_gap"}:
                    self._trigger(now, outcome)
                if outcome != "position_confirmed" and self.pending and self.pending["source"] == "cavebot":
                    self.pending = None

    def close(self, wait=False):
        with self.lock:
            self.closed = True
            self._seal_if_due(self.clock(), closing=True)
        self.wake.set()
        if wait and self.worker is not None:
            self.worker.join(timeout=2)

    def status(self):
        with self.lock:
            self._prune(self.clock())
            return dict(active=not self.closed, capturing=self.incident is not None, available=bool(self.ring),
                        latest=self.latest, error=self.error, unwritten_reports=self.unwritten_reports)

    def manual_save(self):
        # Called on the dashboard HTTP thread. File I/O always runs outside the lock.
        with self.lock:
            now = self.clock()
            report = self._snapshot(now, "manual")
            if not report["events"]:
                raise ValueError("Brak zdarzeń ruchu z ostatnich pięciu minut.")
            report.update(end_ms=self._ms(now), end_reason="manual_snapshot", missing_tail_events=0)
        return self._save(report)

    def poll(self, *, dequeue=True):
        with self.lock:
            now = self.clock()
            self._prune(now)
            p = self.pending
            if not self.closed and p and p["source"] == "client" and now-p["since"] >= UNCONFIRMED_SECONDS:
                if not p["position_seen"] and not p["map_seen"]:
                    age = round((now-self.last_frame)*1000) if self.last_frame is not None else None
                    self._event(now, "position_unconfirmed", origin=p["origin"], target=p["target"],
                                terrain=p["terrain"], map_age_ms=age, elapsed_ms=round((now-p["since"])*1000))
                    self._trigger(now, "position_unconfirmed")
                self.pending = None
            self._seal_if_due(now)
            if not dequeue or not self.completed:
                return None
            incident = self.completed.popleft()
            report = incident["report"]
        report["events"].extend(incident["tail"])
        return report

    def _save(self, report):
        stamp = datetime.fromtimestamp(self.wall_start+report["trigger_ms"]/1000, timezone.utc)
        path = self.directory / (stamp.strftime("%Y%m%dT%H%M%SZ-")+report["id"]+".json")
        tmp = path.with_suffix(".tmp")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            # Encoding one large events array can hold the GIL throughout a long incident.
            metadata = {k:v for k,v in report.items() if k != "events"}
            events = report["events"]
            with tmp.open("w", encoding="utf-8") as out:
                out.write(json.dumps(metadata, separators=(",",":"))[:-1] + ',"events":[')
                for start in range(0, len(events), 128):
                    if start:
                        out.write(",")
                    chunk = json.dumps(events[start:start+128], separators=(",",":"))
                    out.write(chunk[1:-1])
                out.write("]}")
            tmp.replace(path)
        except OSError:
            with self.lock:
                self.error = "Nie udało się zapisać raportu ruchu na dysku."
            raise
        with self.lock:
            self.latest, self.error = str(path), None
        return str(path)

    def _run(self):
        retry = None
        next_write = 0.
        while True:
            report = self.poll(dequeue=retry is None)
            if report is not None:
                retry = report
            if retry is not None and (self.closed or self.clock() >= next_write):
                try:
                    self._save(retry)
                    retry = None
                except OSError:
                    if not next_write or self.closed:
                        log.warning("Movement report write failed", exc_info=True)
                    if self.closed:
                        return
                    next_write = self.clock() + 5
                else:
                    next_write = 0.
            with self.lock:
                if self.closed and not self.completed and retry is None:
                    return
            self.wake.wait(.25)
