"""The bot's MCP server: game commands, actions and the panel, over stdio.

stdout carries only MCP JSON-RPC, so every log line goes to stderr. The
same process runs the login and game proxies, the action scripts in
``actions/`` and the panel's HTTP API.
"""
import asyncio
import atexit
import collections
from datetime import datetime
import importlib
import importlib.util
import json
import logging
import os
import subprocess
import sys
import time as _time
import traceback
from pathlib import Path

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("mcp_server")


# The install root, so dbv and actions/ import when this file is started directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dbv import _legacy, paths
from dbv.paths import INSTALL_ROOT

# Actions load in this process; old flat names in them must resolve even when an
# earlier copy of the dbv package (from before an update) is already imported.
_legacy.install()
from dbv_revision import checkout_revision, revision_label, version_for
from dbv.brain.state.session_startup import LoginStartup
from dbv.brain.state.session_observations import session_observations, note_observation_gap, publish_semantic_result


def _engine_label(root, revision=None):
    started = datetime.now().astimezone().isoformat(sep=" ", timespec="seconds")
    if revision is None:
        revision = checkout_revision(root)
    label = revision_label(revision, version_for(root, revision))
    return f"Silnik {label} · start {started}"


# Capture once, before loading the engine. Reopening the panel reuses this identity.
ENGINE_ROOT = INSTALL_ROOT
ENGINE_REVISION = checkout_revision(ENGINE_ROOT)
ENGINE_LABEL = _engine_label(ENGINE_ROOT, ENGINE_REVISION)

from mcp.server.fastmcp import FastMCP
from dbv.mcp_server.tray_exit import EXIT_FILE_ENV, tray_input

from dbv.client_bridge.proxy import OTProxy
from dbv.brain.state.game_state import GameState, parse_server_packet, scan_packet
from dbv.mcp_server.transport_control import LocalTransportControl
from dbv.client_bridge import protocol as wire
from dbv.client_bridge.protocol import ClientOpcode, Direction
from dbv.client_bridge.constants import SERVER_HOST, LOGIN_PORT, GAME_PORT
from dbv.brain.cavebot import cavebot
from dbv.brain.combat.potions import PotionScheduler, PotionDiagnostics, ITEMS as HEALING_ITEMS, validate_config as validate_potion_config
from dbv.brain.combat.barrier_spell import validate_config as validate_barrier_config
from dbv.brain.combat.barrier_band import BandScheduler, validate_config as validate_band_config
from dbv.mcp_server.dashboard_security import SESSION_TOKEN

# ── settings ────────────────────────────────────────────────────────
SETTINGS_FILE = paths.SETTINGS_FILE
ACTIONS_DIR = paths.ACTIONS_DIR
INTERNAL_ACTIONS = {"dll_bridge"}   # always running while logged in, even when paused


def load_settings() -> dict:
    """settings.json, or empty defaults when it is missing or unreadable."""
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Failed to load settings: %s", exc)
    return {"actions": {}}


