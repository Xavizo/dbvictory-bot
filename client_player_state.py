"""Bounded read-only LocalPlayer values for the verified updated client."""
import math
import struct
import time
from client_profiles import UPDATED, NOVEMBER_26
from passive_map import MapReader, MapReadError, _u32, _vector

FRESH_SECONDS = 0.5


def client_field(name, default=0):
    """Atomic client snapshot wins over packet fields on supported profiles."""
    def get(gs):
        if not getattr(gs, "client_state_required", False):
            return gs.__dict__.get(name, default)
        snapshot = getattr(gs, "client_snapshot", None)
        sampled_at = getattr(gs, "_packet_snapshot_time", None)
        now = sampled_at if sampled_at is not None else time.monotonic()
        if snapshot and 0 <= now - snapshot["monotonic"] <= FRESH_SECONDS:
            return snapshot.get(name, default)
        # Never fall back to the lagging packet cache for healing.
        if name == "position" and snapshot:
            return snapshot["position"]
        return default
    return property(get, lambda gs, value: gs.__dict__.__setitem__(name, value))


class ClientPlayerReader:
    def __init__(self, source, player_id):
        if source.profile not in (UPDATED, NOVEMBER_26):
            raise MapReadError("Bezpośredni odczyt statystyk nie obsługuje tej wersji.")
        self.source, self.player_id = source, player_id
        self.layout = source.profile.player_layout
        self.local_vtable = next(rva for rva, kind in source.profile.creature_vtables if kind == "self")
        self.reader = MapReader(source.read, source.base, False, profile=source.profile)
        self.ptr = None
        # Pinned getSpeed/getStates Lua bindings; read only, never call native code.
        try:
            states_signature = b'\x8b\x81' + struct.pack('<I', self.layout.conditions) + b'\xc3'
            self.conditions_supported = (source.read(source.base+self.layout.speed_getter,8)==bytes.fromhex('66 8b 81 d4 00 00 00 c3')
                and source.read(source.base+self.layout.states_getter,7)==states_signature)
        except Exception:
            self.conditions_supported = False

    def _locate(self, center):
        x,y,z=center
        blocks=self.reader._blocks(z,{(y//32)*2048+x//32})
        block=blocks.get((y//32)*2048+x//32)
        if not block: raise MapReadError("Brak lokalnego gracza na mapie.")
        tile=_u32(block,((y%32)*32+x%32)*8)
        raw=self.source.read(tile,84)
        if _u32(raw)!=self.source.base+self.source.profile.tile_vtable or struct.unpack_from('<III',raw,56)!=center:
            raise MapReadError("Pozycja gracza zmienia się.")
        first,count=_vector(raw,44,8,64)
        candidates = []
        for i in range(count):
            ptr=_u32(self.source.read(first+i*8,8))
            obj=self.source.read(ptr,0x3C)
            pid = _u32(obj,0x38)
            if (_u32(obj)==self.source.base+self.local_vtable and 0x10000000<=pid<0x20000000
                    and (not self.player_id or pid==self.player_id)):
                candidates.append(ptr)
        if len(candidates)==1:
            return candidates[0]
        raise MapReadError("Brak jednoznacznej tożsamości lokalnego gracza.")

    def snapshot(self):
        s=self.source
        center=struct.unpack('<III',s.read(s.base+s.profile.map_center,12))
        if not (8<=center[0]<=65525 and 6<=center[1]<=65527 and center[2]<16):
            raise MapReadError("Nieprawidłowa pozycja gracza.")
        try:
            if self.ptr is None: self.ptr=self._locate(center)
            obj=s.read(self.ptr,0x3C)
            pid = _u32(obj,0x38)
            if (_u32(obj)!=s.base+self.local_vtable or not 0x10000000<=pid<0x20000000
                    or (self.player_id and pid!=self.player_id)):
                raise MapReadError("Zmieniła się tożsamość lokalnego gracza.")
            # Per-build constructor/getters; new builds still need Windows acceptance.
            raw=s.read(self.ptr+self.layout.stats,80)
            # getDirection reads [this+0x58] in both reviewed builds.
            direction=None
            try:
                facing_raw=s.read(self.ptr+0x58,4)
                value=struct.unpack('<I',facing_raw)[0]
                if value<=7 and s.read(self.ptr+0x58,4)==facing_raw:direction=value
            except (MapReadError,struct.error):pass
            values=struct.unpack('<10d',raw)
            hp, maximum, _, _, _, level, _, mana, max_mana, magic=values
            if not all(math.isfinite(v) and v==int(v) for v in (hp,maximum,mana,max_mana,level,magic)):
                raise MapReadError("Niespójne wartości statystyk.")
            if not (0<=hp<=maximum<=2**31-1 and maximum>0 and 0<=mana<=2**31-1 and
                    0<max_mana<=2**31-1 and 1<=level<=5000 and 0<=magic<=5000):
                raise MapReadError("Statystyki przekraczają sprawdzony zakres.")
            if s.read(self.ptr+self.layout.stats,80)!=raw or s.read(self.ptr,0x3C)!=obj:
                raise MapReadError("Statystyki zmieniły się podczas odczytu.")
            # An unstable/unknown facing must only inhibit directional spells,
            # never discard otherwise valid healing statistics.
            if direction is not None:
                try:
                    if s.read(self.ptr+0x58,4)!=facing_raw:direction=None
                except MapReadError:direction=None
            conditions = {}
            if self.conditions_supported:
                try:
                    flags = s.read(self.ptr+self.layout.conditions,4)
                    speed = s.read(self.ptr+0xD4,2)
                    if (s.read(self.ptr+self.layout.conditions,4)==flags and s.read(self.ptr+0xD4,2)==speed
                            and s.read(self.ptr,0x3C)==obj):
                        conditions = {"conditions":_u32(flags),"movement_speed":struct.unpack('<H',speed)[0]}
                except (MapReadError, struct.error):
                    pass  # Optional effect reads never invalidate healing values.
            if not self.player_id:
                if s.read(s.base+s.profile.map_center,12)!=struct.pack("<III",*center):
                    raise MapReadError("Pozycja zmieniła się podczas identyfikacji.")
                self.player_id=pid
            return {**conditions,"player_id":pid,"hp":int(hp),"max_hp":int(maximum),"mana":int(mana),"max_mana":int(max_mana),
                    "level":int(level),"magic_level":int(magic),"position":center,
                    "direction":direction,
                    "stats_updated_at":time.time(),"monotonic":time.monotonic(),"source":"client"}
        except (MapReadError, struct.error):
            self.ptr=None
            raise


async def poll_bound_vitals(bot, state, gs, reader, interval):
    """Keep the existing vitals source on its own schedule during diagnostic waits."""
    while bot.is_connected and state.game_state is gs:
        try:
            sample = reader.snapshot()
        except Exception as exc:
            gs.client_state_error = type(exc).__name__
        else:
            gs.client_snapshot = sample
            gs.client_state_error = None
        await bot.sleep(interval)


async def bind_local_player(bot, state, gs, reader):
    """Bind one verified LocalPlayer for this login, never an old packet ID."""
    gs.client_state_required = True
    gs.client_snapshot = None
    connection = getattr(getattr(state, "game_proxy", None), "recovery_connection_id", None)
    gs.engine_recovery_required = isinstance(connection, str) and bool(connection)
    if gs.engine_recovery_required:
        gs.engine_journal = None
    gs.client_state_error = "Czekam na postać w kliencie"
    for _ in range(150):
        if not bot.is_connected or state.game_state is not gs:
            return False
        try:
            sample = reader.snapshot()
        except (MapReadError, struct.error) as exc:
            gs.client_state_error = str(exc)
            await bot.sleep(.2)
            continue
        if gs.engine_recovery_required:
            if sample.get("connection_id") != connection:
                return False
            journal = await state.local_transport.bind_recovery(connection, reader.player_id)
            if not bot.is_connected or state.game_state is not gs:
                return False
            gs.engine_journal = journal
        gs.client_player_id = reader.player_id
        gs.player_id = reader.player_id
        gs.client_snapshot = sample
        gs.client_state_error = None
        if gs.engine_recovery_required and not getattr(state, "adaptive_cavebot", None):
            from adaptive_cavebot import AdaptiveCavebot, InputsUnavailable
            try:
                state.adaptive_cavebot = AdaptiveCavebot.from_session_cursor(state)
            except (InputsUnavailable, ValueError):
                pass  # Map or user pause can defer restoration until explicit resume.
        return True
    return False
