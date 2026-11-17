"""Passive, bounded in-memory alerts. Never sends game commands or logs chat."""
import asyncio
import copy
import threading
import time
import uuid
import json
from pathlib import Path
from collections import deque

KINDS = ('private', 'default', 'player')


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
    def __init__(self, config=None, clock=time.time, spells=None):
        self.clock = clock
        self.lock = threading.RLock()
        try:
            self.config = validate_config(config)
        except ValueError:
            self.config = default_config()
        self.session = uuid.uuid4().hex
        self.events = deque(maxlen=100)
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
            self.config = validate_config(config)

    def acknowledge(self, through):
        with self.lock:
            if type(through) is not int or not 0 <= through <= self.latest_id:
                raise ValueError('Nieprawidłowy numer alertu.')
            self.read_id = max(self.read_id, through)

    def snapshot(self):
        with self.lock:
            return {'session': self.session, 'config': copy.deepcopy(self.config),
                    'events': copy.deepcopy(list(self.events)), 'latest_id': self.latest_id,
                    'unread': sum(e['id'] > self.read_id for e in self.events),
                    'cavebot_active': self.active, 'map_fresh': self.map_fresh,
                    'chat_received': self.chat_received, 'last_chat_at': self.last_chat_at}

    def _emit(self, kind, sender, text, active, **details):
        row = self.config[kind]
        if not row['enabled'] or (row['scope'] == 'cavebot' and not active):
            return
        now = self.clock()
        sound = row['sound'] and now - self.last_sound >= 3
        if sound:
            self.last_sound = now
        self.latest_id += 1
        self.events.append({'id': self.latest_id, 'kind': kind, 'at': now,
                            'sender': sender[:64], 'text': text[:512], 'sound': sound, **details})

    def _context(self, state):
        gs = state.game_state
        # BotState.connected alone also covers the login/character-list proxy.
        online = bool(state.connected and gs.player_id)
        connected = bool(online and gs.stats_updated_at and self.clock() - gs.stats_updated_at < 2)
        identity = (id(gs), gs.player_id, id(getattr(state, 'game_proxy', None))) if online else None
        if identity != self.identity:
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

    def observe_players(self, state):
        with self.lock:
            if not self._context(state):
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
    while True:
        try:
            state.alert_center.observe_players(state)
        except Exception:
            # Optional notification failure must never terminate healing/combat.
            state.alert_center.map_fresh = False
        await asyncio.sleep(.25)
