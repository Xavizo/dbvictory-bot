"""Passive navigation observations. No packet writes, client calls or playback.

Successful observations describe what happened, not the only possible method or
a guarantee that a future attempt will work. Ambiguous samples stay unassigned.
"""
from collections import deque
from contextlib import closing
import hashlib
import json
import sqlite3
from pathlib import Path
import struct
import threading
import time

from protocol import ClientOpcode as Op

OFFSETS = {1:(1,0), 2:(1,-1), 3:(0,-1), 4:(-1,-1), 5:(-1,0), 6:(-1,1), 7:(0,1), 8:(1,1)}
WALKS = {Op.WALK_NORTH:(0,-1), Op.WALK_EAST:(1,0), Op.WALK_SOUTH:(0,1), Op.WALK_WEST:(-1,0),
         Op.WALK_NORTHEAST:(1,-1), Op.WALK_SOUTHEAST:(1,1), Op.WALK_SOUTHWEST:(-1,1), Op.WALK_NORTHWEST:(-1,-1)}
LEGACY_MAX_RECORDS = 2000
LEGACY_MAX_FILE = 2 * 1024 * 1024
ACTIONS = {"step", "use", "use_with", "use_then_step", "use_with_then_step", "fly_up", "unknown"}


def position(value):
    return isinstance(value, (list, tuple)) and len(value) == 3 and all(type(v) is int for v in value) and 0 <= value[0] < 65535 and 0 <= value[1] < 65535 and 0 <= value[2] < 16


def distance(a, b):
    return max(abs(a[0]-b[0]), abs(a[1]-b[1])) if a[2] == b[2] else 65535


def gate_entry_record(row):
    """A witnessed use that moved onto the door is already a directed edge.

    Opening in place, closing after exit and appearance hints are not edges.
    Reuse old observations without rewriting or promoting their evidence.
    """
    change=row.get('object_change') or {}
    origin,entry,dest=row.get('origin'),row.get('entry'),row.get('exit')
    if (row.get('evidence')!='observed' or change.get('behavior')!='use_enters' or
            row.get('action')!='use' or not all(position(p) for p in (origin,entry,dest)) or
            list(dest)!=list(entry) or distance(origin,entry)!=1 or row.get('item_id')!=change.get('closed_id')):
        return None
    return {**row,'open_item_id':change['open_id']}


def decode_command(opcode, reader, origin):
    """Decode only navigation metadata; never retain packet bytes or chat."""
    if opcode == Op.SAY:
        # Exact public spell only. No arbitrary chat text enters the queue/log/file.
        try:
            if reader.remaining == 9 and reader.read_u8() == 1 and reader.read_u16() == 6:
                if reader.read_bytes(6).lower() == b"fly up" and position(origin):
                    return {"kind":"fly_up", "entry":list(origin)}
        except (IndexError, ValueError, struct.error):
            pass
        return None
    relevant = opcode in WALKS or opcode in {Op.NEW_WALK, Op.AUTO_WALK, Op.USE_ITEM, Op.USE_ITEM_EX, Op.STOP_WALK, Op.LOGOUT}
    if not relevant:
        return None
    try:
        if opcode in {Op.STOP_WALK, Op.LOGOUT}:
            return {"kind":"cancel"}
        if opcode in WALKS:
            if not position(origin):
                return {"kind":"ambiguous"}
            dx,dy = WALKS[opcode]
            target = [origin[0]+dx, origin[1]+dy, origin[2]]
            return {"kind":"step", "origin":list(origin), "entry":target} if position(target) else {"kind":"ambiguous"}
        if opcode in {Op.NEW_WALK, Op.AUTO_WALK}:
            if opcode == Op.NEW_WALK:
                reader.read_u32(); reader.read_u32()
                origin = reader.read_position()
                reader.read_u8()
                count = reader.read_u16()
            else:
                count = reader.read_u8()
            # A longer requested route does not establish which step triggered a transition.
            if not position(origin) or count != 1 or reader.remaining != 1:
                return {"kind":"ambiguous"}
            offset = OFFSETS.get(reader.read_u8())
            if offset is None:
                return {"kind":"ambiguous"}
            target = [origin[0]+offset[0],origin[1]+offset[1],origin[2]]
            return {"kind":"step", "origin":list(origin), "entry":target} if position(target) else {"kind":"ambiguous"}
        source = reader.read_position()
        item = reader.read_u16()
        stack = reader.read_u8()
        if not 100 <= item <= 65535:
            return {"kind":"ambiguous"}
        if opcode == Op.USE_ITEM:
            if not position(source):  # Inventory potions etc. are not navigation.
                return None
            if reader.remaining != 1 or not 100 <= item <= 65535:
                return {"kind":"ambiguous"}
            reader.read_u8()
            return {"kind":"use", "entry":list(source), "item_id":item, "stack":stack}
        target = reader.read_position()
        if not position(target):
            return None
        # Standard use-with carries target ID + stack; this repo also supports a legacy stack-only layout.
        if reader.remaining == 3:
            target_id = reader.read_u16()
        elif reader.remaining == 1:
            target_id = None
        else:
            return {"kind":"ambiguous"}
        target_stack = reader.read_u8()
        return {"kind":"use_with", "entry":list(target), "tool_id":item,
                "item_id":target_id, "stack":target_stack}
    except (IndexError, ValueError, struct.error):
        return {"kind":"ambiguous"}


