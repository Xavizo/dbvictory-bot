"""Read-only equipment/container snapshot for the pinned 2026-11-16 client.

Verified accessors: LocalPlayer::getInventoryItem BA350, Game::getContainers
BA790, Container::getItem 6F9A0 and getSlotPosition 6F6B0 (all RVAs).
No heap scanning and no calls into the game process.
"""
import struct
import time
from client_profiles import UPDATED, NOVEMBER_26
from passive_map import MapReadError, _u32


class EquipmentReader:
    def __init__(self, player, item_ids=(3051,)):
        if player.source.profile not in (UPDATED, NOVEMBER_26):raise MapReadError('Nieobsługiwany odczyt wyposażenia.')
        self.player=player;self.source=player.source;self.item_ids=frozenset(item_ids)
        self.layout = self.source.profile.player_layout
        self.local_vtable = next(rva for rva, kind in self.source.profile.creature_vtables if kind == 'self')

    def snapshot(self, *, open_windows=None):
        stats=self.player.snapshot()  # Revalidate local-player identity, health and session.
        if stats['hp']<=0:raise MapReadError('Postać nie jest żywa.')
        s=self.source;ptr=self.player.ptr;checks=[]
        def read(address,size):
            raw=s.read(address,size);checks.append((address,raw));return raw
        def item(address):
            if not address:return 0
            obj=read(address,0x3b)
            if _u32(obj)!=s.base+s.profile.item_vtable:raise MapReadError('Nieprawidłowy obiekt wyposażenia.')
            value=struct.unpack_from('<H',obj,0x35)[0]
            if not 100<=value<=65535:raise MapReadError('Nieprawidłowe ID wyposażenia.')
            return value
        inventory=read(ptr+self.layout.inventory,88)
        slots={i:item(_u32(inventory,i*8)) for i in range(1,11)}
        head,count=struct.unpack('<II',read(s.base+self.layout.game_singleton+0x2c,8))
        if count>32:raise MapReadError('Za dużo otwartych kontenerów.')
        todo=[_u32(read(head,28),4)];seen=set();sources=[];total=0
        windows = {}
        while todo:
            node=todo.pop();raw=read(node,28)
            if raw[13]:continue
            if node in seen or len(seen)>=count:raise MapReadError('Niespójna lista kontenerów.')
            seen.add(node);todo.extend((_u32(raw),_u32(raw,8)))
            cid,cptr=_u32(raw,16),_u32(raw,20)
            if not cptr:continue  # The client retains empty map entries after close.
            hdr=read(cptr,88)
            if cid>15 or _u32(hdr,0x10)!=cid:raise MapReadError('Nieprawidłowy numer kontenera.')
            blocks,capacity,offset,size=struct.unpack_from('<IIII',hdr,0x48)
            total+=size
            if size>255 or total>1024 or (size and (not 8<=capacity<=256 or capacity&(capacity-1))):
                raise MapReadError('Kontener przekracza sprawdzony zakres.')
            if open_windows is not None:
                windows[cid] = (cptr, size)
            for index in range(size):
                logical=offset+index
                block=_u32(read(blocks+((logical//2)&(capacity-1))*4,4))
                entry=block+(logical%2)*8
                iptr=_u32(read(entry,8));iid=item(iptr)
                if iid in self.item_ids:
                    sources.append({'container':cid,'index':index,'item_id':iid})
        if len(seen)!=count or any(s.read(address,len(raw))!=raw for address,raw in checks):
            raise MapReadError('Wyposażenie zmieniło się podczas odczytu.')
        # Recheck identity after the reads; never publish process addresses.
        identity=s.read(ptr,0x3c)
        if _u32(identity)!=s.base+self.local_vtable or _u32(identity,0x38)!=self.player.player_id:
            raise MapReadError('Zmieniła się postać.')
        if open_windows is not None:
            # Private same-window identity for revalidation. Never serialized in panel responses.
            open_windows.update(windows)
        return {'slots':slots,'sources':sources,'monotonic':time.monotonic(),'player_id':self.player.player_id}
