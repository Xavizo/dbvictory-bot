"""Bounded, read-only map inspection for the verified DBVClient_v5 build.

Reads the client's decoded ThingType catalogue and live Tile objects. It never
invokes client code, injects packets, patches memory, or controls cavebot.
Unknown layouts/cells are reported explicitly rather than guessed.
"""
from collections import Counter
from client_profiles import LEGACY, PROFILES
import hashlib
import logging
import sqlite3
from pathlib import Path
import struct
import threading
import time
from map_navigation import navigation_features, load_confirmed_types, load_location_notes, movable_container_cover
from navigation_tools import has_cut_web
from dbv import paths

CLIENT_SHA256 = "828ea9e11c6890f92cc14956733bac3aa7b80f57bfd8bb8e225568780c6e4b40"
DAT_SHA256 = "26e6342c67378183dd422d3a044a7ef159e27ebfe2af1594fae8e0a35f200c91"
MAP_BLOCKS_RVA = 0xB6E470
MAP_CENTER_RVA = 0xB6E61C
ITEM_TYPES_RVA = 0xB6ED00
TILE_VTABLE_RVA = 0x8BCFC0
ITEM_VTABLE_RVA = 0x8B6890
TYPE_VTABLE_RVA = 0x8BCF8C
TRUE_GETTER_RVA = 0x6C940
# Verified PE RTTI names and constant isNpc/isMonster/isPlayer/isLocalPlayer accessors.
# These addresses are compared only; client functions are never executed.
CREATURE_VTABLES = {0x8B6974:"player", 0x8B6A50:"self", 0x8BCCB0:"monster", 0x8BCD70:"npc"}
U16_ATTRIBUTE_VTABLE_RVA = 0x8B6900
WIDTH, HEIGHT = 18, 14
FLAG_NAMES = {0: "ground", 1: "ground_border", 2: "on_bottom", 3: "on_top", 4: "container", 5: "stackable", 6: "force_use", 7: "multi_use",
              12: "not_walkable", 13: "not_moveable", 14: "blocks_projectiles",
              15: "not_pathable", 16: "pickupable", 252: "floor_change"}


class MapReadError(ValueError):
    pass


class MapChanged(MapReadError):
    """A concurrent update, not a lost process connection."""


def _u32(data, offset=0):
    return struct.unpack_from("<I", data, offset)[0]


def _vector(data, offset, stride, maximum):
    first, last, capacity = struct.unpack_from("<III", data, offset)
    if first == last == capacity == 0:
        return 0, 0
    if not (0x10000 <= first <= last <= capacity < 0x7FFE0000):
        raise MapReadError("Nieprawidłowa struktura danych mapy.")
    if (last - first) % stride or (capacity - first) % stride or (capacity - first) // stride > maximum:
        raise MapReadError("Nieobsługiwany rozmiar danych mapy.")
    return first, (last - first) // stride


def inspect_dat(path):
    """Identify the asset without treating opaque bytes as standard DAT flags."""
    try:
        path = Path(path)
        if path.stat().st_size > 32 * 1024 * 1024:
            return {"status": "too_large", "message": "Plik DAT przekracza limit odczytu."}
        data = path.read_bytes()
    except OSError:
        return {"status": "unavailable", "message": "Plik DAT niedostępny."}
    digest = hashlib.sha256(data).hexdigest()
    return {"status": "opaque", "size": len(data), "sha256": digest,
            "known_asset": any(p.dat_sha256 == digest for p in PROFILES.values()),
            "confirmed_catalogue": digest == DAT_SHA256,
            "message": "Plik DAT w niestandardowym formacie. Właściwości pochodzą z definicji załadowanych przez klienta."}


def classify_tile(items, server_blocking):
    flags = {f for item in items for f in item["flags"]}
    stairs = "type_flag" if "floor_change" in flags else None
    if server_blocking or "not_walkable" in flags:
        kind = "blocked"
    elif "ground" not in flags:
        kind = "no_ground"
    elif "not_pathable" in flags and not movable_container_cover(items):
        kind = "avoid"
    else:
        kind = "clear"
    return kind, stairs


