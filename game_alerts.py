"""Passive, bounded in-memory alerts. Never sends game commands or logs chat."""
import asyncio
import copy
import logging
import threading
import time
import uuid
from client_player_state import FRESH_SECONDS
from ki_loss import KI_DROP_WINDOW, KI_HIT_GRACE, KiDropDetector
import json
from pathlib import Path
from collections import deque

KINDS = ('private', 'default', 'player', 'ki_loss')
KI_ALARM_DURATION = 10


def default_config():
    return {kind: {'enabled': True, 'sound': True, 'scope': 'always'} for kind in KINDS}


def spell_words():
    try:
        path = Path(__file__).parent/'dashboard/public/local-spells/catalog.json'
        rows = json.loads(path.read_text(encoding='utf-8'))['spells']
        return {str(row[key]).strip().rstrip('!').casefold() for row in rows for key in ('name', 'words')}
    except (OSError, ValueError, KeyError, TypeError):
        return {'shunkan', 'shinretsuha'}


def validate_config(value):
    if isinstance(value, dict) and set(value) == {'private', 'default', 'player'}:
        value = {**value, 'ki_loss': default_config()['ki_loss']}
    if not isinstance(value, dict) or set(value) != set(KINDS):
        raise ValueError('Nieprawidłowa konfiguracja alertów.')
    for row in value.values():
        if (not isinstance(row, dict) or set(row) != {'enabled', 'sound', 'scope'}
                or type(row['enabled']) is not bool or type(row['sound']) is not bool
                or row['scope'] not in ('always', 'cavebot')):
            raise ValueError('Nieprawidłowe ustawienie alertu.')
    return copy.deepcopy(value)


def cavebot_running(state):
    if getattr(state, 'automation_paused', False):
        return False
    controller = getattr(state, 'adaptive_cavebot', None)
    task = getattr(state, '_action_tasks', {}).get('adaptive_cavebot')
    # Includes the combat-only mode of V2; a navigation hold is still running.
    return bool(controller and controller.status == 'running' and task and not task.done())


