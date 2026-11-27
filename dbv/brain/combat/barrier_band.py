"""Equip Barrier Band 3051 into an EMPTY slot; active form is 3088.

User-confirmed 15 minute duration is descriptive, never a renewal trigger.
Only bounded read-only inventory snapshots authorize a move from an open bag.
"""
import asyncio
import time
from dbv.brain.state.item_observations import observed_inputs, wait_for_inputs
from dbv.client_bridge.protocol import build_move_item_packet

NAME='auto_barrier_band'
SLOTS={'band':9,'right_hand':5,'left_hand':6}
BAND_IDS={3051,3088}
INPUTS=('inventory',)


def validate_config(config):
    if not isinstance(config,dict) or set(config)-{'enabled','slot'}:raise ValueError('Nieprawidłowe ustawienia Barrier Band.')
    slot=config.get('slot','band')
    if slot not in SLOTS:raise ValueError('Wybierz slot bandów lub ręki.')
    return {**config,'slot':slot}


class BandScheduler:
    def __init__(self):
        self.status='Wyłączony';self.pending=None;self.attempts=0;self.next_attempt=0
        self.empty_since=None;self.empty_stamp=None;self.equipped_id=None;self.stock=None
        self.pending_slot=None;self.receipt=None

    def begin(self):
        self.pending=None;self.attempts=0;self.empty_since=None;self.empty_stamp=None
        self.status='Czekam na wyposażenie.';self.receipt=None;self.pending_slot=None
        self.equipped_id=None;self.stock=None

    def inspect(self,snapshot,config,now=None):
        now=time.monotonic() if now is None else now
        cfg=validate_config(config);slot=SLOTS[cfg['slot']]
        if not snapshot or not 0<=now-snapshot['monotonic']<=.5:
            self.status='Czekam na świeży odczyt wyposażenia.';self.empty_since=None;return None
        slots=snapshot['slots'];self.equipped_id=slots.get(slot);self.stock=len(snapshot['sources'])
        if self.equipped_id in BAND_IDS:
            from dbv.client_bridge.command_delivery import confirm
            if (self.pending is not None and self.pending_slot == slot
                    and 0 <= snapshot['monotonic'] - self.pending <= 2):
                confirm(self.receipt, "band_equipped")
            self.receipt=None;self.pending_slot=None
            self.status='Bariera aktywna' if self.equipped_id==3088 else 'Band założony · oczekiwanie na aktywację'
            self.pending=None;self.attempts=0;self.empty_since=None;return None
        if self.equipped_id != 0:
            if self.equipped_id is not None and self.pending is not None and snapshot['monotonic'] >= self.pending:
                self.pending=None;self.pending_slot=None;self.receipt=None
            self.status='Slot zajęty innym przedmiotem';self.empty_since=None;return None
        if any(slots.get(i) in BAND_IDS for i in SLOTS.values()):
            self.status='Band jest już założony w innym slocie';self.empty_since=None;return None
        if self.pending is not None:
            if now-self.pending<2:self.status='Czekam na potwierdzenie założenia';return None
            if getattr(self.receipt, 'status', None) == 'unknown':
                self.status='Niepewny wynik założenia · czekam na zmianę wyposażenia'
                return None
            self.pending=None;self.receipt=None;self.pending_slot=None;self.empty_since=None
        if self.attempts>=3:
            self.status='Brak potwierdzenia po 3 próbach · wyłącz i włącz, aby ponowić';return None
        if not snapshot['sources']:
            self.status='Brak 3051 w otwartych plecakach';self.empty_since=None;return None
        if self.empty_since is None:
            self.empty_since=now;self.empty_stamp=snapshot['monotonic'];return None
        if now-self.empty_since<.3 or snapshot['monotonic']<=self.empty_stamp or now<self.next_attempt:return None
        return {**snapshot['sources'][0],'slot':slot}

    def sent(self,now=None,*,slot=None):
        now=time.monotonic() if now is None else now
        self.pending=now;self.pending_slot=slot;self.attempts+=1;self.next_attempt=now+5;self.empty_since=None
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
    from dbv.client_bridge.passive_map_win32 import ReadOnlyClient
    from dbv.client_bridge.client_player_state import ClientPlayerReader
    from dbv.client_bridge.client_equipment import EquipmentReader
    state=bot.state;scheduler=state.band_scheduler;scheduler.begin()
    source=None;alive=True;task=asyncio.current_task();player_id=bot.player_id;proxy=state.game_proxy
    game=getattr(state,'game_state',None);generation=getattr(proxy,'session_generation',None)
    def same_session():
        return (state.game_proxy is proxy and bot.player_id==player_id
                and getattr(state,'game_state',None) is game and getattr(proxy,'session_generation',None)==generation)
    try:
        source=ReadOnlyClient();reader=EquipmentReader(ClientPlayerReader(source,player_id))
        while bot.is_connected and same_session():
            cursor=observed_inputs(game,INPUTS)
            cfg=validate_config(state.settings.get('actions',{}).get(NAME,{}))
            if not cfg.get('enabled') or state.automation_paused:break
            try:sample=await asyncio.to_thread(reader.snapshot)
            except Exception:sample=None
            if not same_session():break
            request=scheduler.inspect(sample,cfg)
            if request and can_equip(sample,request,player_id):
                def guard():
                    if (not alive or task.cancelling() or task.done() or state.automation_paused
                            or not same_session() or not proxy.logged_in
                            or validate_config(state.settings.get('actions',{}).get(NAME,{}))!=cfg):return False
                    try:return can_equip(reader.snapshot(),request,player_id)
                    except Exception:return False
                packet=build_move_item_packet((65535,0x40|request['container'],request['index']),3051,
                                              request['index'],(65535,request['slot'],0),1)
                from dbv.client_bridge.command_delivery import delivery,attempted
                receipt=await delivery(await proxy.inject_to_server(packet,guard=guard))
                if attempted(receipt):scheduler.sent(slot=request['slot']);scheduler.receipt=receipt
                else:scheduler.status='Nie wysłano założenia banda: '+str(receipt.reason)
            await wait_for_inputs(bot,INPUTS,cursor,.25)
    finally:
        alive=False
        if source:source.close()
        scheduler.status='Wyłączony'