def save_settings(settings: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


# ── process-wide state ──────────────────────────────────────────────

MAX_ACTION_LOGS = 200   # lines kept per action for the panel


class BotState:
    """Everything this process knows: proxies, the game session, actions and their settings."""

    def __init__(self):
        self.local_transport = LocalTransportControl()
        self.login_proxy = self.game_proxy = None
        self.packet_observer = None
        from dbv.brain.state.session_journal import SessionJournal
        self.packet_journal = SessionJournal()
        self.observer_bridge = None
        self.ready = False           # a character is logged in through the game proxy
        self._auto_task = None
        self.login_startup = LoginStartup()
        self._proxy_tasks = []
        self.settings = load_settings()
        self.automation_paused = bool(self.settings.get("automation_paused", False))
        self.automation_pause_kind = self.settings.get("automation_pause_kind", "emergency") if self.automation_paused else None
        self._stopping_tasks = []
        self._emergency_stopping = False
        self.band_scheduler = BandScheduler()
        self.barrier_scheduler = None
        from dbv.brain.combat.speed_up import SpeedScheduler
        self.speed_scheduler = SpeedScheduler()
        self.potion_scheduler = PotionScheduler()
        self.potion_diagnostics = PotionDiagnostics()
        from dbv.brain.combat.hunter_knife import CorpseSuggestions
        self.knife_suggestions = CorpseSuggestions()
        from dbv.brain.state.item_identification import ItemIdentification
        self.item_identification = ItemIdentification(self.settings.get("item_identification", True) is not False)
        self._action_tasks = {}         # name -> asyncio.Task
        self._action_completed = set()  # actions whose run() returned normally
        self._adaptive_prepare_token = None
        self.game_state = GameState()
        from dbv.brain.state.game_alerts import AlertCenter
        self.alert_center = AlertCenter(self.settings.get("alerts"))
        self.alert_center.set_automation_paused(self.automation_paused)
        from dbv.brain.profiles import ProfileStore
        self.profile_store = ProfileStore()
        self.action_logs = {}           # name -> deque of "[HH:MM:SS] text"
        # Route recording (dbv.brain.cavebot.cavebot keeps the rest).
        self.recording_active, self.recording_name = False, ""
        self.recording_waypoints = []
        self.recording_start_pos, self.recording_start_time = (0, 0, 0), 0
        self._recording_callback = None

    @property
    def connected(self) -> bool:
        return bool(self.ready) and self.game_proxy is not None


state = BotState()


async def _async_toggle_all():
    if state._emergency_stopping:
        return "Trwa zatrzymywanie akcji."
    if state.automation_paused:
        return await _async_resume_automation()
    return await _async_pause_automation()


async def _async_profiles(body):
    from dbv.brain.profiles import bot_config, validate_bot, applied_bot_settings, require_stopped
    from dbv.brain.cavebot.cavebot_combat import validate_combat
    kind=body.get('kind');command=body.get('command');ident=body.get('id')
    if kind not in {'bot','combat'}:raise ValueError('Nieznany typ profilu.')
    store=state.profile_store
    if command=='bind':
        if kind!='combat':raise ValueError('Do trasy przypisujemy profil walki.')
        scope=body.get('scope');draft_id=body.get('draft_id')
        atlas=getattr(getattr(state,'passive_map_monitor',None),'atlas',None)
        if not atlas or atlas.scope!=scope or not any(d['id']==draft_id for d in atlas.drafts()):
            raise ValueError('Brak trasy w bieżącym atlasie.')
        if ident is not None:store.get(ident,'combat')
        links=dict(state.settings.get('route_combat_profiles',{}));key=f'{scope}:{draft_id}'
        if ident is None:links.pop(key,None)
        else:links[key]=ident
        candidate={**state.settings,'route_combat_profiles':links};save_settings(candidate);state.settings=candidate
        return {'bound':ident}
    if command=='save':
        cfg=body.get('config') if kind=='combat' else bot_config(state.settings)
        row=store.save(kind,body.get('name'),cfg,ident)
        return row
    if command=='delete':
        store.delete(ident,kind)
        state.settings['route_combat_profiles']={k:v for k,v in state.settings.get('route_combat_profiles',{}).items() if v!=ident}
        save_settings(state.settings)
        key='selected_'+kind+'_profile'
        if state.settings.get(key)==ident:state.settings.pop(key,None);save_settings(state.settings)
        return {'deleted':True}
    if command!='apply':raise ValueError('Nieznane polecenie profilu.')
    require_stopped(state,kind);row=store.get(ident,kind)
    if kind=='combat':
        cfg=validate_combat(row['config']);candidate={**state.settings,'combat_profile':cfg,'selected_combat_profile':ident}
    else:
        candidate=applied_bot_settings(state.settings,row['config']);candidate['selected_bot_profile']=ident
    save_settings(candidate);state.settings=candidate
    if kind=='bot':state.alert_center.configure(candidate['alerts'])
    return {'config':candidate['combat_profile'] if kind=='combat' else validate_bot(row['config'])}


async def _async_alerts(command, body):
    from dbv.brain.state.game_alerts import validate_config
    if command == "configure":
        config = validate_config(body.get("config"))
        candidate = {**state.settings, "alerts": config}
        save_settings(candidate)
        state.settings["alerts"] = config
        state.alert_center.configure(config)
    elif command == "set_paused":
        state.alert_center.set_paused(body.get("paused"))
        return "Alerty wstrzymane." if state.alert_center.paused else "Alerty wznowione."
    elif command == "acknowledge":
        state.alert_center.acknowledge(body.get("through"))
    else:
        raise ValueError("Nieznana operacja alertów.")
    return "Ustawienia alertów zapisane." if command == "configure" else "Alerty przeczytane."


# ── the `bot` object given to action scripts ────────────────────────

def _game_value(name, doc):
    return property(lambda self: getattr(state.game_state, name), doc=doc)


class BotContext:
    """What an action's ``run(bot)`` can see and do. Values are read from the current session."""

    def __init__(self, action_name: str = ""):
        self._log = logging.getLogger("action")
        self._action_name = action_name
        from dbv.brain.state.session_lifetime import SessionLease
        self._session = SessionLease(state)

    @property
    def is_connected(self) -> bool:
        return state.connected

    hp = _game_value("hp", "Current HP.")
    max_hp = _game_value("max_hp", "Maximum HP.")
    mana = _game_value("mana", "Current Ki.")
    max_mana = _game_value("max_mana", "Maximum Ki.")
    level = _game_value("level", "Character level.")
    experience = _game_value("experience", "Experience points.")
    capacity = _game_value("capacity", "Free capacity.")
    speed = _game_value("speed", "Walking speed.")
    player_icons = _game_value("player_icons", "Condition bitmask (haste, poison, ...).")
    position = _game_value("position", "(x, y, z) of the character.")
    player_id = _game_value("player_id", "Creature id of the character.")
    creatures = _game_value("creatures", "{id: creature} seen around the character.")
    messages = _game_value("messages", "Recent general server messages.")

    @property
    def game_proxy(self):
        return state.game_proxy

    @property
    def state(self):
        return state

    async def inject_to_server(self, packet: bytes, *, guard=None):
        """Send a client packet to the server; returns the delivery result.

        Raises CancelledError when automation is paused or the session this
        action started in has ended, so the action stops instead of acting
        in the wrong session.
        """
        self.last_receipt = None
        if state.automation_paused or not self._session.valid():
            raise asyncio.CancelledError()
        if not state.game_proxy:
            return None
        from dbv.client_bridge.command_delivery import delivery
        from dbv.brain.state.session_lifetime import SessionLease, current_task, task_alive
        lease, owner = SessionLease(state), current_task()

        def allowed():
            return (self._session.valid() and lease.valid() and task_alive(owner)
                    and not state.automation_paused and (guard is None or guard()))
        self.last_receipt = await state.game_proxy.inject_to_server(packet, guard=allowed)
        return await delivery(self.last_receipt)

    async def sleep(self, seconds: float) -> None:
        """Sleep, then stop the action if automation was paused meanwhile."""
        await asyncio.sleep(seconds)
        if state.automation_paused and self._action_name not in INTERNAL_ACTIONS:
            raise asyncio.CancelledError()

    async def use_item_in_container(self, item_id: int, container: int = 0, slot: int = 0) -> None:
        """Use the item in ``slot`` of an open container (ids as the client sends them: 64 is the first)."""
        await self.inject_to_server(wire.build_use_item_packet(0xFFFF, container, slot, item_id, slot, 0))

    async def use_item_on_map(self, x: int, y: int, z: int, item_id: int, stack_pos: int = 0, index: int = 0) -> None:
        await self.inject_to_server(wire.build_use_item_packet(x, y, z, item_id, stack_pos, index))

    async def say(self, text: str, *, guard=None):
        packet = wire.build_say_packet(text)
        if guard is None:
            await self.inject_to_server(packet)
            return None
        return await self.inject_to_server(packet, guard=guard)

    async def walk(self, direction: str, steps: int = 1, delay: float = 0.3) -> None:
        heading = _resolve_direction(direction)
        for _ in range(steps if heading is not None else 0):
            await self.inject_to_server(wire.build_walk_packet(heading))
            if steps > 1:
                await self.sleep(delay)

    def log(self, msg: str) -> None:
        """Log a line; an action's lines also appear in its panel log."""
        self._log.info(msg)
        if self._action_name:
            lines = state.action_logs.setdefault(self._action_name, collections.deque(maxlen=MAX_ACTION_LOGS))
            lines.append(f"[{_time.strftime('%H:%M:%S')}] {msg}")


bot_ctx = BotContext()


# ── running action scripts ──────────────────────────────────────────

def _load_action_module(name: str):
    """A fresh import of actions/<name>.py, or None for an unknown or unsafe name."""
    if any(not (c.isalnum() or c == "_") for c in name):
        return None
    source = ACTIONS_DIR / f"{name}.py"
    if not source.exists():
        return None
    spec = importlib.util.spec_from_file_location(f"actions.{name}", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check_speed_config(cfg):
    from dbv.brain.combat.speed_up import validate_config
    validate_config(cfg)


# Settings an action needs before it may start; each raises ValueError with the reason.
_CONFIG_CHECKS = {
    "speed_up": _check_speed_config,
    "auto_potions": lambda cfg: validate_potion_config(cfg, require_cooldowns=True),
    "barrier_spell": lambda cfg: validate_barrier_config(cfg),
    "auto_barrier_band": lambda cfg: validate_band_config(cfg),
}


def _adaptive_start_refusal():
    controller = getattr(state, "adaptive_cavebot", None)
    if controller is None or controller.status not in {"ready", "paused"} or not controller.path:
        return "Wybierz zapisaną trasę i użyj Start w Cavebot."
    try:
        controller.guard()
        if not controller.combat_only and list(state.game_state.position) != controller.path[controller.index]:
            return "Pozycja zmieniła się podczas pauzy. Uruchom od początku trasy."
    except ValueError as exc:
        return str(exc)
    return None


async def _wait_for_character(session):
    """True once the session knows its character; False if it ended first."""
    def waiting():
        return (state.connected and state.game_state is session and (
            not session.player_id or (session.client_state_required and not session.client_player_id)))
    while waiting():
        await asyncio.sleep(.05)
    return state.connected and state.game_state is session


async def _run_action(name, module, ctx):
    log.info("[action:%s] Started", name)
    try:
        if name in INTERNAL_ACTIONS or await _wait_for_character(state.game_state):
            await module.run(ctx)
            state._action_completed.add(name)
    except asyncio.CancelledError:
        state._action_completed.discard(name)
        log.info("[action:%s] Stopped", name)
    except Exception:
        state._action_completed.discard(name)
        log.error("[action:%s] Crashed:\n%s", name, traceback.format_exc())
    finally:
        if name == "adaptive_cavebot" and not state.automation_paused:
            state.settings.setdefault("actions", {}).setdefault(name, {})["enabled"] = False


def _start_action(name: str) -> str | None:
    """Start actions/<name>.py's ``run(bot)`` as a task. Returns why not, or None once started."""
    if name == "adaptive_cavebot":
        refusal = _adaptive_start_refusal()
        if refusal:
            return refusal
    if state.automation_paused and name not in INTERNAL_ACTIONS:
        return "Emergency pause is active. Unlock it before enabling actions."
    if name in _CONFIG_CHECKS:
        try:
            _CONFIG_CHECKS[name](state.settings.get("actions", {}).get(name, {}))
        except ValueError as exc:
            return str(exc)
    running = state._action_tasks.get(name)
    if running is not None and not running.done():
        return f"'{name}' is already running."
    module = _load_action_module(name)
    if module is None:
        return f"actions/{name}.py not found."
    if not asyncio.iscoroutinefunction(getattr(module, "run", None)):
        return f"actions/{name}.py has no async def run(bot)."
    state._action_completed.discard(name)
    state._action_tasks[name] = asyncio.create_task(_run_action(name, module, BotContext(action_name=name)))
    return None


def _stop_action(name: str) -> bool:
    """Cancel an action's task. Returns whether it was still running."""
    if name == "adaptive_cavebot":
        state._adaptive_prepare_token = None
        if getattr(state, "adaptive_cavebot", None) and state.adaptive_cavebot.path:
            state.adaptive_cavebot.pause("Nawigacja wstrzymana.")
    task = state._action_tasks.pop(name, None)
    if task is None or task.done():
        return False
    task.cancel()
    return True


def _start_all_enabled_actions() -> int:
    """After login: start the internal actions, then every enabled one. Returns how many started."""
    wanted = list(INTERNAL_ACTIONS)
    for name, cfg in state.settings.get("actions", {}).items():
        if name == "adaptive_cavebot":
            cfg["enabled"] = False  # navigation never resumes by itself after a login
        elif name not in INTERNAL_ACTIONS and cfg.get("enabled", False):
            wanted.append(name)
    started = 0
    for name in wanted:
        refusal = _start_action(name)
        if refusal:
            kind = " internal" if name in INTERNAL_ACTIONS else ""
            log.warning("[action:%s] Could not auto-start%s: %s", name, kind, refusal)
        else:
            started += 1
    return started


def _discover_actions() -> list[str]:
    """Names of the scripts in actions/, sorted."""
    if not ACTIONS_DIR.exists():
        return []
    return sorted(script.stem for script in ACTIONS_DIR.glob("*.py") if script.stem != "__init__")


# ── shared by the MCP tools and the panel API ───────────────────────

def _live(task):
    return task is not None and not task.done()


async def _async_toggle_action(name: str, enabled: bool) -> str:
    """Turn an action on or off, persist the choice, and start or stop it now. Returns a message."""
    if enabled and name == "adaptive_cavebot":
        return "Error: V2 uruchamiaj przez Start zapisanej trasy."
    if name in INTERNAL_ACTIONS:
        return f"Action '{name}' is an internal service and cannot be toggled."
    if enabled:
        if state.automation_paused:
            return "Error: Emergency pause is active. Unlock it first."
        if name in _CONFIG_CHECKS:
            try:
                _CONFIG_CHECKS[name](state.settings.get("actions", {}).get(name, {}))
            except ValueError as exc:
                return f"Error: {exc}"
    if not (ACTIONS_DIR / f"{name}.py").exists():
        return f"actions/{name}.py not found."
    state.settings.setdefault("actions", {}).setdefault(name, {})["enabled"] = enabled
    save_settings(state.settings)
    if not enabled:
        stopped = " and stopped" if _stop_action(name) else ""
        return f"Action '{name}' disabled{stopped}."
    if not state.connected:
        return f"Action '{name}' enabled. It will auto-start when the bot connects."
    refusal = _start_action(name)
    return f"Enabled but failed to start: {refusal}" if refusal else f"Action '{name}' enabled and started."


async def _async_update_action_config(name: str, changes: dict) -> dict:
    if name not in _discover_actions() or name in INTERNAL_ACTIONS:
        raise ValueError("Unknown or internal action")
    if not isinstance(changes, dict) or "enabled" in changes:
        raise ValueError("Use the toggle endpoint to enable/disable actions")
    cfg = {**state.settings.get("actions", {}).get(name, {}), **changes}
    if name == "auto_potions":
        cfg = validate_potion_config(cfg, require_cooldowns=cfg.get("enabled", False))
    if name == "auto_barrier_band": cfg = validate_band_config(cfg)
    if name == "barrier_spell": cfg = validate_barrier_config(cfg)
    if name == "speed_up":
        from dbv.brain.combat.speed_up import validate_config
        cfg = validate_config(cfg)
    if name == "auto_hunter_knife":
        from dbv.brain.combat.hunter_knife import validate_config as validate_knife_config
        cfg = validate_knife_config(cfg)
    state.settings.setdefault("actions", {})[name] = cfg
    save_settings(state.settings)
    return cfg


async def _async_emergency_stop(*, preserve_enabled=False) -> str:
    # Latch and cancel before the first await, including packets already queued.
    state._adaptive_prepare_token=None
    if state._emergency_stopping:
        if not preserve_enabled and state.automation_pause_kind=="manual":
            state.automation_pause_kind="emergency"
            state.settings["automation_pause_kind"]="emergency"
            state.settings.pop("paused_actions",None)
            for name,cfg in state.settings.get("actions",{}).items():
                if name not in INTERNAL_ACTIONS:cfg["enabled"]=False
            save_settings(state.settings)
        return "Emergency pause is already stopping actions."
    state._emergency_stopping = True
    state.automation_paused = True
    state.alert_center.set_automation_paused(True)
    state.settings["automation_paused"] = True
    state.automation_pause_kind = "manual" if preserve_enabled else "emergency"
    state.settings["automation_pause_kind"] = state.automation_pause_kind
    if preserve_enabled:
        state.settings["paused_actions"] = sorted((set(n for n,c in state.settings.get("actions",{}).items() if c.get("enabled")) | set(state._action_tasks)) - INTERNAL_ACTIONS - state._action_completed)
    else:
        state.settings.pop("paused_actions",None)
    if state.game_proxy:
        state.game_proxy.pause_injections()
    names = set(_discover_actions()) | set(state.settings.get("actions", {})) | set(state._action_tasks)
    tasks = []
    for name in names - INTERNAL_ACTIONS:
        if not preserve_enabled:
            state.settings.setdefault("actions", {}).setdefault(name, {})["enabled"] = False
        if _live(state._action_tasks.get(name)):
            tasks.append(state._action_tasks[name])  # waited for below, after cancelling
        _stop_action(name)
    if _live(state._auto_task):
        state._auto_task.cancel()
        tasks.append(state._auto_task)
    state._auto_task = None
    state._stopping_tasks = tasks
    state.game_state.lure_active = False  # never leave a lure half done
    if not preserve_enabled:
        if getattr(state,"adaptive_cavebot",None):state.adaptive_cavebot.stop("Awaryjny STOP — wybierz trasę ponownie.")
        state.potion_diagnostics.set_active(False)
    warning = ""
    try:
        save_settings(state.settings)
    except OSError:
        log.exception("Could not persist emergency pause")
        warning = " Settings could not be saved; pause is active for this session only."
    try:
        if tasks:
            await asyncio.wait(tasks, timeout=1)
        if state.game_proxy:
            try:
                await state.game_proxy.send_emergency_stop()
            except (OSError, asyncio.TimeoutError):
                warning += " Could not confirm movement/target cancellation on the server."
    finally:
        state._emergency_stopping = False
    return ("Automation paused. Selected actions are preserved for explicit resume." if preserve_enabled else "Emergency pause active. All user actions disabled; enable them individually after unlocking.") + warning


async def _async_pause_automation() -> str:
    if state.automation_paused:
        return "Automation is already paused."
    return await _async_emergency_stop(preserve_enabled=True)


async def _async_resume_automation() -> str:
    if not state.automation_paused:
        return "Automation is already running."
    if state.automation_pause_kind != "manual":
        raise ValueError("Emergency STOP requires unlocking and enabling actions individually")
    if state._emergency_stopping or any(not task.done() for task in state._stopping_tasks):
        raise ValueError("Wait for actions to finish stopping")
    names = [n for n in state.settings.get("paused_actions",[]) if n not in INTERNAL_ACTIONS
             and state.settings.get("actions",{}).get(n,{}).get("enabled")]
    previous=dict(state.settings)
    state.settings.update(automation_paused=False,automation_pause_kind=None)
    state.settings.pop("paused_actions",None)
    try: save_settings(state.settings)
    except OSError:
        state.settings=previous
        raise
    state.automation_paused=False
    state.automation_pause_kind=None
    if state.game_proxy: state.game_proxy.injections_paused=False
    errors=[]
    if state.connected:
        for name in names:
            if name=='adaptive_cavebot':
                try:await _async_adaptive_control('resume')
                except (ValueError,RuntimeError) as exc:errors.append(str(exc))
            else:
                error=_start_action(name)
                if error: errors.append(error)
    if errors:
        await _async_pause_automation()
        raise ValueError("Resume failed; paused again: " + "; ".join(errors))
    state.alert_center.set_automation_paused(state.automation_paused)
    return "Selected actions resumed." if state.connected else "Pause removed; selected actions will start after login."


async def _async_unlock_automation() -> str:
    if state._emergency_stopping:
        raise ValueError("Wait for emergency stop to finish before unlocking")
    if any(not task.done() for task in state._stopping_tasks):
        raise ValueError("Wait for actions to finish stopping")
    if state.automation_pause_kind == "manual":
        raise ValueError("Use resume for a manual pause")
    previous = state.settings.get("automation_paused", False)
    state.settings["automation_paused"] = False
    try:
        save_settings(state.settings)
    except OSError:
        state.settings["automation_paused"] = previous
        raise
    state.automation_paused = False
    state.alert_center.set_automation_paused(False)
    state.automation_pause_kind = None
    if state.game_proxy:
        state.game_proxy.injections_paused = False
    return "Unlocked. All user actions remain disabled."


async def _async_potion_diagnostics(enabled: bool) -> dict:
    state.potion_diagnostics.set_active(enabled)
    return state.potion_diagnostics.snapshot()


async def _async_item_identification(enabled: bool) -> dict:
    """Show or hide the ID of looked-at items; the choice persists."""
    previous = state.settings.get("item_identification", True)
    state.settings["item_identification"] = enabled
    try:
        save_settings(state.settings)
    except OSError:
        state.settings["item_identification"] = previous
        raise
    state.item_identification.enabled = enabled
    return state.item_identification.snapshot()


async def _async_adaptive_preview(config=None) -> dict:
    from dbv.brain.cavebot.cavebot_combat import validate_combat,eligible_monsters,choose_target,target_policy,attack_distance_ok
    from dbv.brain.combat.combat_spell_conditions import evaluate_spells
    config=validate_combat(config)
    monitor=getattr(state,'passive_map_monitor',None)
    sample=monitor.snapshot() if state.connected and monitor else None
    candidates=eligible_monsters(state.game_state,sample,config) if sample else []
    target=choose_target(candidates,config)
    spells=target_policy(config,target)['spells'] if target else config['spells']
    checks=evaluate_spells(spells,state.game_state,candidates,target,sample=sample)
    if target and not attack_distance_ok(config,target):
        for row in checks:row.update(ready=False,selected=False,reason='Strategia dystansowa: cel za blisko')
    return {'targets':sorted(candidates,key=lambda c:(-c['priority'],c['distance'])),'selected':target,'spells':checks,
            'message':'Podgląd niczego nie atakuje. Cel jest rozpoznawany tylko ze świeżego odczytu potwora i pola.'}


async def _async_adaptive_control(command: str, config: dict | None=None) -> dict:
    from dbv.brain.cavebot.adaptive_cavebot import AdaptiveCavebot,MANUAL_MOVES
    from dbv.brain.cavebot.route_identity import same_route
    name="adaptive_cavebot";controller=getattr(state,name,None)
    task=state._action_tasks.get(name)
    if command=='configure_combat':
        from dbv.brain.cavebot.cavebot_combat import validate_combat
        if task and not task.done():raise ValueError('Zatrzymaj przed zmianą profilu walki.')
        state.settings['combat_profile']=validate_combat((config or {}).get('combat'))
        selected=state.settings.get('selected_combat_profile')
        if selected:
            row=state.profile_store.get(selected,'combat')
            state.profile_store.save('combat',row['name'],state.settings['combat_profile'],selected)
        save_settings(state.settings)
        return {'configured':True}
    if command=='resume' and controller and controller.status=='running' and controller.navigation_hold:
        controller.retry_navigation()
        return controller.snapshot()
    if command in {'start','start_combat','resume'}:
        if task and not task.done():raise ValueError("V2 już wykonuje trasę.")
        if state._adaptive_prepare_token is not None:raise ValueError('Trwa przygotowanie trasy. Poczekaj lub naciśnij Stop.')
        config=config or {};candidate=AdaptiveCavebot(state)
        token=object();session=state;state._adaptive_prepare_token=token
        proxy=state.game_proxy
        def cancel_preparation(opcode,reader):
            if opcode in MANUAL_MOVES or opcode in {ClientOpcode.USE_ITEM,ClientOpcode.USE_ITEM_EX,ClientOpcode.CANCEL_ATTACK}:
                if session._adaptive_prepare_token is token:session._adaptive_prepare_token=None
        if proxy:proxy.register_client_packet_callback(cancel_preparation)
        try:
            resume=(command=='resume' or (command=='start' and controller and
                    controller.resumable_for(config.get('scope'),config.get('draft_id'),config.get('loop',True)) and
                    same_route(next((d for d in state.passive_map_monitor.atlas.drafts() if d['id']==controller.draft_id),None),controller.route_draft)))
            if resume:
                if not controller and getattr(state.game_state, "engine_journal", None):
                    controller = AdaptiveCavebot.from_session_cursor(state)
                if not controller or not controller.path or controller.status not in {'idle','paused','ready'}:
                    raise ValueError('Nie ma postępu trasy do wznowienia.')
                candidate=await controller.resume_candidate()
                candidate.status='ready'
            elif command=='start_combat':candidate.prepare_combat_only(config.get('combat',state.settings.get('combat_profile')))
            else:await candidate.prepare_async(config.get("scope"),config.get("draft_id"),config.get("loop",True),0)
            if state is not session or state._adaptive_prepare_token is not token:
                raise ValueError('Przygotowanie trasy anulowano. V2 pozostaje wyłączony.')
            active=state._action_tasks.get(name)
            if active and not active.done():raise ValueError('V2 jest już uruchomiony.')
            candidate.guard();candidate.map_now()
            previous=getattr(state,name,None);state.adaptive_cavebot=candidate
            error=_start_action(name)
            if error:
                state.adaptive_cavebot=previous
                raise ValueError(error)
            controller=candidate
            state.settings.setdefault("actions",{}).setdefault(name,{})["enabled"]=True
        finally:
            if proxy:proxy.unregister_client_packet_callback(cancel_preparation)
            if session._adaptive_prepare_token is token:session._adaptive_prepare_token=None
    elif command=="discard_progress":
        if (task and not task.done()) or (controller and controller.status=='running'):
            raise ValueError('Najpierw zatrzymaj V2, potem odrzuć postęp.')
        state._adaptive_prepare_token=None
        journal = getattr(state.game_state, "engine_journal", None)
        if journal is not None and not controller:
            journal.save_route(None)
        if controller:controller.stop('Postęp odrzucony. Zapisana trasa i ustawienia walki pozostają zachowane.')
        state.settings.setdefault('actions',{}).setdefault(name,{})['enabled']=False
    elif command in {"pause","stop"}:
        _stop_action(name)
        if task:
            await asyncio.gather(task,return_exceptions=True)
            if state.game_proxy and state.connected and not getattr(controller,"combat_only",False):
                await state.game_proxy.inject_to_server(wire.build_stop_walk_packet())
        state.settings.setdefault("actions",{}).setdefault(name,{})["enabled"]=False
        if controller and command=="stop":controller.stop(preserve_progress=True)
    else:raise ValueError("Nieznane polecenie V2.")
    return controller.snapshot() if controller else {"status":"idle","reason":"V2 jest wyłączony."}


async def _async_restart_action(name: str) -> str:
    """Reload an action's code from disk and start it again. Returns a message."""
    if name == "adaptive_cavebot":
        return "Użyj Start/Wznów w Cavebot."
    if name == "auto_barrier_band":
        try:
            validate_band_config(state.settings.get("actions", {}).get(name, {}))
        except ValueError as exc:
            return f"Error: {exc}"
    if not (ACTIONS_DIR / f"{name}.py").exists():
        return f"actions/{name}.py not found."
    _stop_action(name)
    await asyncio.sleep(0.1)  # let the cancelled task unwind first
    refusal = _start_action(name)
    return f"Failed to restart: {refusal}" if refusal else f"Action '{name}' restarted (code reloaded from disk)."


DASHBOARD_PORT = 4747
_dashboard_launched = False   # a launch was requested and has not been stopped
_dashboard_processes: dict[str, subprocess.Popen] = {}
_panel_server = None  # serves dashboard/.next on DASHBOARD_PORT in this process


def _dashboard_running(name: str) -> bool:
    process = _dashboard_processes.get(name)
    return process is not None and process.poll() is None


def _end_process_tree(process):
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, timeout=10)
    else:
        process.terminate()
    process.wait(timeout=5)


def _stop_dashboard() -> None:
    """Stop only process trees started by this MCP server, never other apps."""
    global _dashboard_launched
    for process in _dashboard_processes.values():
        if process.poll() is None:
            try:
                _end_process_tree(process)
            except (OSError, subprocess.TimeoutExpired):
                log.warning("Could not stop dashboard child PID %s", process.pid)
    _dashboard_processes.clear()
    _stop_panel_server()
    _dashboard_launched = False


def _stop_panel_server() -> None:
    global _panel_server
    server, _panel_server = _panel_server, None
    if server is not None:
        server.shutdown()
        server.server_close()


def _panel_window_command() -> list[str] | None:
    """The panel window process (pywebview), or None when pywebview is not installed."""
    if importlib.util.find_spec("webview") is None:
        return None
    python = Path(sys.executable)
    # pythonw, as DBVictory.exe (the bot's name and icon) when the release ships it.
    windowed = next((p for p in (python.with_name("DBVictory.exe"), python.with_name("pythonw.exe")) if p.is_file()), python)
    return [str(windowed), "-B", "-m", "dbv.mcp_server.panel_window"]


atexit.register(_stop_dashboard)


@atexit.register
def _flush_movement_report():
    history = getattr(state.game_state, "movement_history", None)
    if history:history.close(wait=True)


@atexit.register
def _close_observer():
    state.packet_journal.stop("exit")
    if state.observer_bridge is not None:
        state.observer_bridge.close()


def _check_process_running(name: str) -> bool:
    """Whether Windows lists a process with this image name."""
    try:
        listing = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
                                 capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return False
    return name.lower() in listing.lower()


def _check_port_listening(port: int) -> bool:
    """Probe loopback without shelling out to netstat."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _check_pipe_exists() -> bool:
    """Whether the observer DLL is serving its pipe (without taking the connection)."""
    try:
        bridge = getattr(state.game_state, "dll_bridge", None)
        if bridge is not None:
            return bridge.connected
        from dbv.client_bridge.observer_pipe import ObserverPipe
        from dbv.client_bridge.passive_map_win32 import ReadOnlyClient
        client = ReadOnlyClient()
        try:
            return ObserverPipe(client.profile).exists()
        finally:
            client.close()
    except Exception:
        return False


def _proxy_line():
    proxy = state.game_proxy
    if proxy is None:
        return "[--] Proxy: not started"
    if not state.ready:
        return "[..] Proxy: started, waiting for login"
    sent, received = proxy.packets_from_client, proxy.packets_from_server
    both_ends = proxy.server_writer is not None and proxy.client_writer is not None
    if proxy.logged_in and both_ends:
        return f"[OK] Proxy: connected (server={received} client={sent} packets)"
    return (f"[!!] Proxy: BROKEN (logged_in={proxy.logged_in} writers={both_ends} "
            f"server={received} client={sent})")


def _dashboard_line():
    serving = _check_port_listening(DASHBOARD_PORT)
    if serving and _dashboard_running("window"):
        return f"[OK] Dashboard: panel window open, served on port {DASHBOARD_PORT}"
    if serving:
        return f"[OK] Dashboard: server on port {DASHBOARD_PORT} (window starting...)"
    if _dashboard_launched:
        return "[..] Dashboard: launch requested, waiting for startup..."
    return "[--] Dashboard: not launched"


def _build_status_report() -> str:
    """One line per part of the bot: [OK] working, [..] starting, [--] off, [!!] broken."""
    lines = ["[OK] MCP Server: running", ENGINE_LABEL,
             "[OK] Game Client: dbvStart.exe detected" if _check_process_running("dbvStart.exe")
             else "[!!] Game Client: dbvStart.exe NOT found",
             _proxy_line(),
             "[OK] DLL Pipe: observer pipe available" if _check_pipe_exists()
             else "[--] DLL Pipe: not detected (dll_bridge action will inject)",
             _dashboard_line()]
    gs = state.game_state
    if gs.player_id:
        x, y, z = gs.position
        lines.append(f"[OK] Player: id={gs.player_id:#010x} pos=({x},{y},{z}) "
                     f"HP={gs.hp}/{gs.max_hp} creatures={len(gs.creatures)}")
    return "\n".join(lines)


def _activate_staged_dashboard(directory):
    """Switch the prebuilt panel for this engine before starting its web server."""
    if not ENGINE_REVISION:
        return
    staged = directory / f".next-ready-{ENGINE_REVISION[:7]}"
    if not (staged / "BUILD_ID").is_file() or not (staged / "routes-manifest.json").is_file():
        return
    current = directory / ".next"
    previous = directory / f".next-before-{_time.time_ns()}"
    current.rename(previous)
    try:
        staged.rename(current)
    except OSError:
        previous.rename(current)
        raise


def _launch_dashboard() -> None:
    """Serve the prebuilt, loopback-only panel and open its authenticated desktop window."""
    global _dashboard_launched, _panel_server
    from dbv.mcp_server import panel_server
    dashboard_dir = paths.DASHBOARD_DIR
    if os.name != "nt":
        log.warning("The game dashboard requires Windows.")
        return
    if not (dashboard_dir / ".next" / "BUILD_ID").exists():
        log.warning("Build the dashboard first: cd dashboard && npm ci && npm run build")
        return
    window = _panel_window_command()
    if window is None:
        log.warning("The panel window needs pywebview — run: pip install -r requirements.txt")
        return
    if _panel_server is None and _check_port_listening(DASHBOARD_PORT):
        log.error("Dashboard port %s is occupied by another process; refusing to reuse it.", DASHBOARD_PORT)
        return
    try:
        if _panel_server is None:
            _activate_staged_dashboard(dashboard_dir)
            _panel_server = panel_server.start(dashboard_dir, DASHBOARD_PORT)
        if not _dashboard_running("window"):
            env = os.environ.copy()
            for name in ("DBV_DASHBOARD_TOKEN", EXIT_FILE_ENV, "ELECTRON_RUN_AS_NODE", "NODE_OPTIONS"):
                env.pop(name, None)
            if getattr(state, "tray_exit_file", None):
                env[EXIT_FILE_ENV] = str(state.tray_exit_file)
            env["DBV_DASHBOARD_TOKEN"] = SESSION_TOKEN
            env["DBV_ENGINE_LABEL"] = ENGINE_LABEL
            _dashboard_processes["window"] = subprocess.Popen(
                window, cwd=str(paths.INSTALL_ROOT),
                env=env, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        _dashboard_launched = True
    except Exception as e:
        log.warning("Failed to launch dashboard: %s", e)
        _stop_dashboard()


# ── MCP tools ───────────────────────────────────────────────────────

mcp = FastMCP("dbvictory-bot", instructions=(
        "DBVictory game bot. Inspect get_status and list_actions first. "
        "Call start_bot only when the user wants to connect to the Windows game client. "
        "It patches client memory and launches local proxies. Login happens in the "
        "game client; never request or log credentials. Enable only requested actions."))

def _abbreviation(name):
    return "".join(part[0] for part in ("north", "south", "east", "west") if part in name)


# Every spelling a direction may be given in: "ne" or "northeast", in any case.
DIR_MAP = {spelling: heading for heading in Direction
           for spelling in (heading.name.lower(), _abbreviation(heading.name.lower()))}
TURN_DIR_MAP = {spelling: heading for spelling, heading in DIR_MAP.items() if heading <= Direction.WEST}


def _resolve_direction(direction: str, allow_diagonal: bool = True) -> Direction | None:
    return (DIR_MAP if allow_diagonal else TURN_DIR_MAP).get(direction.strip().lower())


# ── reset and hot reload ────────────────────────────────────────────

def _proxies():
    return [proxy for proxy in (state.game_proxy, state.login_proxy) if proxy]


async def _close_relay_sockets(proxy):
    """Close both relay sockets and wait for Windows to release them.

    A game client left on a half-closed socket never reconnects to the new proxy.
    """
    for side in ("client_writer", "server_writer"):
        writer = getattr(proxy, side, None)
        if writer:
            try:
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
            except Exception:
                pass


async def _stop_proxies():
    # Stop listening first, so an old proxy cannot accept the reconnecting client.
    for proxy in _proxies():
        if hasattr(proxy, "close_server"):
            proxy.close_server()
        await _close_relay_sockets(proxy)
    # With the sockets closed the relay tasks can finish; cancel them and the servers.
    pending = [getattr(proxy, "_connection_task", None) for proxy in _proxies()] + state._proxy_tasks
    pending = [task for task in pending if _live(task)]
    for task in pending:
        task.cancel()
    state._proxy_tasks.clear()
    for task in pending:
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.CancelledError, Exception):
            pass
    for proxy in _proxies():
        proxy.client_writer = proxy.server_writer = None
        proxy.logged_in = False
    await asyncio.sleep(1.0)  # Windows needs time to release the ports


def _reload(module, label):
    try:
        importlib.reload(module)
        log.info("Reloaded %s.py", label)
    except Exception as exc:
        log.warning("Failed to reload %s: %s", label, exc)


async def _reset_bot() -> str:
    """Stop everything for this connection, reload the parser and recorder, and start clean."""
    from dbv.brain.state import game_state as _gs_mod

    log.info("=== RESETTING BOT ===")
    pending_start = state.login_startup.cancel()
    if pending_start is not None:
        await asyncio.gather(pending_start, return_exceptions=True)

    state._adaptive_prepare_token=None
    state.packet_journal.stop("reset")
    history = getattr(state.game_state, "movement_history", None)
    if history:history.close()
    for name in list(state._action_tasks):
        _stop_action(name)
    if getattr(state,"adaptive_cavebot",None):state.adaptive_cavebot.stop("Sesja została zresetowana.")
    if getattr(state,"packet_observer",None):
        await state.packet_observer.close()
        state.packet_observer = None
    await asyncio.sleep(0.1)
    await _stop_proxies()

    # Stop only our dashboard process trees.
    _stop_dashboard()

    _reload(_gs_mod, "game_state")
    # Keep dashboard_api alive: its loopback listeners, credentials and client
    # registry belong to this MCP process, not to one game connection.
    from dbv.brain.cavebot import cavebot as _cb_mod
    _reload(_cb_mod, "cavebot")
    globals().update(scan_packet=_gs_mod.scan_packet, parse_server_packet=_gs_mod.parse_server_packet)
    from dbv.brain.state.game_state import GameState

    state.login_proxy = state.game_proxy = None
    state.ready = False
    state._auto_task = None
    state._action_tasks.clear()
    state.game_state = GameState()
    state.settings = load_settings()
    state.automation_paused = bool(state.settings.get("automation_paused", False))
    state.alert_center.set_automation_paused(state.automation_paused)
    state.automation_pause_kind = state.settings.get("automation_pause_kind","emergency") if state.automation_paused else None
    state._stopping_tasks = []
    state.recording_active, state.recording_name = False, ""
    state.recording_waypoints = []
    state.recording_start_pos, state.recording_start_time = (0, 0, 0), 0
    state._recording_callback = None

    log.info("Bot reset complete.")
    return "Bot reset. Call start_bot to reconnect."


def _close_game_connections(pid: int) -> int:
    """Drop the client's connections to the login and game ports (the local proxy too).

    Called after the old proxy is gone and before the new one starts, so the
    client goes back to its login screen and reconnects through the new proxy.
    """
    from dbv.client_bridge.tcp_reset import close_connections
    return close_connections(pid, (GAME_PORT, LOGIN_PORT))


# ── tools ───────────────────────────────────────────────────────────

def _client_compatibility():
    """(profile, observer) of the running client, or an error message string."""
    try:
        from dbv.client_bridge.passive_map_win32 import ReadOnlyClient
        verified_client = ReadOnlyClient()
        try:
            client_profile = verified_client.profile
            observer = client_profile.observer
        finally:
            verified_client.close()
        if observer and not paths.DLL_DIR.joinpath(client_profile.observer_dll).is_file():
            return f"ERROR: Build dll/{client_profile.observer_dll} before starting this client version."
    except Exception as exc:
        return f"ERROR: Client compatibility check failed: {exc}"
    return client_profile, observer


async def _redirect_client():
    """Point the client at the local proxy. Returns (pid, copies changed) or an error message."""
    try:
        import pymem
    except ImportError:
        return "ERROR: pymem is not installed. Run: pip install pymem"
    try:
        pm = pymem.Pymem("dbvStart.exe")
    except Exception as exc:
        return f"ERROR: Could not attach to dbvStart.exe — is the game running? ({exc})"
    pid = pm.process_id
    log.info("Attached to client (PID: %s)", pid)
    # A client that is logged in already must drop back to its login screen,
    # so that its next login goes through the proxy.
    dropped = _close_game_connections(pid)
    if dropped:
        log.info("Closed %d existing game connection(s) — client should return to login screen", dropped)
        await asyncio.sleep(2)
    from dbv.client_bridge.client_redirect import ClientMemory, redirect_server_address
    try:
        patched = redirect_server_address(ClientMemory(pm), SERVER_HOST)
    finally:
        pm.close_process()
    if not patched:
        log.warning("No IPs patched (client may already be patched). Continuing...")
    return pid, patched


def _note_client_inventory_change():
    session_observations(state.game_state).publish("client_action", ("inventory",))
    state.game_state.message_history.invalidate_inventory()
    state.potion_diagnostics.invalidate_inventory()
    usage = getattr(state.game_state, 'senzu_usage', None)
    if usage is not None:
        usage.invalidate_inventory()


def _note_client_item_use(opcode, reader):
    tile, item_id, stack = reader.read_position(), reader.read_u16(), reader.read_u8()
    if item_id == 5908:
        state.game_state.knife_manual_use_at = _time.monotonic()
    if item_id in HEALING_ITEMS:
        mode = "self" if opcode == ClientOpcode.USE_ON_CREATURE else "use" if opcode == ClientOpcode.USE_ITEM else "use_with"
        if opcode == ClientOpcode.USE_ON_CREATURE and reader.read_u32() != state.game_state.player_id:
            mode = "other_creature"
        state.potion_diagnostics.record_use(item_id, mode, state.game_state)
        cfg = validate_potion_config(state.settings.get("actions", {}).get("auto_potions", {}))
        state.potion_scheduler.record_item_use(item_id, cfg, _time.monotonic(), state.game_state.stats_updated_at)
        journal = getattr(state.game_state, "engine_journal", None)
        if journal is not None:
            journal.note_use(item_id, state.potion_scheduler)
    log.info("[SNIFF] USE_ITEM pos=%s item_id=%s stack=%s", tile, item_id, stack)


_INVENTORY_CHANGES = frozenset({ClientOpcode.MOVE_THING, ClientOpcode.REQUEST_TRADE, ClientOpcode.ACCEPT_TRADE,
                                ClientOpcode.BUY_ITEM, ClientOpcode.SELL_ITEM})
_ITEM_USES = frozenset({ClientOpcode.USE_ITEM, ClientOpcode.USE_ITEM_EX, ClientOpcode.USE_ON_CREATURE})


def _observe_client_packet(opcode, reader):
    """What the player does in the client: inventory changes, the attack target, item uses."""
    if log.isEnabledFor(logging.DEBUG):
        label = ClientOpcode(opcode).name if opcode in ClientOpcode._value2member_map_ else "?"
        log.debug("[C->S] 0x%02X (%s)", opcode, label)
    if opcode in _INVENTORY_CHANGES:
        _note_client_inventory_change()
    try:
        if opcode == ClientOpcode.ATTACK:
            state.game_state.attack_target_id = reader.read_u32()
            session_observations(state.game_state).publish("client_action", ("target",))
        elif opcode in _ITEM_USES:
            _note_client_item_use(opcode, reader)
    except Exception:
        pass  # a short or odd packet is the client's business, not ours


@mcp.tool()
async def start_bot() -> str:
    """Connect the bot to the running game client.

    dbvStart.exe must be running at its login screen. The client is pointed
    at the local login and game proxies; the player then logs in through
    the client as usual, and enabled actions start after login.
    """
    if os.name != "nt":
        return "ERROR: start_bot requires native Windows and the running DBVictory client."

    # Fail before any patch, socket closure or session teardown on unknown builds.
    compatible = _client_compatibility()
    if isinstance(compatible, str):
        return compatible
    client_profile, observer = compatible

    if state.ready or state.game_proxy or state.login_proxy:
        log.info("Bot already running — auto-resetting...")
        await _reset_bot()
    redirected = await _redirect_client()
    if isinstance(redirected, str):
        return redirected
    game_pid, patched = redirected
    log.info("Patched %s server IP(s)", patched)

    state.login_proxy = OTProxy(SERVER_HOST, LOGIN_PORT, LOGIN_PORT, is_login_proxy=True)
    state.game_proxy = OTProxy(SERVER_HOST, GAME_PORT, GAME_PORT, is_login_proxy=False)
    from dbv.client_bridge.client_profiles import NOVEMBER_26
    if client_profile == NOVEMBER_26:
        from dbv.client_bridge.client_protocol import read_live_protocol_key
        state.game_proxy.client_key_reader = lambda: read_live_protocol_key(game_pid)
    state.game_proxy.injections_paused = state.automation_paused
    # The bounded observer handles each server frame once.

    def on_login_success(keys):
        if state.game_proxy is not observation_proxy or state.login_startup.owns(observation_proxy):
            return
        if getattr(state,"packet_observer",None):state.packet_observer.invalidate()
        previous = getattr(state.game_state, "movement_history", None)
        if previous:previous.close()
        # New identity/stats/conditions for every login, including reconnects.
        state.game_state = login_state = GameState()
        from dbv.brain.cavebot.movement_history import MovementHistory
        login_state.movement_history = MovementHistory()
        login_state.movement_history.start_worker()
        login_state.client_state_required = observer
        login_state.session_id = observation_proxy.session_generation
        if getattr(state, "packet_journal", None):state.packet_journal.session_changed(observation_proxy.session_generation)
        session_observations(login_state)
        monitor = getattr(state, "passive_map_monitor", None)
        if monitor:
            monitor.reset_learning_session()
        login_state.lure_active = False  # a lure never carries over a reconnect
        state.ready = True
        log.info("=== BOT READY — game session established ===")
        state.login_startup.schedule(state, _start_all_enabled_actions)

    # Only the owner loop can publish parser results or notify other domains.
    from dbv.client_bridge.packet_observer import PacketObserver, publish_server_feedback
    observation_proxy = state.game_proxy

    def apply_packet_observations(gs, result, kills):
        if gs is not state.game_state:return
        journal = getattr(state, "packet_journal", None)
        if journal is not None and journal.enabled:
            # Metadata and already-parsed events only; never the frame itself.
            journal.server_frame(result.first_opcode, result.plain_size, result.sequential_complete,
                                 state.packet_observer.last_lag_ms if state.packet_observer else None)
            journal.server_events(result.state.server_events)
            journal.kills(kills)
            for message in result.messages:
                journal.chat(message)
            batch = result.state.message_history
            journal.server_messages(list(batch.general) + list(batch.combat))
        publish_semantic_result(gs, result, kills)
        for message in result.messages:
            state.alert_center.observe_chat(message, state, gs, result.received_at)
        publish_server_feedback(gs, result, state.potion_diagnostics)

    def packet_observation_gap(reason):
        gs = state.game_state
        if getattr(state, "packet_journal", None):state.packet_journal.gap(reason)
        note_observation_gap(gs, reason)
        history = getattr(gs, "movement_history", None)
        if history:history.observation_gap(reason)
        gs.client_walk_requests.clear()
        gs.packet_position_trusted = False
        gs.pets.invalidate_pending_look()
        gs.message_history.invalidate_inventory()
        usage = getattr(gs, "senzu_usage", None)
        if usage is not None:
            usage.invalidate_inventory()
        state.potion_diagnostics.invalidate_inventory()
        monitor = getattr(state,"passive_map_monitor",None)
        if monitor:monitor.invalidate_packet_evidence()

    state.packet_observer = PacketObserver(
        lambda: state.game_state,
        lambda: observation_proxy.session_generation,
        lambda: state.game_proxy is observation_proxy and observation_proxy.logged_in,
        apply_packet_observations, packet_observation_gap)

    def on_disconnect():
        if state.game_proxy is not observation_proxy:
            return
        state.login_startup.cancel()
        if getattr(state, "packet_journal", None):state.packet_journal.stop("disconnected")
        if getattr(state,"packet_observer",None):state.packet_observer.invalidate()
        history = getattr(state.game_state, "movement_history", None)
        if history:history.close()
        state._adaptive_prepare_token=None
        state.potion_diagnostics.invalidate_inventory()
        monitor = getattr(state, "passive_map_monitor", None)
        if monitor:
            monitor.reset_learning_session()
        state.ready = False
        state.game_state.stats_updated_at = 0
        state.game_state.lure_active = False
        for name in list(state._action_tasks):
            _stop_action(name)  # The passive reader also belongs to this login.
        if getattr(state,"adaptive_cavebot",None):state.adaptive_cavebot.stop("Rozłączono — wymagany ponowny Start.")
        if state._auto_task:
            state._auto_task.cancel()
        state.game_proxy.pause_injections()
        state.game_proxy.injections_paused = state.automation_paused

    def observe_navigation_command(opcode, reader):
        monitor = getattr(state, "passive_map_monitor", None)
        if monitor:
            monitor.record_client_packet(opcode, reader)

    def identify_looked_item(opcode, reader):
        item_id = state.item_identification.record_client_packet(opcode, reader)
        if item_id is not None and state.game_proxy:
            from dbv.brain.state.item_identification import status_message
            log.info("Item Id: %s", item_id)
            asyncio.get_running_loop().create_task(state.game_proxy.inject_to_client(status_message(item_id)))

    def journal_client_packet(opcode, reader):
        if getattr(state, "packet_journal", None) and state.packet_journal.enabled:
            state.packet_journal.client_packet(opcode, reader.remaining + 1)

    for observe in (_observe_client_packet, observe_navigation_command, identify_looked_item, journal_client_packet):
        state.game_proxy.register_client_packet_callback(observe)
    state.game_proxy.on_login_success = on_login_success
    state.game_proxy.on_game_disconnected = on_disconnect
    state.game_proxy.on_encrypted_server_data = state.packet_observer.offer
    state._proxy_tasks = [asyncio.create_task(proxy.start()) for proxy in (state.login_proxy, state.game_proxy)]
    log.info("Proxies started — waiting for player to log in via the game client...")

    # The panel API and window start now; they do not wait for the login.
    from dbv.mcp_server import dashboard_api
    dashboard_api.start_api(asyncio.get_running_loop(), state)
    _launch_dashboard()
    _publish_observer_bridge()
    return (f"Bot launched. Patched {patched} IP(s). Proxies listening.\n"
            "Log in through the game client — actions will auto-start.\n\n"
            f"=== Component Status ===\n{_build_status_report()}")


NOT_CONNECTED = "Bot is not connected. Call start_bot first."


async def _send(packet):
    await state.game_proxy.inject_to_server(packet)


@mcp.tool()
async def walk(direction: str, steps: int = 1) -> str:
    """Take one or more steps. direction: n, e, s, w, ne, se, sw, nw or the full word."""
    if not state.connected:
        return NOT_CONNECTED
    heading = _resolve_direction(direction)
    if heading is None:
        return f"Unknown direction: {direction}. Use n/s/e/w/ne/se/sw/nw."
    for _ in range(steps):
        await _send(wire.build_walk_packet(heading))
        if steps > 1:
            await asyncio.sleep(0.3)
    return f"Walked {heading.name} {steps} step(s)."


@mcp.tool()
async def turn(direction: str) -> str:
    """Face north, east, south or west (n/e/s/w) without moving."""
    if not state.connected:
        return NOT_CONNECTED
    heading = _resolve_direction(direction, allow_diagonal=False)
    if heading is None:
        return f"Unknown direction: {direction}. Turn only supports n/s/e/w."
    await _send(wire.build_turn_packet(heading))
    return f"Turned {heading.name}."


@mcp.tool()
async def say(text: str) -> str:
    """Say a line in the default chat channel (spells are said this way too)."""
    if not state.connected:
        return NOT_CONNECTED
    await _send(wire.build_say_packet(text))
    return f"Said: {text}"


@mcp.tool()
async def attack(creature_id: int) -> str:
    """Attack the creature with this id (ids come from the status and the observer)."""
    if not state.connected:
        return NOT_CONNECTED
    await _send(wire.build_attack_packet(creature_id))
    return f"Attacking creature {creature_id}."


@mcp.tool()
async def follow(creature_id: int) -> str:
    """Follow the creature with this id."""
    if not state.connected:
        return NOT_CONNECTED
    await _send(wire.build_follow_packet(creature_id))
    return f"Following creature {creature_id}."


@mcp.tool()
async def stop() -> str:
    """Stop walking."""
    if not state.connected:
        return NOT_CONNECTED
    await _send(wire.build_stop_walk_packet())
    return "Stopped walking."


def _autowalk_refusal():
    if _live(state._action_tasks.get("adaptive_cavebot")):
        return "Zatrzymaj Cavebot przed uruchomieniem auto-walk."
    if state.automation_paused:
        return "Emergency pause is active. Unlock it first."
    if not state.connected:
        return NOT_CONNECTED
    return None


@mcp.tool()
async def autowalk(direction: str, steps: int = 100, delay: float = 0.5) -> str:
    """Keep stepping in one direction in the background, at most `steps` times, `delay` seconds apart."""
    refusal = _autowalk_refusal()
    if refusal:
        return refusal
    heading = _resolve_direction(direction)
    if heading is None:
        return f"Unknown direction: {direction}."
    if _live(state._auto_task):
        state._auto_task.cancel()

    async def keep_walking():
        for _ in range(steps):
            if not state.connected:
                return
            await _send(wire.build_walk_packet(heading))
            await asyncio.sleep(delay)

    state._auto_task = asyncio.create_task(keep_walking())
    return f"Auto-walking {heading.name} for up to {steps} steps (delay={delay}s). Use stop_autowalk to cancel."


@mcp.tool()
async def stop_autowalk() -> str:
    """Cancel the background walk started by autowalk."""
    if not _live(state._auto_task):
        return "No auto-walk is running."
    state._auto_task.cancel()
    state._auto_task = None
    if state.connected:
        await _send(wire.build_stop_walk_packet())
    return "Auto-walk cancelled."


@mcp.tool()
async def use_item(x: int, y: int, z: int, item_id: int, stack_pos: int = 0, index: int = 0) -> str:
    """Use the item `item_id` on map tile (x, y, z): a door, stairs, a lever... stack_pos is its place in the tile's stack."""
    if not state.connected:
        return NOT_CONNECTED
    await _send(wire.build_use_item_packet(x, y, z, item_id, stack_pos, index))
    return f"Used item {item_id} at ({x}, {y}, {z})."


@mcp.tool()
async def move_item(from_x: int, from_y: int, from_z: int, item_id: int, from_stack: int,
                    to_x: int, to_y: int, to_z: int, count: int = 1) -> str:
    """Drag `count` of item `item_id` from one tile (or container slot) to another."""
    if not state.connected:
        return NOT_CONNECTED
    source, target = (from_x, from_y, from_z), (to_x, to_y, to_z)
    await _send(wire.build_move_item_packet(source, item_id, from_stack, target, count))
    return f"Moved {count}x item {item_id} from ({from_x},{from_y},{from_z}) to ({to_x},{to_y},{to_z})."


@mcp.tool()
async def look_at(x: int, y: int, z: int, item_id: int, stack_pos: int = 0) -> str:
    """Look at item `item_id` on tile (x, y, z); the server answers with its description."""
    if not state.connected:
        return NOT_CONNECTED
    await _send(wire.build_look_packet(x, y, z, item_id, stack_pos))
    return f"Looking at item {item_id} at ({x}, {y}, {z})."


def _action_summary(source: str) -> str:
    """The first non-empty line of a script without quotes, cut to 50 characters."""
    for line in source.splitlines():
        text = line.strip().strip('"').strip("'")
        if text:
            return text[:50]
    return ""


def _action_status(name):
    if not state.settings.get("actions", {}).get(name, {}).get("enabled", False):
        return "OFF"
    return ">>> RUNNING" if _live(state._action_tasks.get(name)) else "ON (idle)"


def _table(header, rows):
    widths = [max([len(title), *(len(row[i]) for row in rows)]) for i, title in enumerate(header)]
    rule = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def line(cells):
        return "| " + " | ".join(f"{cell:<{w}}" for cell, w in zip(cells, widths)) + " |"
    return [rule, line(header), rule, *map(line, rows), rule]


@mcp.tool()
async def list_actions() -> str:
    """Show every action in actions/ with its state and the start of its code.

    A new action is a .py file in actions/ with `async def run(bot)`. `bot`
    offers say(text), walk(direction, steps, delay), sleep(seconds),
    use_item_in_container(item_id, container, slot), inject_to_server(packet),
    log(msg), is_connected, and the character's hp, mana, position, creatures...
    """
    names = [name for name in _discover_actions() if name not in INTERNAL_ACTIONS]
    if not names:
        return "No actions found. Create a .py file in the actions/ folder with async def run(bot)."
    sources = {}
    for name in names:
        try:
            sources[name] = (ACTIONS_DIR / f"{name}.py").read_text(encoding="utf-8")
        except OSError:
            sources[name] = ""
    lines = _table(("Action", "Status", "Description"),
                   [(name, _action_status(name), _action_summary(sources[name])) for name in names])
    for name in names:
        preview = sources[name].rstrip()
        if len(preview) > 500:
            preview = preview[:500] + "\n... (truncated)"
        lines += [f"\n--- {name}.py ---", preview]
    return "\n".join(lines)


@mcp.tool()
async def toggle_action(name: str, enabled: bool = True) -> str:
    """Turn an action on (and start it) or off (and stop it). name is the file name without .py."""
    return await _async_toggle_action(name, enabled)


@mcp.tool()
async def remove_action(name: str) -> str:
    """Stop an action, delete its script and forget its settings."""
    if name in INTERNAL_ACTIONS:
        return f"Action '{name}' is an internal service and cannot be removed."
    if any(not (c.isalnum() or c == "_") for c in name):
        return "Invalid action name. Only alphanumeric characters and underscores are allowed."
    _stop_action(name)
    script = ACTIONS_DIR / f"{name}.py"
    had_script = script.exists()
    if had_script:
        script.unlink()
    had_settings = state.settings.get("actions", {}).pop(name, None) is not None
    if had_settings:
        save_settings(state.settings)
    return f"Removed action '{name}'." if had_script or had_settings else f"Action '{name}' not found."


@mcp.tool()
async def restart_action(name: str) -> str:
    """Reload an action's script from disk and run it again."""
    return await _async_restart_action(name)


_FIGHT_MODE_NAMES = ({1: "offensive", 2: "balanced", 3: "defensive"}, {0: "stand", 1: "chase"}, {0: "PvP", 1: "safe"})


@mcp.tool()
async def set_fight_modes(fight_mode: int = 1, chase_mode: int = 0, safe_mode: int = 1) -> str:
    """Set the fight stance (1 offensive, 2 balanced, 3 defensive), chase (0 stand, 1 chase) and safe mode (1 no PvP)."""
    if not state.connected:
        return NOT_CONNECTED
    await _send(wire.build_set_fight_modes_packet(fight_mode, chase_mode, safe_mode))
    chosen = [names.get(value, value) for names, value in zip(_FIGHT_MODE_NAMES, (fight_mode, chase_mode, safe_mode))]
    return "Fight modes set: {}, {}, {}.".format(*chosen)


@mcp.tool()
async def send_ping() -> str:
    """Send a ping to the game server."""
    if not state.connected:
        return NOT_CONNECTED
    await _send(wire.build_ping_packet())
    return "Ping sent."


@mcp.tool()
async def start_local_transport(upstream_port: int) -> dict[str, int | str]:
    """Start the separate transparent transport for an explicitly local test server.

    Returns the loopback listener port. 'listening' means ready for a client;
    'connected' means a client has reached the upstream service.
    """
    return await state.local_transport.start(upstream_port)


@mcp.tool()
async def stop_local_transport() -> dict[str, int | str]:
    """Stop the launcher's local transport and close its test connection."""
    return await state.local_transport.stop()


@mcp.tool()
async def get_status() -> str:
    """Report each part of the bot (client, proxy, observer, panel, character) and what is running."""
    status = _build_status_report()
    local = await state.local_transport.snapshot()
    status += f"\nLocal transport: {local['state']}"
    if local.get("port"):
        status += (f" · 127.0.0.1:{local['port']} · connections {local['connections']}"
                   f" · failures {local['failures']}")
    status += f"\nEmergency pause: {'ACTIVE' if state.automation_paused else 'off'}"
    running = [name for name, task in state._action_tasks.items() if task and not task.done()]
    if running:
        status += "\n\nRunning actions: " + ", ".join(running)
    status += "\nAuto-walk: " + ("running" if _live(state._auto_task) else "idle")
    if state.game_proxy and hasattr(state.game_proxy,'command_snapshot'):
        commands=state.game_proxy.command_snapshot()
        status += f"\nSession: {commands['session']} · queued commands: {commands['queued']}"
    return f"=== Component Status ===\n{status}"


# ── Read-only observation, local or over the launcher's bridge ─────────

def _tail(items, count):
    items = list(items)
    return items[-count:] if count else []


async def _session_activity() -> dict:
    """Compact read-only snapshot of what this process is doing in the game."""
    from dbv.brain.state.session_journal import _plain
    gs, proxy = state.game_state, state.game_proxy
    running = sorted(n for n, t in state._action_tasks.items() if t and not t.done())
    adaptive = getattr(state, "adaptive_cavebot", None)
    observer = getattr(state, "packet_observer", None)
    return _plain({
        "pid": os.getpid(), "engine": ENGINE_LABEL,
        "connected": state.connected, "logged_in": bool(proxy and proxy.logged_in),
        "session": getattr(proxy, "session_generation", None) if proxy else None,
        "packets": {"server": proxy.packets_from_server, "client": proxy.packets_from_client} if proxy else None,
        "player": {key: getattr(gs, key, None) for key in (
            "player_name", "level", "experience", "hp", "max_hp", "mana", "max_mana", "capacity", "speed",
            "position", "in_protection_zone", "attack_target_id", "stats_updated_at")},
        "creatures_known": len(gs.creatures), "session_kills": getattr(gs, "session_kills", 0),
        "automation_paused": state.automation_paused, "pause_kind": state.automation_pause_kind,
        "running_actions": running,
        "action_log_tail": {name: _tail(state.action_logs.get(name, ()), 5) for name in running},
        "recording": {"active": state.recording_active, "name": state.recording_name,
                      "waypoints": len(state.recording_waypoints)},
        "adaptive_cavebot": adaptive.snapshot() if adaptive else None,
        "auto_walk": bool(state._auto_task and not state._auto_task.done()),
        "observer": observer.snapshot() if observer else None,
        "commands": proxy.command_snapshot() if proxy and hasattr(proxy, "command_snapshot") else None,
        "recent_server_events": [{"at": at, "kind": kind, "details": details}
                                 for at, kind, details in _tail(gs.server_events, 20)],
        "recent_kills": _tail(gs.kill_log, 10),
        "recent_combat_messages": _tail(gs.message_history.combat, 15),
        "journal": state.packet_journal.status(),
    })


async def _journal_control(action: str = "status", include_chat: bool = False) -> dict:
    proxy = state.game_proxy
    if action == "status":
        return state.packet_journal.status()
    if action == "start":
        session = proxy.session_generation if proxy is not None and proxy.logged_in else None
        return state.packet_journal.start(session, include_chat=include_chat)
    if action == "stop":
        return state.packet_journal.stop()
    if action == "dump":
        extra = {"activity": await _session_activity(), "status_report": await get_status()}
        path = state.packet_journal.dump(extra)
        return {"path": str(path), **state.packet_journal.status()}
    raise ValueError("action must be status, start, stop or dump")


_OBSERVER_HANDLERS = {"status": get_status, "activity": _session_activity, "journal": _journal_control}


def _publish_observer_bridge():
    if state.observer_bridge is not None:
        return
    from dbv.client_bridge.session_bridge import ObserverBridge
    bridge = ObserverBridge(asyncio.get_running_loop(), _OBSERVER_HANDLERS)
    try:
        if bridge.start():
            state.observer_bridge = bridge
    except OSError as error:
        log.warning("Observer bridge unavailable: %s", error)


async def _observe(op: str, **args):
    """Answer locally when this process owns the game, else ask the launcher's process."""
    if state.game_proxy is not None:
        return "local", await _OBSERVER_HANDLERS[op](**args)
    from dbv.client_bridge import session_bridge
    return "launcher", await asyncio.to_thread(session_bridge.call, op, **args)


@mcp.tool()
async def attach_status() -> dict:
    """Find the game session (this process or the launcher's) and return its status. Read-only."""
    from dbv.client_bridge import session_bridge
    if state.game_proxy is not None:
        return {"source": "local", "pid": os.getpid(), "status": await get_status()}
    try:
        info = await asyncio.to_thread(session_bridge.read_info)
        status = await asyncio.to_thread(session_bridge.call, "status")
    except session_bridge.BridgeUnavailable as error:
        return {"source": None, "attached": False, "reason": str(error)}
    return {"source": "launcher", "attached": True, "pid": info.get("pid"),
            "started_at": info.get("started_at"), "status": status}


@mcp.tool()
async def session_activity() -> dict:
    """Read-only snapshot of the running session: player, actions, cavebot, commands, recent events."""
    from dbv.client_bridge import session_bridge
    try:
        source, result = await _observe("activity")
    except session_bridge.BridgeUnavailable as error:
        return {"source": None, "error": str(error)}
    return {"source": source, **result}


@mcp.tool()
async def packet_journal(action: str = "status", include_chat: bool = False) -> dict:
    """Opt-in packet metadata journal for the current game session (no payloads or keys).

    action: status | start | stop | dump. start switches it on until logout or reconnect and
    writes .codex/journal/journal-*.jsonl; dump writes the in-memory rings to a JSON file and
    returns its path. Other players' chat text is kept only with include_chat=true.
    """
    from dbv.client_bridge import session_bridge
    if action not in {"status", "start", "stop", "dump"}:
        return {"error": "action must be status, start, stop or dump"}
    try:
        source, result = await _observe("journal", action=action, include_chat=include_chat)
    except session_bridge.BridgeUnavailable as error:
        return {"source": None, "error": str(error)}
    except RuntimeError as error:
        return {"source": "launcher", "error": str(error)}
    return {"source": source, **result}


@mcp.tool()
async def session_recovery_preview() -> dict:
    """Compare a read-only sample of waypoint and potion timers in a separate process."""
    from dbv.brain.state.recovery_preview import preview
    return await preview(state)


@mcp.tool()
async def open_dashboard(standalone: bool = False) -> str:
    """Open the local panel. Standalone mode also works before game login."""
    if state.game_proxy is None:
        if not standalone:
            return "ERROR: Bot session is not started. Start from the character list first."
        from dbv.mcp_server import dashboard_api
        if not dashboard_api.start_api(asyncio.get_running_loop(), state, panel_only=True):
            return "ERROR: Could not open the local dashboard API."
    _launch_dashboard()
    if _dashboard_running('window'):
        return "Panel opened. Game connection and actions unchanged."
    return "ERROR: Could not open panel. Check the installed dashboard and pywebview."


@mcp.tool()
async def emergency_stop() -> str:
    """Latch off all user actions and cavebot. Relay and read-only status remain available."""
    return await _async_emergency_stop()


@mcp.tool()
async def unlock_automation() -> str:
    """Clear emergency latch, without restarting any action or route."""
    return await _async_unlock_automation()


@mcp.tool()
async def configure_potions(config: dict) -> dict:
    """Configure ordered healing rules (HP/Ki, item 10161/10160/8465, threshold, jitter, interval, cooldown, mode). Legacy potion fields remain supported until rules are saved. Does not enable it."""
    return await _async_update_action_config("auto_potions", config)


@mcp.tool()
async def read_message_history(channel: str, limit: int = 50, before: int | None = None) -> dict:
    """Read one page of general or combat history from this game session, oldest first.
    Use next_before as before for the preceding page. Timestamps are Unix seconds.
    """
    gs = state.game_state
    return {"session_id": gs.session_id, **gs.message_history.page(channel, limit, before)}


@mcp.tool()
async def potion_diagnostics(enabled: bool | None = None) -> dict:
    """Observe manual potion use for up to 5 minutes; never sends item-use packets."""
    if enabled is not None:
        return await _async_potion_diagnostics(enabled)
    return state.potion_diagnostics.snapshot()


@mcp.tool()
async def logout() -> str:
    """Log the character out."""
    if not state.connected:
        return NOT_CONNECTED
    await _send(wire.build_logout_packet())
    return "Logout packet sent."


# ── route recording ─────────────────────────────────────────────────

@mcp.tool()
async def start_recording(name: str) -> str:
    """Start recording a route: walks, item uses, stairs and doors. name: letters, digits, - and _."""
    if not state.connected:
        return NOT_CONNECTED
    refusal = cavebot.start_recording(state, name)
    if refusal:
        return f"Error: {refusal}"
    return f"Recording '{name}' started. Walk around, use doors/stairs/ladders. Call stop_recording when done."


@mcp.tool()
async def stop_recording() -> str:
    """Stop recording and save the route."""
    if not state.recording_active:
        return "No recording in progress."
    recording = cavebot.stop_recording(state)
    if recording is None:
        return "Recording stopped (no data saved)."
    return f"Recording '{recording['name']}' saved with {len(recording.get('waypoints', []))} waypoint(s)."


@mcp.tool()
async def list_recordings() -> str:
    """List the saved routes."""
    saved = cavebot.list_recordings()
    if not saved:
        return "No recordings found. Use start_recording to create one."
    return "Saved recordings:\n" + "\n".join(
        f"  {row['name']}  ({row['count']} waypoints)  created: {row['created_at']}" for row in saved)


@mcp.tool()
async def delete_recording(name: str) -> str:
    """Delete a saved route by name."""
    deleted = cavebot.delete_recording(name)
    return f"Recording '{name}' deleted." if deleted else f"Recording '{name}' not found."


# ── Entry point ─────────────────────────────────────────────────────

async def serve_with_tray_exit():
    from mcp.server.stdio import stdio_server
    with tray_input("utf-8") as (exit_file, lines):
        state.tray_exit_file = exit_file
        server = mcp._mcp_server
        async with stdio_server(stdin=lines) as (reader, writer):
            await server.run(reader, writer, server.create_initialization_options())


if __name__ == "__main__":
    state.tray_exit_file = os.environ.pop(EXIT_FILE_ENV, None)
    if state.tray_exit_file:
        # The standalone launcher owns stdin and closes this child on exit.
        mcp.run(transport="stdio")
    else:
        asyncio.run(serve_with_tray_exit())