class AlertCenter:
    def __init__(self, config=None, clock=time.time, spells=None, monotonic=time.monotonic):
        self.monotonic = monotonic
        self.ki_detector = KiDropDetector()
        self.ki_alarm_id = None
        self.ki_alarm_until = 0.
        self.pending_ki = None
        self.last_vitals = None
        self.vitals_alarm_id = None
        self.clock = clock
        self.lock = threading.RLock()
        try:
            self.config = validate_config(config)
        except ValueError:
            self.config = default_config()
        self.session = uuid.uuid4().hex
        self.events = deque(maxlen=100)
        self.paused = True
        self.silenced_through = 0
        self.latest_id = self.read_id = 0
        self.last_sound = float('-inf')
        self.seen_messages = {}
        self.players = {}
        self.known_players = {}
        self.identity = None
        self.chat_received = 0
        self.last_chat_at = None
        self.active = False
        self.map_fresh = False
        self.spells = spell_words() if spells is None else set(spells)

    def configure(self, config):
        with self.lock:
            config = validate_config(config)
            if config['ki_loss'] != self.config['ki_loss']:
                self.ki_detector.high = self.pending_ki = self.ki_alarm_id = None
            self.config = config

    def set_paused(self, paused):
        if type(paused) is not bool:
            raise ValueError('Nieprawidłowy stan pauzy alertów.')
        with self.lock:
            if self.paused != paused:
                self.silenced_through = self.latest_id
                self.pending_ki = None
                self.paused = paused

    def acknowledge(self, through):
        with self.lock:
            if type(through) is not int or not 0 <= through <= self.latest_id:
                raise ValueError('Nieprawidłowy numer alertu.')
            self.read_id = max(self.read_id, through)

    def snapshot(self):
        with self.lock:
            return {'session': self.session, 'config': copy.deepcopy(self.config),
                    'events': copy.deepcopy(list(self.events)), 'latest_id': self.latest_id,
                    'vitals_alarm_id': self.vitals_alarm_id,
                    'ki_alarm_id': self.ki_alarm_id if self.monotonic() < self.ki_alarm_until else None,
                    'paused': self.paused, 'silenced_through': self.silenced_through,
                    'unread': sum(e['id'] > max(self.read_id, self.silenced_through) for e in self.events),
                    'cavebot_active': self.active, 'map_fresh': self.map_fresh,
                    'chat_received': self.chat_received, 'last_chat_at': self.last_chat_at}

    def _emit(self, kind, sender, text, active, **details):
        row = self.config[kind]
        if not row['enabled'] or (row['scope'] == 'cavebot' and not active):
            return
        now = self.clock()
        sound = not self.paused and row['sound'] and now - self.last_sound >= 3
        if sound:
            self.last_sound = now
        self._append(kind, sender, text, sound, **details)

    def _append(self, kind, sender, text, sound, **details):
        now = self.clock()
        self.latest_id += 1
        if self.paused:
            self.silenced_through = self.latest_id
            sound = False
        self.events.append({'id': self.latest_id, 'kind': kind, 'at': now,
                            'sender': sender[:64], 'text': text[:512], 'sound': sound, **details})

    def _context(self, state):
        gs = state.game_state
        # BotState.connected alone also covers the login/character-list proxy.
        online = bool(state.connected and gs.player_id)
        connected = bool(online and gs.stats_updated_at and self.clock() - gs.stats_updated_at < 2)
        identity = (id(gs), gs.player_id, id(getattr(state, 'game_proxy', None))) if online else None
        if identity != self.identity:
            self.ki_detector.high = self.pending_ki = self.ki_alarm_id = None
            self.last_vitals = self.vitals_alarm_id = None
            self.players.clear()
            self.known_players.clear()
            self.seen_messages.clear()
            self.identity = identity
        self.active = connected and cavebot_running(state)
        return connected

    def observe_chat(self, message, state, source):
        with self.lock:
            if source is not state.game_state or not self._context(state):
                return
            now = self.clock()
            self.chat_received += 1
            self.last_chat_at = now
            name = message['sender'].strip()
            me = getattr(source, 'player_name', '').strip()
            # Wait for authoritative own identity instead of alerting on own casts.
            if not me or name.casefold() == me.casefold():
                return
            kind = message['kind']
            if kind == 'default':
                if message['text'].strip().rstrip('!').casefold() in self.spells:
                    return
                # Nearby human identity must come from a typed live map creature.
                if not any(v['name'].casefold() == name.casefold() and now - v['at'] < 30
                           for v in self.known_players.values()):
                    return
            self.seen_messages = {k: t for k, t in self.seen_messages.items() if now - t < 5}
            key = (message['statement'], kind, name, message['text'])
            if key in self.seen_messages:
                return
            if len(self.seen_messages) >= 256:
                self.seen_messages.pop(next(iter(self.seen_messages)))
            self.seen_messages[key] = now
            self._emit(kind, name, message['text'], self.active)

    def _watch_vitals(self, state):
        gs = state.game_state
        potion_task = getattr(state, '_action_tasks', {}).get('auto_potions')
        potions = potion_task is not None and not potion_task.done()
        if (self.identity is None or getattr(state, 'automation_paused', False)
                or not getattr(gs, 'client_state_required', False)
                or not (potions or cavebot_running(state))):
            self.last_vitals = self.vitals_alarm_id = None
            return
        now = self.monotonic()
        sample = gs.client_snapshot or {}
        if (0 <= now - sample.get('monotonic', float('-inf')) <= FRESH_SECONDS
                and sample.get('max_hp', 0) > 0 and 0 <= sample.get('hp', -1) <= sample['max_hp']):
            self.last_vitals = (sample['monotonic'], sample['hp'], sample['max_hp']) if sample['hp'] else None
            self.vitals_alarm_id = None
        elif self.last_vitals and self.vitals_alarm_id is None and now - self.last_vitals[0] >= 1.5:
            stamp, hp, maximum = self.last_vitals
            text = f'Ostatni odczyt: {hp}/{maximum} HP. Automatyczne leczenie czeka na aktualne HP.'
            self._append('vitals', 'Brak aktualnego HP', text, True)
            self.vitals_alarm_id = self.latest_id
            logging.getLogger(__name__).warning('Vitals unavailable: age_ms=%d last_hp=%s/%s source_error=%s',
                                                round((now-stamp)*1000), hp, maximum,
                                                getattr(gs, 'client_state_error', None))

    def observe_ki(self, state):
        with self.lock:
            connected = self._context(state)
            gs = state.game_state
            cfg = self.config['ki_loss']
            now = self.clock()
            stamp, mana, maximum = gs.stats_updated_at, gs.mana, gs.max_mana
            position = tuple(gs.position)
            if (not connected or not cfg['enabled'] or (cfg['scope'] == 'cavebot' and not self.active)
                    or not 0 <= now - stamp <= KI_DROP_WINDOW or gs.hp <= 0
                    or maximum <= 0 or not 0 <= mana <= maximum or len(position) != 3):
                self.ki_detector.high = self.pending_ki = None
                return
            current = self.monotonic()
            last_hit = gs.last_incoming_hit
            if self.pending_ki:
                drop, text, location = self.pending_ki
                if last_hit is not None and last_hit >= drop - KI_DROP_WINDOW:
                    self.pending_ki = None
                elif current - drop >= KI_HIT_GRACE:
                    self._append('ki_loss', 'Nagła utrata Ki', text, cfg['sound'], position=list(location))
                    self.ki_alarm_id = self.latest_id
                    self.ki_alarm_until = current + KI_ALARM_DURATION
                    self.pending_ki = None

            drop = self.ki_detector.observe(gs, current, now)
            if drop is None or (last_hit is not None and last_hit >= drop.at - KI_DROP_WINDOW):
                return
            text = (f'W jednym odczytanym spadku Ki zmalało z {drop.before:.0%} do {drop.after:.0%} '
                    f'w {drop.elapsed:.2f} s. Brak komunikatu o ataku na postać.')
            if self.pending_ki is None:
                self.pending_ki = (drop.at, text, position)

    def observe_players(self, state):
        with self.lock:
            connected = self._context(state)
            self._watch_vitals(state)
            if not connected:
                self.map_fresh = False
                return
            monitor = getattr(state, 'passive_map_monitor', None)
            # Read the immutable published map result; no extra process reads or
            # atlas/learning serialization on the healing/proxy event loop.
            result = getattr(monitor, '_result', {})
            now = self.clock()
            self.map_fresh = result.get('status') == 'ready' and now-result.get('captured_at', 0) < 2
            if not self.map_fresh:
                return  # Map churn must not produce exit/re-entry alerts.
            gs = state.game_state
            position = tuple(gs.position)
            if tuple(result.get('center', ())) != position:
                return
            creatures = dict(gs.creatures)
            visible = set()
            for cell in result.get('cells', []):
                if cell['z'] != position[2]:
                    continue
                for occupant in cell.get('alert_players', []):
                    cid = occupant['id']
                    if cid == gs.player_id:
                        continue
                    visible.add(cid)
                    info = creatures.get(cid, {})
                    name = info.get('name', '')
                    previous = self.players.get(cid)
                    if name:
                        self.known_players[cid] = {'name': name, 'at': now}
                    if previous is None or now-previous >= 15:
                        self._emit('player', name or 'Gracz', 'Pojawił się w zasięgu mapy.', self.active,
                                   position=[cell['x'], cell['y'], cell['z']])
                    self.players[cid] = now
            self.players = {cid: t for cid, t in self.players.items() if now-t < 60}
            self.known_players = {cid: v for cid, v in self.known_players.items() if now-v['at'] < 30}


async def monitor_alerts(state):
    next_players = 0.
    while True:
        journal = getattr(state.game_state, 'observations', None)
        cursor = journal.revision_for(('health',)) if journal else 0
        try:
            now = time.monotonic()
            if now >= next_players:
                next_players = now + .25
                state.alert_center.observe_players(state)
            state.alert_center.observe_ki(state)
        except Exception:
            # Optional notification failure must never terminate healing/combat.
            state.alert_center.map_fresh = False
        timeout = max(0., next_players - time.monotonic())
        if journal:
            await journal.wait(('health',), cursor, timeout)
        else:
            await asyncio.sleep(timeout)
