"""Equip Barrier Band 3051 into an EMPTY slot; active form is 3088.

User-confirmed 15 minute duration is descriptive, never a renewal trigger.
Only bounded read-only inventory snapshots authorize a move from an open bag.
"""
import asyncio
import time
from protocol import build_move_item_packet

NAME='auto_barrier_band'
SLOTS={'band':9,'right_hand':5,'left_hand':6}
BAND_IDS={3051,3088}


def validate_config(config):
    if not isinstance(config,dict) or set(config)-{'enabled','slot'}:raise ValueError('Nieprawidłowe ustawienia Barrier Band.')
    slot=config.get('slot','band')
    if slot not in SLOTS:raise ValueError('Wybierz slot bandów lub ręki.')
    return {**config,'slot':slot}


class BandScheduler:
    def __init__(self):
        self.status='Wyłączony';self.pending=None;self.attempts=0;self.next_attempt=0
        self.empty_since=None;self.empty_stamp=None;self.equipped_id=None;self.stock=None

    def begin(self):
        self.pending=None;self.attempts=0;self.empty_since=None;self.empty_stamp=None
        self.status='Czekam na wyposażenie.';self.receipt=None

    def inspect(self,snapshot,config,now=None):
        now=time.monotonic() if now is None else now
        cfg=validate_config(config);slot=SLOTS[cfg['slot']]
        if not snapshot or not 0<=now-snapshot['monotonic']<=.5:
            self.status='Czekam na świeży odczyt wyposażenia.';self.empty_since=None;return None
        slots=snapshot['slots'];self.equipped_id=slots.get(slot);self.stock=len(snapshot['sources'])
        if self.equipped_id in BAND_IDS:
            from command_delivery import confirm
            confirm(getattr(self,"receipt",None),"band_equipped")
            self.status='Bariera aktywna' if self.equipped_id==3088 else 'Band założony · oczekiwanie na aktywację'
            self.pending=None;self.attempts=0;self.empty_since=None;return None
        if self.equipped_id != 0:
            self.status='Slot zajęty innym przedmiotem';self.empty_since=None;return None
        if any(slots.get(i) in BAND_IDS for i in SLOTS.values()):
            self.status='Band jest już założony w innym slocie';self.empty_since=None;return None
        if self.pending:
            if now-self.pending<2:self.status='Czekam na potwierdzenie założenia';return None
            self.pending=None;self.empty_since=None
        if self.attempts>=3:
            self.status='Brak potwierdzenia po 3 próbach · wyłącz i włącz, aby ponowić';return None
        if not snapshot['sources']:
            self.status='Brak 3051 w otwartych plecakach';self.empty_since=None;return None
        if self.empty_since is None:
            self.empty_since=now;self.empty_stamp=snapshot['monotonic'];return None
        if now-self.empty_since<.3 or snapshot['monotonic']<=self.empty_stamp or now<self.next_attempt:return None
        return {**snapshot['sources'][0],'slot':slot}

    def sent(self,now=None):
        now=time.monotonic() if now is None else now
        self.pending=now;self.attempts+=1;self.next_attempt=now+5;self.empty_since=None
        self.status='Czekam na potwierdzenie założenia'

    def snapshot(self):
        return {'status':self.status,'equipped_id':self.equipped_id,'stock':self.stock,'attempts':self.attempts}


def can_equip(snapshot,request,player_id,now=None):
    now=time.monotonic() if now is None else now
    return bool(snapshot and snapshot['player_id']==player_id and 0<=now-snapshot['monotonic']<=.5
                and snapshot['slots'].get(request['slot'])==0
                and not any(snapshot['slots'].get(i) in BAND_IDS for i in SLOTS.values())
                and {k:request[k] for k in ('container','index','item_id')} in snapshot['sources']
                and request['item_id']==3051)


async def run_band(bot):
    from passive_map_win32 import ReadOnlyClient
    from client_player_state import ClientPlayerReader
    from client_equipment import EquipmentReader
    state=bot.state;scheduler=state.band_scheduler;scheduler.begin()
    source=None;alive=True;task=asyncio.current_task();player_id=bot.player_id;proxy=state.game_proxy
    try:
        source=ReadOnlyClient();reader=EquipmentReader(ClientPlayerReader(source,player_id))
        while bot.is_connected and state.game_proxy is proxy and bot.player_id==player_id:
            cfg=validate_config(state.settings.get('actions',{}).get(NAME,{}))
            if not cfg.get('enabled') or state.automation_paused:break
            try:sample=await asyncio.to_thread(reader.snapshot)
            except Exception:sample=None
            request=scheduler.inspect(sample,cfg)
            if request and can_equip(sample,request,player_id):
                def guard():
                    if (not alive or task.cancelling() or task.done() or state.automation_paused
                            or state.game_proxy is not proxy or not proxy.logged_in or bot.player_id!=player_id
                            or validate_config(state.settings.get('actions',{}).get(NAME,{}))!=cfg):return False
                    try:return can_equip(reader.snapshot(),request,player_id)
                    except Exception:return False
                packet=build_move_item_packet((65535,0x40|request['container'],request['index']),3051,
                                              request['index'],(65535,request['slot'],0),1)
                from command_delivery import delivery,attempted
                receipt=await delivery(await proxy.inject_to_server(packet,guard=guard))
                if attempted(receipt):scheduler.sent();scheduler.receipt=receipt
                else:scheduler.status='Nie wysłano założenia banda: '+str(receipt.reason)
            await bot.sleep(.25)
    finally:
        alive=False
        if source:source.close()
        scheduler.status='Wyłączony'
