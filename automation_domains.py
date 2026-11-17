"""Independent combat evaluation and one movement executor per game session.

Healing remains its own action task. Neither worker owns or cancels that task.
Combat publishes movement intents; only navigation can execute a walk/tool use.
"""
import asyncio
import logging
import time

log = logging.getLogger(__name__)


class CavebotDomains:
    def __init__(self, owner):
        self.owner = owner
        self.movement_state = 'idle'
        self.combat_state = 'idle'
        self.movement_error = None
        self.combat_error = None
        self.decision = None
        self.tasks = []

    def snapshot(self):
        return dict(movement=dict(status=self.movement_state,reason=self.movement_error),
                    combat=dict(status=self.combat_state,reason=self.combat_error))

    def signature(self):
        o = self.owner
        return (tuple(o.state.game_state.position),o.index,o.generation,o.motion_generation)

    def movement_allowed(self):
        o = self.owner
        return (not o.combat_only and not o.navigation_hold
                and self.movement_state != 'complete' and time.monotonic() >= o.manual_until)

    async def combat_loop(self):
        o = self.owner
        o.combat.defer_movement = True
        while o.status == 'running':
            self.decision = None
            o.combat.movement_intent = None
            if not o.combat.config['enabled']:
                self.combat_state = 'disabled'
                await asyncio.sleep(.05)
                continue
            try:
                o.guard(combat=True)
                sample = o.map_now()
                signature = self.signature()
                boundary = bool(not o.combat_only and o.path and o.route_boundary(sample))
                consumed = await o.combat.tick(sample,boundary,allow_movement=self.movement_allowed())
                self.combat_state = 'running'
                self.combat_error = None
                if signature == self.signature():
                    self.decision = dict(signature=signature,at=time.monotonic(),consumed=consumed,
                                         intent=o.combat.movement_intent)
            except ValueError as exc:
                self.combat_state = 'waiting'
                self.combat_error = str(exc)
            except Exception:
                # A programming error is visible and contained, not retried blindly.
                self.combat_state = 'fault'
                self.combat_error = 'Błąd walki. Zatrzymaj i uruchom automat ponownie; leczenie działa niezależnie.'
                log.exception('Combat worker failed')
                return
            await asyncio.sleep(.05)

    async def consume_movement(self,sample,boundary):
        """Called only by the movement worker. True means no route step this tick."""
        o = self.owner
        if not o.combat.config['enabled']:
            self.movement_state = 'running'
            return False
        decision = self.decision
        if (not decision or decision['signature'] != self.signature()
                or time.monotonic()-decision['at'] > .3):
            self.movement_state = 'waiting'
            if self.combat_error:o.reason=self.combat_error
            await asyncio.sleep(.01)
            return True
        intent = decision['intent']
        if intent:
            self.decision = None  # consume at most once; a new observation replans
            if (self.movement_allowed() and time.monotonic()<intent['expires']
                    and list(o.state.game_state.position)==intent['origin']):
                self.movement_state = 'combat_movement'
                validate = intent['validate']
                await o.step(intent['target'],validate=lambda:
                    self.movement_allowed() and (validate is None or validate()))
            return True
        if decision['consumed'] or not o.combat.route_step_allowed(boundary):
            self.movement_state = 'waiting_for_combat'
            await asyncio.sleep(.01)
            return True
        self.movement_state = 'running'
        return False

    async def movement_loop(self):
        from adaptive_cavebot import InputsUnavailable,MovementDeferred
        o = self.owner
        while o.status == 'running':
            try:
                await o._navigation_loop()
            except (InputsUnavailable,MovementDeferred) as exc:
                self.movement_state='waiting';self.movement_error=str(exc)
                o.reason=str(exc)
                await asyncio.sleep(.05)
            except Exception as exc:
                if not isinstance(exc,ValueError):log.exception('Movement worker failed')
                self.movement_state='held';self.movement_error=str(exc) if isinstance(exc,ValueError) else 'Błąd wykonania trasy.'
                if not o.combat.config['enabled']:
                    o.pause(self.movement_error)
                    return
                o.navigation_hold=self.movement_error
                o.logs.append('Ruch: '+self.movement_error)
                while o.navigation_hold and o.status=='running':
                    o.reason='Ruch wstrzymany: '+o.navigation_hold+' Walka działa niezależnie.'
                    await asyncio.sleep(.05)
                self.movement_error=None

    async def run(self):
        o=self.owner
        self.tasks=[asyncio.create_task(self.combat_loop(),name='cavebot-combat'),
                    asyncio.create_task(self.movement_loop(),name='cavebot-movement')]
        try:
            while o.status=='running':
                if not o.session_lease.valid() or o.state.automation_paused:
                    o.pause('Sesja zakończona lub automatyka wstrzymana.')
                    break
                if o.state.game_state.hp<=0:
                    o.pause('Brak żywej postaci — automatyka wstrzymana.')
                    break
                await asyncio.sleep(.02)
        finally:
            for task in self.tasks:task.cancel()
            await asyncio.gather(*self.tasks,return_exceptions=True)
            o.combat.defer_movement=False
            self.decision=None
            if self.movement_state not in {'complete','held'}:self.movement_state='stopped'
            if self.combat_state!='fault':self.combat_state='stopped'