class MapReader:
    """Decoder separated from Win32 access so corrupt layouts are testable."""
    def __init__(self, read, base, allow_confirmed=True, profile=LEGACY, asset_sha256=None):
        self.profile = profile
        self.read = read
        self.base = base
        self.monster_slots = {}
        asset_sha256 = asset_sha256 or profile.dat_sha256
        self.navigation_types = load_confirmed_types(asset_sha256=asset_sha256) if allow_confirmed else {}
        self.location_notes = load_location_notes(asset_sha256=asset_sha256) if allow_confirmed else {}

    def _blocks(self, floor, wanted):
        head, count = struct.unpack("<II", self.read(self.base + self.profile.map_blocks + floor * 8, 8))
        if count > 256 or not 0x10000 <= head < 0x7FFE0000:
            raise MapReadError("Nieprawidłowy indeks pól mapy.")
        header = self.read(head, 16)
        if header[13] != 1:
            raise MapReadError("Nieobsługiwany indeks pól mapy.")
        pending = [_u32(header, 4)]
        visited, blocks = set(), {}
        while pending:
            node = pending.pop()
            if node == head:
                continue
            if node in visited or len(visited) >= count:
                raise MapChanged("Mapa zmieniła się podczas odczytu.")
            visited.add(node)
            raw = self.read(node, 20)
            if raw[13] != 0:
                raise MapReadError("Nieprawidłowy węzeł mapy.")
            pending.extend([_u32(raw), _u32(raw, 8)])
            key = _u32(raw, 16)
            if key in wanted:
                blocks[key] = self.read(node + 20, 8192)
        if len(visited) != count:
            raise MapReadError("Mapa zmieniła się podczas odczytu.")
        return blocks

    def _item(self, item_id, types, count, cache):
        if item_id in cache:
            return cache[item_id]
        if not 100 <= item_id < count:
            raise MapReadError("Nieznane ID obiektu.")
        ptr = _u32(self.read(types + item_id * 8, 8))
        raw = self.read(ptr, 44)
        if (_u32(raw) != self.base + self.profile.type_vtable or raw[24] != 0
                or struct.unpack_from("<H", raw, 26)[0] != item_id or raw[28] != 0):
            raise MapReadError("Definicja obiektu nie pasuje do ID.")
        attrs, size = _vector(raw, 32, 4, 256)
        flags_raw = self.read(attrs, size * 4) if size else b""
        flags = [name for index, name in FLAG_NAMES.items()
                 if index < size and _u32(flags_raw, index * 4)]
        result = {"id": item_id, "flags": flags}
        # stdext::any::holder<uint16>: verified vtable, then the U16 payload.
        # Optional help metadata must never turn a failed read into a guessed type.
        for index, name in ((28,"minimap_color"), (29,"lens_help")):
            if size > index and _u32(flags_raw, index * 4):
                try:
                    help_raw = self.read(_u32(flags_raw, index * 4), 6)
                    if _u32(help_raw) == self.base + self.profile.u16_attribute_vtable:
                        result[name] = struct.unpack_from("<H", help_raw, 4)[0]
                except (MapReadError, struct.error):
                    pass
        cache[item_id] = result
        return result

    def _tile(self, ptr, xyz, types, type_count, cache):
        raw = self.read(ptr, 84)
        if _u32(raw) != self.base + self.profile.tile_vtable or struct.unpack_from("<III", raw, 56) != xyz:
            raise MapReadError("Pole zmieniło położenie podczas odczytu.")
        first, count = _vector(raw, 44, 8, 64)
        things = self.read(first, count * 8) if count else b""
        items, occupants = [], 0
        occupant_types = Counter()
        alert_players = []
        monster_stacks = []
        for i in range(count):
            thing = _u32(things, i * 8)
            data = self.read(thing, 56)
            vtable = _u32(data)
            if vtable == self.base + self.profile.item_vtable:
                # Verified getId accessor reads U16 at +0x35 (packed object).
                item_id = struct.unpack_from("<H", data, 0x35)[0]
                items.append({**self._item(item_id, types, type_count, cache), "stack": i})
            elif self.base <= vtable < self.base + 0xC00000:
                # isCreature() is a verified constant-true accessor, never called.
                if _u32(self.read(vtable + 8 * 4, 4)) != self.base + self.profile.true_getter:
                    raise MapReadError("Nieobsługiwany obiekt na polu.")
                occupants += 1
                creature_kind = dict(self.profile.creature_vtables).get(vtable-self.base,"unknown")
                occupant_types[creature_kind] += 1
                if creature_kind in ("player", "monster"):
                    try:
                        cid = _u32(self.read(thing + 0x38, 4)) if self.profile.observer else _u32(data, 0x34)
                    except (MapReadError, struct.error):
                        cid = 0  # A missing optional ID does not change occupancy.
                    if creature_kind == "player" and 0x10000000 <= cid < 0x40000000:
                        alert_players.append({"id": cid})
                    elif creature_kind == "monster":
                        # A map-only summon can lack a battle ID. Its local
                        # identity stays private and expires when it leaves view.
                        identity = cid if cid not in (0, 0xffffffff) else ("visible", thing)
                        monster_stacks.append((i, identity))
            else:
                raise MapReadError("Nieprawidłowy typ obiektu na polu.")
        # Detect container changes rather than publishing a mixed snapshot.
        if self.read(ptr + 44, 24) != raw[44:68]:
            raise MapReadError("Pole zmieniło się podczas odczytu.")
        blocking = raw[82]
        kind, stairs = classify_tile(items, blocking)
        navigation = navigation_features(items, self.navigation_types)
        note = self.location_notes.get(xyz)
        if note and any(i["id"]==note["item_id"] for i in items):
            navigation = [f for f in navigation if f["item_id"]!=note["item_id"]]
            item = next(i for i in items if i["id"]==note["item_id"])
            navigation.append({**note,"stack":item["stack"],"source":"confirmed_location",
                               "door_state_hint":"closed" if "not_walkable" in item["flags"] else "open",
                               "closing_policy":"unknown"})
        if any(f["kind"] == "stairs" and f["source"] == "confirmed_id" for f in navigation):
            stairs = "confirmed_id"
        for stack, identity in monster_stacks:
            self.monster_slots[(*xyz, stack)] = identity
        return {"x": xyz[0], "y": xyz[1], "z": xyz[2], "kind": kind,
                **({"web_cut":True} if has_cut_web(items,self.navigation_types) else {}),
                "stairs": stairs, "items": items, "occupants": occupants,
                "occupant_types":dict(occupant_types), "alert_players": alert_players,
                "navigation": navigation,
                "server_blocking": blocking, "ground_speed": struct.unpack_from("<H", raw, 80)[0]}

    def snapshot(self):
        self.monster_slots.clear()
        started = time.perf_counter()
        center = struct.unpack("<III", self.read(self.base + self.profile.map_center, 12))
        x, y, z = center
        if not (8 <= x <= 65525 and 6 <= y <= 65527 and z < 16):
            raise MapReadError("Brak poprawnej pozycji klienta.")
        origin = (x - 8, y - 6)
        wanted = {((yy // 32) * 2048 + xx // 32)
                  for xx in (origin[0], origin[0] + WIDTH - 1)
                  for yy in (origin[1], origin[1] + HEIGHT - 1)}
        blocks = self._blocks(z, wanted)
        types_raw = self.read(self.base + self.profile.item_types, 12)
        types, count = _vector(types_raw, 0, 8, 65536)
        if count <= 10161:
            raise MapReadError("Katalog obiektów nie został załadowany.")
        cache, cells = {}, []
        for yy in range(origin[1], origin[1] + HEIGHT):
            for xx in range(origin[0], origin[0] + WIDTH):
                cell = {"x": xx, "y": yy, "z": z, "kind": "unknown", "items": [], "occupants": 0, "stairs": None}
                try:
                    block = blocks.get((yy // 32) * 2048 + xx // 32)
                    ptr = _u32(block, ((yy % 32) * 32 + xx % 32) * 8) if block else 0
                    if ptr:
                        cell = self._tile(ptr, (xx, yy, z), types, count, cache)
                    else:
                        cell["reason"] = "Pole nie jest załadowane przez klienta."
                except MapReadError as exc:
                    cell["reason"] = f"Odczyt pola niepełny. {exc}"
                except struct.error:
                    cell["reason"] = "Odczyt pola niepełny. Brak kompletnych danych pola."
                cells.append(cell)
        if self.read(self.base + self.profile.map_center, 12) != struct.pack("<III", *center):
            raise MapChanged("Pozycja zmieniła się podczas odczytu; czekam na następną próbkę.")
        return {"status": "ready", "center": list(center), "origin": list(origin),
                "width": WIDTH, "height": HEIGHT, "cells": cells,
                "counts": dict(Counter(c["kind"] for c in cells)),
                "catalogue_items": count, "captured_at": time.time(),
                "read_ms": round((time.perf_counter() - started) * 1000, 1)}


class PassiveMapMonitor:
    """Keep memory I/O off the proxy event loop; no action scheduling."""
    ACTIVE_INTERVAL = .05
    def __init__(self, state):
        self.state = state
        self._result = {"status": "waiting", "message": "Czekam na połączenie z grą.", "cells": []}
        self._stop = threading.Event()
        self._refresh = threading.Event()
        self._thread = threading.Thread(target=self._run, name="passive-map", daemon=True)
        self.learner = None
        self.annotations = None
        self.atlas = None
        self._learning_error = None
        self._manual_play = False
        self._visit = self._visit_writer = None
        self._visit_epoch = 0
        self._visit_key = None
        self._knife_witness_lock = threading.Lock()
        self._knife_witness = None

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._refresh.set()
        self._thread.join(timeout=2)

    def request_refresh(self):
        """Wake the existing reader when navigation is waiting for a new center."""
        self._refresh.set()

    def poll_interval(self):
        task=getattr(self.state,"_action_tasks",{}).get("adaptive_cavebot")
        return self.ACTIVE_INTERVAL if task and not task.done() else .25

    def snapshot(self):
        result = self._result
        age = time.time() - result.get("captured_at", time.time())
        learning = self.learner.snapshot() if self.learner else {"status":"waiting","total":0,"observed":0,"records":[],"passive":True}
        if self._learning_error:
            learning = {**learning,"status":"error","storage_error":self._learning_error}
        return {**result, "learning":learning,
                "annotation_scope":self.annotations.scope if self.annotations else None,
                "annotation_error":self.annotations.error if self.annotations else None,
                "annotation_editable":bool(self.annotations and not self.annotations.error and self.state.connected and result.get("status")=="ready" and age<=2),
                "age_seconds": round(max(0, age), 2), "passive": True,
                "stale": result.get("status") != "ready" or age > 2}

    def save_annotation(self,body):
        from map_annotations import validate_note, FIELDS
        if not isinstance(body,dict) or set(body)!=FIELDS|{"scope","operation"} or body["operation"] not in ("save","remove"):
            raise ValueError("Nieprawidłowe żądanie opisu pola.")
        note=validate_note({k:body[k] for k in FIELDS})
        store=self.annotations
        result=self._result
        if (not self.state.connected or store is None or body["scope"]!=store.scope or
                result.get("status")!="ready" or time.time()-result.get("captured_at",0)>2):
            raise ValueError("Mapa jest nieaktualna. Wybierz pole ponownie na żywo.")
        cell=next((c for c in result["cells"] if [c["x"],c["y"],c["z"]]==note["position"]),None)
        if not cell or cell["kind"]=="unknown" or not any(i["id"]==note["item_id"] for i in cell["items"]):
            raise ValueError("Obiekt nie jest już widoczny na tym polu. Wybierz pole ponownie.")
        store.save(note,remove=body["operation"]=="remove")
        return "Usunięto własny opis." if body["operation"]=="remove" else "Zapisano własny opis pola."

    def record_client_packet(self, opcode, reader):
        from protocol import ClientOpcode
        if opcode == ClientOpcode.LOOK and self.state.connected:
            if reader.remaining == 8:
                pos = reader.read_position()
                reader.read_u16()
                self.state.game_state.pets.look(pos, reader.read_u8())
            return
        if not self.state.connected:
            return
        from transition_learning import WALKS, decode_command
        movement = opcode in WALKS or opcode in (ClientOpcode.NEW_WALK, ClientOpcode.AUTO_WALK,
                                                 ClientOpcode.STOP_WALK, ClientOpcode.FOLLOW, ClientOpcode.LOGOUT)
        if self.learner is None and not movement and opcode != ClientOpcode.USE_ITEM_EX:
            return
        result = self._result
        origin = result.get("center") if 0 <= time.time()-result.get("captured_at",0)<0.8 else None
        command = decode_command(opcode,reader,origin)
        if command and command.get("kind") == "use_with" and command.get("tool_id") == 5908:
            from hunter_knife import manual_corpse_ids
            witness = None
            if origin and result.get("status") == "ready":
                pos = tuple(command["entry"])
                ids = manual_corpse_ids(result, pos)
                if ids == [(command["stack"], command.get("item_id"))]:
                    witness = (self.state.game_state, pos, ids[0], time.time())
            with self._knife_witness_lock:
                self._knife_witness = witness
        if command and not movement:
            history = getattr(self.state.game_state, "movement_history", None)
            if history:
                history.interrupt_floor_step()
        if (origin and command and command.get('kind') == 'use_with' and command.get('tool_id') in (8899, 8900)):
            history = getattr(self.state.game_state, 'movement_history', None)
            if history:
                from transition_learning import cell_at
                target = command['entry']
                history.web_tool('client', target, command['tool_id'], command.get('item_id'),
                                 command.get('stack'), cell_at(result, target))
        if movement:
            request = command if command and command['kind'] == 'step' else {}
            self.state.game_state.client_walk_requests.append((time.perf_counter(), ClientOpcode(opcode).name,
                                                               request.get('origin'), request.get('entry')))
            history = getattr(self.state.game_state, "movement_history", None)
            if history:
                if opcode in WALKS or opcode in (ClientOpcode.NEW_WALK, ClientOpcode.AUTO_WALK):
                    history.request("client", request.get("origin") or origin, request.get("entry"), result if origin else None)
                else:
                    history.stop_request()
        if self.learner is not None:
            self.learner.enqueue(command)

    def reset_learning_session(self):
        self._visit_epoch += 1
        self._result = {"status":"waiting","message":"Czekam na połączenie z grą.","cells":[]}
        self.invalidate_packet_evidence()

    def _check_manual_knife(self, game_state, frame):
        from hunter_knife import manual_corpse_ids
        with self._knife_witness_lock:
            if self._knife_witness is None:
                return
            owner, pos, (stack, original), started = self._knife_witness
            if owner is not game_state or time.time() - started > 2:
                self._knife_witness = None
                return
            if frame.get("status") != "ready" or frame.get("captured_at", 0) <= started:
                return
            ids = manual_corpse_ids(frame, pos)
            if ids is None or ids == [(stack, original)]:
                return
            self._knife_witness = None
            if len(ids) == 1 and ids[0][0] == stack and ids[0][1] != original:
                logging.getLogger(__name__).info(
                    "Hunter Knife 5908 manual: tile=%s corpse_before=%d corpse_after=%d",
                    pos, original, ids[0][1])

    def invalidate_packet_evidence(self):
        with self._knife_witness_lock:
            self._knife_witness = None
        """A packet-analysis gap invalidates correlations, not native map reads."""
        history = getattr(getattr(self.state, "game_state", None), "movement_history", None)
        if history is not None:
            history.take_floor_steps(False)
        if self.learner:
            self.learner.reset()

    def _read_interrupted(self, message, asset=None):
        """Retain only a recent image. Never present it as live or learn from it."""
        previous=self._result
        if (self.state.connected and previous.get("cells")
                and time.time()-previous.get("captured_at",0)<=2):
            self._result={**previous,"status":"refreshing","message":message}
        else:
            self._result={"status":"unavailable","message":message,"cells":[],"asset":asset}

    def _learn(self, result, asset):
        # Failures in optional learning must not interrupt the map or game proxy.
        try:
            from transition_learning import TransitionLearner
            proxy = getattr(self.state,"game_proxy",None)
            server = f"{getattr(proxy,'server_host','')}:{getattr(proxy,'server_port',0)}"
            scope = hashlib.sha256(f"{asset.get('client_sha256',CLIENT_SHA256)}|{asset.get('sha256')}|{server}".encode()).hexdigest()
            if getattr(self, "_inherited_scope", None) != scope:
                # A client update changes the scope; start it from the same server's previous data.
                from atlas_scope import inherit
                inherit(paths.LOCAL_DATA_DIR, scope, server)
                self._inherited_scope = scope
            if self.learner is None or self.learner.scope != scope:
                self.learner = TransitionLearner(paths.LOCAL_DATA_DIR,scope)
            if self.annotations is None or self.annotations.scope != scope:
                from map_annotations import MapAnnotations
                self.annotations = MapAnnotations(paths.LOCAL_DATA_DIR,scope)
            if self.atlas is None or self.atlas.scope != scope:
                from map_atlas import MapAtlas
                types = load_confirmed_types(asset_sha256=asset.get("sha256"))
                webs = {iid: entry for iid, entry in types.items() if entry['kind'] == 'web'}
                self.atlas = MapAtlas(paths.LOCAL_DATA_DIR,scope,web_definitions=webs)
            tasks = getattr(self.state,"_action_tasks",{})
            auto_task = getattr(self.state,"_auto_task",None)
            automated = ((auto_task is not None and not auto_task.done())
                         or ("adaptive_cavebot" in tasks and not tasks["adaptive_cavebot"].done()))
            gs = getattr(self.state,"game_state",None)
            dead = bool(getattr(gs,"stats_updated_at",0)) and getattr(gs,"hp",0)<=0
            result = {**result,"cells":self.annotations.annotate(result["cells"])}
            result = {**result,"cells":self.learner.annotate(result["cells"])}
            learning = not (automated or dead)
            self._manual_play = learning
            self.learner.observe(result,active=learning)
            self._learning_error = None
            if learning:
                result = {**result,"cells":self.learner.annotate(result["cells"])}
        except Exception:
            self._learning_error = "Uczenie przejść chwilowo niedostępne; podgląd mapy pozostaje aktywny."
        return result

    def _publish(self, result, asset, game_state):
        result = self._learn(result, asset)
        if not self.state.connected or self.state.game_state is not game_state:
            return
        # Navigation needs the annotated live cells as soon as they are ready.
        # Atlas persistence uses the same immutable frame, on the reader thread.
        self._result = {**result, "asset": asset}
        if self._knife_witness is not None:
            self._check_manual_knife(game_state, result)
        journal = getattr(game_state, "observations", None)
        if journal is not None and result.get("status") == "ready":
            # The frame may have waited during learning. Keep its capture age.
            captured = time.monotonic() - max(0., time.time() - result["captured_at"])
            families = ("map", "creatures") if "pet_map_generation" in result else ("map",)
            journal.publish("map_snapshot", families, captured)
        history = getattr(game_state, "movement_history", None)
        if history:
            history.frame(result, getattr(self.state, "packet_observer", None))
            steps = history.take_floor_steps(result.get("status") == "ready" and self._manual_play and not self._learning_error)
            if steps and self.atlas and self.learner:
                try:
                    self.learner.learn_floor_steps(steps, self.atlas._paged_tiles())
                except (OSError, ValueError, sqlite3.Error):
                    logging.getLogger(__name__).warning("Odczyt schodów z atlasu chwilowo niedostępny.", exc_info=True)
        route = getattr(self.state, 'adaptive_cavebot', None)
        reporter = getattr(route, 'reporter', None)
        if (reporter is not None and getattr(route, 'status', None) == 'running'
                and not getattr(route, 'combat_only', False) and reporter.gs is game_state
                and result.get('status') == 'ready' and 'pet_map_generation' in result):
            reporter.observe_map(result)
        if self.atlas is not None and not self._learning_error:
            try:
                self.atlas.observe(result)
            except Exception:
                self._learning_error = "Zapis atlasu chwilowo niedostępny."
        try:
            manual = (self._manual_play and not self._learning_error and self.atlas is not None
                      and result.get('status')=='ready' and 'pet_map_generation' in result
                      and getattr(route,'status',None)!='running')
            if manual:
                key = (game_state,self.atlas,self._visit_epoch)
                if self._visit_key != key:
                    self._end_manual_visit()
                    from manual_hunts import DiscoveryWriter, ManualVisits
                    if self._visit_writer is None:
                        self._visit_writer = DiscoveryWriter()
                    atlas, learner = self.atlas, self.learner
                    def submit(visit, final):
                        rows = list(learner.records.values()) if learner else []
                        self._visit_writer.submit(atlas,visit,final,rows)
                    self._visit = ManualVisits(game_state,submit)
                    self._visit_key = key
                self._visit.observe(result)
            elif self._visit is not None:
                self._end_manual_visit()
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Błąd pasywnego rozpoznawania expowiska')

    def _end_manual_visit(self):
        if self._visit is not None:
            self._visit.finish()
            self._visit = self._visit_key = None

    def _run(self):
        source = None
        asset = None
        retrying_frame = False
        try:
            while not self._stop.is_set():
                self._refresh.clear()
                try:
                    if not self.state.connected:
                        self._end_manual_visit()
                        if self.learner:
                            self.learner.reset()
                            self.learner.observe(None)
                        if source:
                            source.close()
                            source = None
                        self._result = {"status": "waiting", "message": "Czekam na połączenie z grą.", "cells": []}
                    else:
                        if source is None:
                            from passive_map_win32 import ReadOnlyClient
                            source = ReadOnlyClient()
                            asset = inspect_dat(source.path.parent / "bytes" / "things" / "file.dat")
                            asset["client_sha256"] = source.profile.sha256
                        gs = self.state.game_state
                        reader = MapReader(source.read, source.base, allow_confirmed=True, profile=source.profile, asset_sha256=asset.get("sha256"))
                        result = reader.snapshot()
                        result["pet_map_generation"] = gs.pets.observe_map(result["cells"], result["captured_at"],
                                                                           reader.monster_slots, gs.creatures)
                        if gs is not self.state.game_state or not self.state.connected:
                            continue
                        self._publish(result, asset, gs)
                except MapChanged as exc:
                    self._read_interrupted("Aktualizacja podczas ruchu — ostatni poprawny obraz.",asset)
                    # Keep the read-only handle and pending learning. Retry promptly;
                    # the learner itself rejects gaps beyond its time budget.
                    # A torn frame has no useful center. Back off once and then
                    # recapture; a second ordinary poll delay adds a visible gap.
                    interval = self.poll_interval()
                    retry_delay = .01 if interval == self.ACTIVE_INTERVAL and not retrying_frame else interval
                    retrying_frame = True
                    if self._stop.wait(retry_delay):
                        break
                    continue
                except Exception as exc:
                    # No addresses, process contents, credentials or raw packets in UI/logs.
                    message = str(exc) if isinstance(exc, MapReadError) else "Odczyt mapy niedostępny. Sprawdź klienta i jego wersję."
                    self._read_interrupted(message,asset)
                    if self.learner:
                        self.learner.reset()
                        self.learner.observe(None)
                    if source:
                        source.close()
                        source = None
                    if self._stop.wait(1.5):
                        break
                retrying_frame = False
                self._refresh.wait(self.poll_interval())
        finally:
            if source:
                source.close()
            self._end_manual_visit()
            if self._visit_writer is not None:
                self._visit_writer.close()
