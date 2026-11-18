"""Haste renewal from fresh client conditions, with one draw per effect cycle."""
import asyncio
import math
import random
import time
from protocol import build_say_packet

NAME='speed_up'
# One passive measurement: 33.266 s, +/-470 speed; user may override.
DEFAULT_DURATION=33.3


def validate_config(value):
    if not isinstance(value,dict) or set(value)-{'enabled','duration','before','after'}:
        raise ValueError('Nieprawidłowe ustawienia Speed Up.')
    result={**value,'duration':value.get('duration',DEFAULT_DURATION),'before':value.get('before',0),'after':value.get('after',5)}
    for key,low,high in [('duration',2,3600),('before',0,3600),('after',0,3600)]:
        v=result[key]
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not low<=v<=high:
            raise ValueError('Podaj poprawne czasy Speed Up w sekundach.')
    if result['before']>result['duration']-2:
        raise ValueError('Odnowienie nie może wypadać wcześniej niż 2 s po poprzednim użyciu.')
    return result


def fresh(sample, now):
    return bool(sample and 0<=now-sample.get('monotonic',-100)<=.5
                and type(sample.get('conditions')) is int and sample.get('hp',0)>0)


class SpeedScheduler:
    def __init__(self, draw=random.uniform):
        self.draw=draw;self.begin()

    def begin(self):
        self.status='Czekam na odczyt efektu';self.previous=None;self.pending=None
        self.end=None;self.due=None;self.offset=None;self.last_ack=0;self.last_sent=-1e9
        self.attempts=0;self.confirmed=False;self.receipt=None

    def _cycle(self, started, cfg):
        self.offset=self.draw(-cfg['before'],cfg['after'])
        self.end=started+cfg['duration'];self.due=max(self.last_sent+2,self.end+self.offset)
        from command_delivery import confirm
        confirm(getattr(self,'receipt',None),'haste_observed')
        self.receipt=None
        self.pending=None;self.attempts=0;self.confirmed=True

    def inspect(self, sample, cfg, ack=0, now=None):
        now=time.monotonic() if now is None else now;cfg=validate_config(cfg)
        if not fresh(sample,now):
            if not sample or not 0<=now-sample.get('monotonic',-100)<=.5:
                self.status='Brak świeżych danych postaci · Speed Up czeka na odczyt klienta'
            elif sample.get('hp',0)<=0:
                self.status='Speed Up wstrzymany · HP wynosi 0'
            else:
                self.status='Brak odczytu efektu przyspieszenia'
            return False
        active=bool(sample['conditions']&2);newly_active=active and self.previous is False
        acknowledged=False
        if ack>self.last_ack:
            self.last_ack=ack
            if active and 0<=now-ack<cfg['duration']:
                self._cycle(ack,cfg);acknowledged=True
        if newly_active and not acknowledged:self._cycle(now,cfg)
        if self.previous is None and self.due is None:
            if active:self.status='Efekt już aktywny · czekam na jego koniec'
            else:self.due=now
        if self.previous is True and not active:
            # Prefer observed expiry to the estimate, retaining the same draw.
            if self.offset is None:self.offset=self.draw(-cfg['before'],cfg['after'])
            if self.pending is None:self.end=now;self.due=max(self.last_sent+2,now+max(0,self.offset))
        self.previous=active
        if self.pending is not None:
            if now-self.pending<3:self.status='Czekam na potwierdzenie Speed Up';return False
            self.pending=None
            # An unconfirmed renewal cannot cause repeated casts into an active effect.
            self.due=None if active else now+10
            self.status='Brak potwierdzenia · czekam na koniec efektu' if active else 'Brak potwierdzenia · ponowię za 10 s'
        if self.attempts>=3:self.status='Brak potwierdzenia po 3 próbach · włącz ponownie';return False
        if self.due is None:return False
        if now<self.due:self.status=f'Odnowienie za {max(0,self.due-now):.1f} s';return False
        self.status='Gotowy do odnowienia';return True

    def sent(self, now=None):
        now=time.monotonic() if now is None else now
        self.last_sent=now;self.pending=now;self.attempts+=1;self.status='Czekam na potwierdzenie Speed Up'

    def snapshot(self):
        now=time.monotonic()
        return {'status':self.status,'remaining':round(max(0,self.due-now),1) if self.due is not None else None,
                'offset':round(self.offset,1) if self.offset is not None else None,'effect_active':self.previous,
                'confirmed':self.confirmed,'attempts':self.attempts}


async def run_speed(bot):
    state=bot.state;gs=state.game_state;proxy=state.game_proxy;pid=bot.player_id
    scheduler=state.speed_scheduler;scheduler.begin();task=asyncio.current_task();alive=True
    try:
        while bot.is_connected and state.game_proxy is proxy and state.game_state is gs and bot.player_id==pid:
            cfg=validate_config(state.settings.get('actions',{}).get(NAME,{}))
            if not cfg.get('enabled') or state.automation_paused:break
            sample=getattr(gs,'client_snapshot',None)
            if scheduler.inspect(sample,cfg,getattr(gs,'haste_cast_at',0)):
                def guard():
                    return bool(alive and not task.cancelling() and not task.done() and not state.automation_paused
                                and state.game_proxy is proxy and proxy.logged_in and state.game_state is gs and bot.player_id==pid
                                and validate_config(state.settings.get('actions',{}).get(NAME,{}))==cfg
                                and fresh(getattr(gs,'client_snapshot',None),time.monotonic()))
                from command_delivery import delivery,attempted
                receipt=await delivery(await proxy.inject_to_server(build_say_packet('speed up'),guard=guard))
                if attempted(receipt):
                    scheduler.sent();scheduler.receipt=receipt
                    bot.log('Speed Up: przekazano próbę; oczekiwanie na potwierdzenie efektu.')
                else:
                    scheduler.status='Speed Up nie wysłano: '+str(receipt.reason)
                    scheduler.due=time.monotonic()+.5
            await bot.sleep(.1)
    finally:
        alive=False;scheduler.status='Wyłączony'