def cell_at(snapshot, pos):
    return next((c for c in snapshot.get("cells", []) if [c["x"],c["y"],c["z"]] == list(pos) and c["kind"] != "unknown"), None)


def door(cell):
    return cell is not None and any(f["kind"] in {"door","level_gate"} for f in cell.get("navigation", []))


class TransitionLearner:
    def __init__(self, directory, scope):
        self.scope = scope
        self.path = Path(directory) / f"map_transitions-{scope[:16]}.sqlite3"
        self._legacy = self.path.with_suffix(".json")
        self._revision = 0
        self.records = {}
        self.storage_error = None
        self._stamp = None
        self._queue = deque(maxlen=64)
        self._lock = threading.Lock()
        self._epoch = 0
        self._seen_epoch = 0
        self._last = None
        self._commands = []
        self._door_use = None
        self._door_visit = None
        self._pending = None
        self._preparation = None
        self._object_candidate = self._exit_check = None
        self._view = {}
        self._load()
        if not self.storage_error:
            self._load_recovered_steps()
        self._publish("waiting")

    @staticmethod
    def _file_stamp(path):
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def _load(self):
        try:
            if self.path.exists():
                with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                    db.execute("BEGIN")
                    meta = db.execute("SELECT scope,revision FROM store").fetchone()
                    if not meta or meta[0] != self.scope or meta[1] < 1:
                        raise ValueError()
                    for identity, raw in db.execute("SELECT id,data FROM observations"):
                        row = json.loads(raw)
                        self._validate(row)
                        if row["id"] != identity:
                            raise ValueError()
                        self.records[identity] = row
                    self._revision = meta[1]
                return
            self._stamp = self._file_stamp(self._legacy)
            if self._stamp is None:
                return
            for row in self._read_observations(self._legacy):
                self.records[row["id"]] = row
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, AttributeError):
            self.records.clear()
            self.storage_error = "Plik obserwacji jest nieprawidłowy lub niedostępny; nie zostanie nadpisany."

    def _read_observations(self, path):
        with path.open("rb") as stream:
            raw = stream.read(LEGACY_MAX_FILE + 1)
        if len(raw) > LEGACY_MAX_FILE:
            raise ValueError()
        doc = json.loads(raw)
        rows = doc["records"]
        if doc["version"] != 1 or doc["scope"] != self.scope or not isinstance(rows, list) or len(rows) > LEGACY_MAX_RECORDS:
            raise ValueError()
        for row in rows:
            self._validate(row)
        return rows

    def _load_recovered_steps(self):
        # Optional local evidence reconstructed from historical movement reports.
        # It never replaces an observed exit or increases live observation counts.
        try:
            rows = self._read_observations(self.path.with_suffix(".recovered.json"))
        except FileNotFoundError:
            return
        except (ValueError, KeyError, TypeError, AttributeError, OSError):
            import logging
            logging.getLogger(__name__).warning("Could not load recovered atlas steps for %s", self.scope[:16])
            return
        known = {tuple(r["entry"]) for r in self.records.values()
                 if r["entry"] and r["action"] == "step" and r["evidence"] == "observed"}
        destinations = {}
        for row in rows:
            entry, exit_pos = row["entry"], row["exit"]
            if (row["action"] == "step" and row["evidence"] == "observed" and row["reason"] == "matched_command"
                    and row["count"] >= 2 and row["entry_items"] and entry and distance(row["origin"], entry) == 1
                    and abs(entry[2] - exit_pos[2]) == 1 and max(abs(entry[k] - exit_pos[k]) for k in (0, 1)) <= 1):
                key = tuple(entry)
                destinations.setdefault(key, {})[tuple(exit_pos)] = row
        for entry, exits in destinations.items():
            if entry not in known and len(exits) == 1:
                row = next(iter(exits.values()))
                self.records[row["id"]] = row

    def _validate(self, row):
        required = {"id","origin","entry","exit","action","item_id","tool_id","entry_items","direction","evidence","reason","count","first_seen","last_seen"}
        if (not required <= set(row) or set(row)-required-{"object_change","prepared_item_id"}) or not position(row["origin"]) or not position(row["exit"]) or (row["entry"] is not None and not position(row["entry"])):
            raise ValueError()
        if "prepared_item_id" in row and (type(row["prepared_item_id"]) is not int or not 100<=row["prepared_item_id"]<=65535):
            raise ValueError()
        if row["action"] not in ACTIONS or row["evidence"] not in {"observed","review"} or row["direction"] not in {"up","down","same_floor"}:
            raise ValueError()
        if row["reason"] not in {"matched_command","multiple_commands","no_command","incomplete_entry","long_jump","door_crossing","multiple_steps","sample_gap"}:
            raise ValueError()
        if type(row["count"]) is not int or not 1<=row["count"]<=1000000:
            raise ValueError()
        if not all(type(row[k]) in (int,float) and 0<row[k]<1e12 for k in ("first_seen","last_seen")):
            raise ValueError()
        if not isinstance(row["entry_items"],list) or len(row["entry_items"])>16 or any(type(v) is not int or not 100<=v<=65535 for v in row["entry_items"]):
            raise ValueError()
        if any(row[k] is not None and (type(row[k]) is not int or not 100<=row[k]<=65535) for k in ("item_id","tool_id")):
            raise ValueError()
        if "object_change" in row:
            change=row["object_change"]
            if (not isinstance(change,dict) or set(change)!={"kind","closed_id","open_id","behavior"}
                    or change["kind"] not in {"door","level_gate","passage"}
                    or change["behavior"] not in {"open_then_step","use_enters","manual_close","closes_after_exit","stays_open"}
                    or any(type(change[k]) is not int or not 100<=change[k]<=65535 for k in ("closed_id","open_id"))):
                raise ValueError()
        if row["id"] != self._key(row):
            raise ValueError()

    @staticmethod
    def _key(row):
        data = {k:row.get(k) for k in ("entry","exit","action","item_id","tool_id","entry_items","evidence")}
        if "object_change" in row:
            data["object_change"] = row["object_change"]
        if "prepared_item_id" in row:
            data["prepared_item_id"] = row["prepared_item_id"]
        if row.get("entry") is None:
            data["origin"] = row["origin"]
        return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()[:20]

    def _save(self, row):
        if self.storage_error:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            initial = self._revision == 0
            if initial and self._file_stamp(self._legacy) != self._stamp:
                self.storage_error = "Plik obserwacji został zmieniony poza sesją; zapis wstrzymany."
                return
            # Import the complete legacy snapshot and the first change atomically.
            # Later observations only write the affected row.
            uri = self.path.resolve().as_uri() + ("?mode=rwc" if initial else "?mode=rw")
            with closing(sqlite3.connect(uri, uri=True, timeout=.25)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                if initial:
                    db.execute("CREATE TABLE store (scope TEXT PRIMARY KEY, revision INTEGER NOT NULL)")
                    db.execute("INSERT INTO store VALUES (?,1)", (self.scope,))
                    db.execute("CREATE TABLE observations (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
                    rows = self.records.values()
                else:
                    updated = db.execute("UPDATE store SET revision=revision+1 WHERE scope=? AND revision=?",
                                         (self.scope, self._revision))
                    if updated.rowcount != 1:
                        self.storage_error = "Baza obserwacji została zmieniona poza sesją; zapis wstrzymany."
                        return
                    rows = (row,)
                db.executemany("INSERT OR REPLACE INTO observations VALUES (?,?)",
                               ((r["id"], json.dumps(r, ensure_ascii=False, separators=(",", ":"))) for r in rows))
            self._revision += 1
        except (OSError, sqlite3.Error):
            self.storage_error = "Nie udało się zapisać obserwacji; bieżące dane pozostają w pamięci."

    def enqueue(self, command, now=None):
        if command is None:
            return
        now = time.monotonic() if now is None else now
        with self._lock:
            if len(self._queue) == self._queue.maxlen:
                self._epoch += 1  # Do not attribute a transition after dropped commands.
                self._queue.clear()
            self._queue.append({**command,"time":now})

    def reset(self):
        with self._lock:
            self._epoch += 1
            self._queue.clear()

    def _reset_tracking(self):
        self._last = self._pending = self._door_use = self._door_visit = None
        self._preparation = None
        self._object_candidate = self._exit_check = None
        self._commands = []

    def snapshot(self):
        return self._view

    def _publish(self, status):
        rows = sorted(self.records.values(),key=lambda r:r["last_seen"],reverse=True)
        destinations = {}
        for row in rows:
            if row["entry"] is not None and row["evidence"] == "observed" and not row.get("object_change"):
                key = (tuple(row["entry"]),row["action"],row["item_id"],row["tool_id"])
                destinations.setdefault(key,set()).add(tuple(row["exit"]))
        def display(row):
            key = (tuple(row["entry"] or []),row["action"],row["item_id"],row["tool_id"])
            return {**row,"variable_exit":len(destinations.get(key,()))>1}
        center = self._last[1]["center"] if self._last else None
        def proximity(row):
            entry = row["entry"]
            if entry is None or center is None:
                return (2,0,0,-row["last_seen"])
            same_floor = entry[2]==center[2]
            return (0 if same_floor else 1, 0 if same_floor else abs(entry[2]-center[2]),
                    max(abs(entry[0]-center[0]),abs(entry[1]-center[1])), -row["last_seen"])
        nearby = sorted(rows,key=proximity)[:20]
        self._view = {"status":status,"total":len(rows),"observed":sum(r["evidence"]=="observed" for r in rows),
                      "records":[display(r) for r in rows[:20]],"nearby_records":[display(r) for r in nearby],
                      "storage_error":self.storage_error,"file":self.path.name,
                      "passive":True}

    def _record(self, record, wall):
        record = {"item_id":None,"tool_id":None,"entry_items":[],**record}
        record["id"] = self._key(record)
        old = self.records.get(record["id"])
        record.update(count=min(1000000,old["count"]+1) if old else 1,
                      first_seen=old["first_seen"] if old else wall,last_seen=wall)
        self.records[record["id"]] = record
        self._save(record)

    def _attribute(self, previous, current, commands):
        origin, destination = list(previous["center"]),list(current["center"])
        direction = "up" if destination[2]<origin[2] else "down" if destination[2]>origin[2] else "same_floor"
        row = {"origin":origin,"entry":None,"exit":destination,"action":"unknown","evidence":"review","reason":"no_command","direction":direction}
        preparation = self._preparation
        if (preparation and preparation["changed"] and len(commands)==2 and
                commands[0]["time"]==preparation["time"] and commands[1]["kind"]=="step" and
                commands[1]["entry"]==preparation["entry"]):
            commands = commands[1:]
        if len(commands)!=1:
            if commands: row["reason"] = "multiple_commands"
            return row
        command = commands[0]
        if command["kind"] not in {"step","use","use_with","fly_up"}:
            row["reason"] = "multiple_steps"
            return row
        entry = command["entry"]
        tile = cell_at(previous,entry)
        if tile is None or distance(origin,entry)>1:
            row["reason"] = "incomplete_entry"
            return row
        if command["kind"] == "step" and command["origin"] != origin:
            row["reason"] = "incomplete_entry"
            return row
        if command["kind"] == "fly_up":
            spots = [f for f in tile.get("navigation",[]) if f["kind"] == "rope_spot"]
            if entry != origin or len(spots) != 1 or destination[2] >= origin[2]:
                row["reason"] = "incomplete_entry"
                return row
            row["item_id"] = spots[0]["item_id"]
        if command["kind"] in {"use","use_with"}:
            matching = [i for i in tile["items"] if i["stack"] == command["stack"]]
            if not matching or (command.get("item_id") is not None and matching[0]["id"] != command["item_id"]):
                row["reason"] = "incomplete_entry"
                return row
            row["item_id"] = matching[0]["id"]
            row["tool_id"] = command.get("tool_id")
        row.update(entry=list(entry),action=command["kind"],evidence="observed",reason="matched_command",
                   entry_items=[i["id"] for i in tile["items"]][:16])
        if command["kind"] == "step" and preparation and preparation["entry"] == entry and preparation["changed"]:
            row.update(action=preparation["kind"]+"_then_step", item_id=preparation["item_id"],
                       tool_id=preparation.get("tool_id"), entry_items=preparation["ids"])
            if preparation.get("prepared_item_id"):
                row["prepared_item_id"] = preparation["prepared_item_id"]
        return row

    def observe(self, sample, now=None, wall=None, active=True):
        """Called only by the map worker. Disk I/O never blocks proxy callbacks."""
        now = time.monotonic() if now is None else now
        wall = time.time() if wall is None else wall
        with self._lock:
            epoch, incoming = self._epoch,list(self._queue)
            self._queue.clear()
        if epoch != self._seen_epoch:
            self._seen_epoch = epoch
            self._reset_tracking()
            incoming = []
        if not active or sample is None or sample.get("status") != "ready":
            self._reset_tracking()
            self._publish("waiting" if active else "suspended")
            return
        if self._last is None or now-self._last[0]>1.0:
            self._reset_tracking()
            self._last = (now,sample)
            self._publish("learning")
            return
        previous_time, previous = self._last
        self._commands.extend(incoming)
        self._commands = [c for c in self._commands if previous_time-0.8 <= c["time"] <= now]
        origin, destination = previous["center"],sample["center"]
        changed = origin != destination
        self._observe_objects(previous, sample, incoming, now, wall)
        # The next sample can confirm either the landing itself or a single
        # acknowledged step whose reported origin is exactly that landing.
        if self._pending:
            pending = self._pending
            if destination == pending["exit"]:
                self._record(pending,wall)
            elif destination[2] == pending["exit"][2]:
                walked_from_exit = (pending.get("evidence")=="observed" and
                    len(incoming)==1 and incoming[0]["kind"]=="step" and
                    incoming[0]["time"]>=previous_time and incoming[0].get("origin")==pending["exit"] and
                    incoming[0].get("entry")==destination and distance(pending["exit"],destination)==1)
                if not walked_from_exit:
                    pending = {**pending,"evidence":"review","reason":"sample_gap"}
                self._record(pending,wall)
            self._pending = None

        commands = self._commands
        if self._preparation and now-self._preparation["time"]>6:
            self._preparation = None
        for command in incoming:
            if command["kind"] in {"use","use_with"}:
                self._preparation = None
                tile = cell_at(previous,command["entry"])
                if tile and distance(origin,command["entry"])<=1:
                    item = next((i for i in tile["items"] if i["stack"]==command["stack"] and
                                 (command.get("item_id") is None or i["id"]==command["item_id"])),None)
                    if item:
                        self._preparation = {**command,"item_id":item["id"],"ids":[i["id"] for i in tile["items"]][:16],"changed":False}
            elif command["kind"] in {"ambiguous","cancel"}:
                self._preparation = None
        if self._preparation and not changed:
            tile = cell_at(sample,self._preparation["entry"])
            if tile and not self._preparation["changed"] and self._preparation["item_id"] not in [i["id"] for i in tile["items"]]:
                self._preparation["changed"] = True
                replacement=next((i for i in tile["items"] if i["stack"]==self._preparation["stack"]),None)
                if replacement:self._preparation["prepared_item_id"]=replacement["id"]
        # Track a door only when use was adjacent, matched the object, and opening is observed.
        for command in incoming:
            if command["kind"] == "use":
                tile = cell_at(previous,command["entry"])
                if door(tile) and distance(origin,command["entry"])<=1 and any(i["id"]==command["item_id"] and i["stack"]==command["stack"] for i in tile["items"]):
                    self._door_use = {**command,"origin":list(origin),"ids":[i["id"] for i in tile["items"]][:16]}
                else:
                    self._door_use = None
            elif command["kind"] in {"ambiguous","cancel","use_with"}:
                self._door_use = self._door_visit = None
        if self._door_use and now-self._door_use["time"]>6:
            self._door_use = None
        current_tile = cell_at(sample,destination)
        walked_out = len(commands)==1 and commands[0]["kind"]=="step" and commands[0]["origin"]==origin and commands[0]["entry"]==destination
        # A short acknowledged walk can cross an open door between map samples.
        # Require the complete command chain and unchanged open door on both
        # samples; never infer a crossing from proximity or a teleport alone.
        if changed and 2<=len(commands)<=8 and all(c['kind']=='step' for c in commands):
            chain=[list(origin)]+[c['entry'] for c in commands]
            if chain[-1]==destination and all(c['origin']==chain[i] and distance(chain[i],chain[i+1])==1 for i,c in enumerate(commands)):
                for a,entry,b in zip(chain,chain[1:],chain[2:]):
                    before,after=cell_at(previous,entry),cell_at(sample,entry)
                    if (a!=b and before and after and before['kind']==after['kind']=='clear'
                        and before['items']==after['items']
                        and any(f.get('kind')=='door' and f.get('door_state_hint')=='open' for f in before.get('navigation',[]))
                        and any(f.get('kind')=='door' and f.get('door_state_hint')=='open' for f in after.get('navigation',[]))):
                        self._record({'origin':list(a),'entry':list(entry),'exit':list(b),
                                      'action':'step','entry_items':[i['id'] for i in before['items']],
                                      'direction':'same_floor','evidence':'observed','reason':'door_crossing'},wall)
        if changed and self._door_visit and origin == self._door_visit["entry"] and distance(origin,destination)==1 and walked_out:
            visit = self._door_visit
            self._record({"origin":visit["origin"],"entry":list(origin),"exit":list(destination),
                          "action":visit["action"],"item_id":visit.get("item_id"),"entry_items":visit["ids"],
                          "direction":"same_floor","evidence":"observed","reason":"door_crossing"},wall)
            self._door_visit = None
        elif changed:
            self._door_visit = None
        if changed and door(current_tile) and current_tile["kind"] == "clear" and distance(origin,destination)<=1:
            used = self._door_use
            if used and used["entry"] == destination and used["item_id"] not in [i["id"] for i in current_tile["items"]]:
                self._door_visit = {"entry":list(destination),"origin":used["origin"],"action":"use_then_step","item_id":used["item_id"],"ids":used["ids"]}
            elif len(commands)==1 and commands[0]["kind"]=="step" and commands[0]["entry"]==destination:
                self._door_visit = {"entry":list(destination),"origin":list(origin),"action":"step","ids":[i["id"] for i in current_tile["items"]][:16]}

        is_jump = origin[2] != destination[2] or distance(origin,destination)>8
        if changed and len(commands)==1 and commands[0]["kind"] in {"step","use","use_with","fly_up"}:
            target = cell_at(previous,commands[0]["entry"])
            # Detect a short same-floor teleport only when its source is already identified.
            if target and any(f["kind"]=="teleport" for f in target.get("navigation",[])) and destination != commands[0]["entry"]:
                is_jump = True
        if changed and is_jump:
            self._pending = self._attribute(previous,sample,commands)
            self._door_use = self._door_visit = None
        if changed:
            self._commands = []
            self._preparation = None
        self._last = (now,sample)
        self._publish("learning")

    def _observe_objects(self, previous, current, commands, now, wall):
        origin, destination=previous["center"],current["center"]
        def record(entry, change, before, after, item_id):
            self._record({"origin":list(before),"entry":list(entry),"exit":list(after),"action":"use",
                          "item_id":item_id,"entry_items":[change["closed_id"],change["open_id"]],
                          "direction":"same_floor","evidence":"observed","reason":"matched_command",
                          "object_change":change},wall)
        if commands:
            self._object_candidate=None
            if len(commands)==1 and commands[0]["kind"]=="use":
                command=commands[0];tile=cell_at(previous,command["entry"])
                if tile and distance(origin,command["entry"])<=1:
                    item=next((i for i in tile["items"] if i["id"]==command["item_id"] and i["stack"]==command["stack"]),None)
                    if item:
                        kinds=[f["kind"] for f in tile.get("navigation",[]) if f["kind"] in {"door","level_gate"}]
                        self._object_candidate={**command,"origin":list(origin),"item":item,
                                                "object_kind":kinds[0] if kinds else "passage"}
            if any(c["kind"] not in {"step"} for c in commands):self._exit_check=None
        candidate=self._object_candidate
        if candidate and now-candidate["time"]>2:self._object_candidate=candidate=None
        if candidate:
            tile=cell_at(current,candidate["entry"])
            after=next((i for i in tile["items"] if i["stack"]==candidate["stack"]),None) if tile else None
            before=candidate["item"]
            if after and before["id"]!=after["id"]:
                was_blocked="not_walkable" in before.get("flags",[])
                is_blocked="not_walkable" in after.get("flags",[])
                same_place=destination==candidate["origin"]
                entered=destination==candidate["entry"]
                if was_blocked!=is_blocked and (same_place or (entered and was_blocked)):
                    change={"kind":candidate["object_kind"],
                            "closed_id":before["id"] if was_blocked else after["id"],
                            "open_id":after["id"] if was_blocked else before["id"],
                            "behavior":("use_enters" if entered else "open_then_step") if was_blocked else "manual_close"}
                    record(candidate["entry"],change,candidate["origin"],destination,before["id"])
                self._object_candidate=None
        if (origin!=destination and distance(origin,destination)==1 and len(commands)==1
                and commands[0]["kind"]=="step" and commands[0]["origin"]==origin and commands[0]["entry"]==destination):
            known=next((r for r in reversed(list(self.records.values())) if r.get("object_change") and r["entry"]==origin),None)
            if known:
                self._exit_check={"entry":list(origin),"destination":list(destination),"change":known["object_change"],"at":now}
        check=self._exit_check
        if check:
            tile=cell_at(current,check["entry"])
            ids={i["id"] for i in tile["items"]} if tile else set()
            change=check["change"]
            behavior="closes_after_exit" if change["closed_id"] in ids else "stays_open" if now-check["at"]>=2 and change["open_id"] in ids else None
            if behavior:
                record(check["entry"],{**change,"behavior":behavior},check["entry"],check["destination"],change["open_id"])
                self._exit_check=None
            elif now-check["at"]>3:self._exit_check=None

    def annotate(self, cells):
        by_position = {}
        changes = {}
        for row in self.records.values():
            if row["entry"] is not None and row["evidence"]=="observed":
                key=tuple(row["entry"])
                if row.get("object_change"):
                    changes.setdefault(key,[]).append(row)
                    crossing=gate_entry_record(row)
                    if crossing:by_position.setdefault(key,[]).append(crossing)
                elif row["exit"]!=row["origin"]:
                    by_position.setdefault(key,[]).append(row)
        result=[]
        for cell in cells:
            key=(cell["x"],cell["y"],cell["z"])
            features=[dict(f) for f in cell.get("navigation",[])]
            for item in cell["items"]:
                observed=[r for r in changes.get(key,[]) if item["id"] in (r["object_change"]["closed_id"],r["object_change"]["open_id"])]
                if not observed:continue
                pairs={(r["object_change"]["closed_id"],r["object_change"]["open_id"]) for r in observed}
                if len(pairs)!=1:continue  # Conflicting pairs require review.
                closed,opened=next(iter(pairs));facts={r["object_change"]["behavior"] for r in observed}
                old=next((f for f in features if f.get("item_id")==item["id"]),{})
                change=observed[-1]["object_change"]
                feature={**old,"kind":old.get("kind",change["kind"]),"item_id":item["id"],"stack":item["stack"],
                         "source":"observed" if old.get("source")!="user_annotation" else "user_annotation",
                         "observation_count":sum(r["count"] for r in observed),
                         "door_state_hint":"closed" if item["id"]==closed else "open",
                         "behavior_hint":"use_enters" if "use_enters" in facts else "open_then_step" if "open_then_step" in facts else "unknown",
                         "closing_policy":"after_exit" if "closes_after_exit" in facts else "observed_open" if "stays_open" in facts else "manual" if "manual_close" in facts else "unknown"}
                features=[f for f in features if f.get("item_id")!=item["id"]]+[feature]
            # First successful traversal updates the visible object immediately.
            ids={i["id"] for i in cell["items"]}
            matching=[r for r in by_position.get(key,[]) if
                      (r["item_id"] in ids or r.get("prepared_item_id") in ids or r.get("open_item_id") in ids if r["item_id"] is not None else bool(r["entry_items"]) and set(r["entry_items"])<=ids)]
            for row in matching:
                if (row['action']=='use_with_then_step' and row.get('prepared_item_id') and row['direction']=='down'
                        and row.get('tool_id') in {3457,8899,8900}):
                    for obj in cell['items']:
                        if obj['id'] not in (row['item_id'],row['prepared_item_id']):continue
                        kind='hole' if obj['id']==row['prepared_item_id'] else 'covered_hole'
                        if not any(f.get('item_id')==obj['id'] and f.get('source')=='user_annotation' for f in features):
                            features=[f for f in features if f.get('item_id')!=obj['id']]
                            features.append({'kind':kind,'item_id':obj['id'],'stack':obj['stack'],'source':'observed',
                                             'activation_hint':'step' if kind=='hole' else 'shovel','direction_hint':'down','observation_count':row['count']})
            links=[{k:r[k] for k in ("id","exit","action","direction","count")} for r in matching[:4]]
            for index,feature in enumerate(features):
                proof=[r for r in matching if (feature.get("item_id") in (r["item_id"],r.get("prepared_item_id")) if r["item_id"] is not None
                       else feature.get("item_id") in r["entry_items"])]
                if proof and feature["kind"] not in {"door","level_gate"}:
                    features[index]={**feature,"observation_count":sum(r["count"] for r in proof)}
                    if feature.get("source")=="client_hint":features[index]["source"]="observed"
            # A matched tool/use proves this particular object was involved, even without DAT hints.
            for row in matching:
                item=next((i for i in cell["items"] if i["id"]==row["item_id"]),None)
                if item and not any(f.get("item_id")==item["id"] for f in features):
                    features.append({"kind":"rope_spot" if row["tool_id"]==3003 and row["direction"]=="up" else "passage",
                                     "item_id":item["id"],"stack":item["stack"],"source":"observed","observation_count":row["count"]})
            result.append({**cell,"navigation":features,"transitions":links})
        return result
