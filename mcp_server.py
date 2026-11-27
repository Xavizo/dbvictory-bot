"""
DBVictory Bot — MCP Server

Exposes the bot's actions as MCP tools so Codex or Claude Code can control the
game character through natural language.

Transport: stdio  (stdout = JSON-RPC, all logging → stderr)
"""

import asyncio
import atexit
import collections
from datetime import datetime
import importlib
import importlib.util
import json
import logging
import subprocess
import sys
import os
import time as _time
import traceback
from pathlib import Path

# ── Logging to stderr (stdout reserved for MCP JSON-RPC) ────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("mcp_server")


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dbv_revision import checkout_revision, revision_label, version_for
from session_startup import LoginStartup
from session_observations import session_observations, note_observation_gap, publish_semantic_result


def _engine_label(root, revision=None):
    started = datetime.now().astimezone().isoformat(sep=" ", timespec="seconds")
    if revision is None:
        revision = checkout_revision(root)
    label = revision_label(revision, version_for(root, revision))
    return f"Silnik {label} · start {started}"


# Capture once, before loading the engine. Reopening the panel reuses this identity.
ENGINE_ROOT = Path(__file__).resolve().parent
ENGINE_REVISION = checkout_revision(ENGINE_ROOT)
ENGINE_LABEL = _engine_label(ENGINE_ROOT, ENGINE_REVISION)

from mcp.server.fastmcp import FastMCP
from tray_exit import EXIT_FILE_ENV, tray_input

from proxy import OTProxy
from game_state import GameState, parse_server_packet, scan_packet
from transport_control import LocalTransportControl
from protocol import (
    Direction,
    build_walk_packet,
    build_turn_packet,
    build_say_packet,
    build_attack_packet,
    build_follow_packet,
    build_stop_walk_packet,
    build_ping_packet,
    build_use_item_packet,
    build_move_item_packet,
    build_look_packet,
    build_set_fight_modes_packet,
    PacketWriter,
    ClientOpcode,
    ServerOpcode,
    PacketReader,
)
from constants import SERVER_HOST, LOGIN_PORT, GAME_PORT
import cavebot
from potions import PotionScheduler, PotionDiagnostics, ITEMS as HEALING_ITEMS, validate_config as validate_potion_config
from barrier_spell import validate_config as validate_barrier_config
from barrier_band import BandScheduler, validate_config as validate_band_config
from dashboard_security import SESSION_TOKEN

# ── Constants ───────────────────────────────────────────────────────
SETTINGS_FILE = Path(__file__).parent / "bot_settings.json"
ACTIONS_DIR = Path(__file__).parent / "actions"
INTERNAL_ACTIONS = {"dll_bridge"}


def load_settings() -> dict:
    """Load settings from disk, returning defaults if file doesn't exist."""
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"Failed to load settings: {e}")
    return {"actions": {}}


def save_settings(settings: dict) -> None:
    """Persist settings to disk."""
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


# ── Global state ────────────────────────────────────────────────────

MAX_ACTION_LOGS = 200


class BotState:
    """Holds proxy references and connection status."""

    def __init__(self):
        self.local_transport = LocalTransportControl()
        self.login_proxy: OTProxy | None = None
        self.game_proxy: OTProxy | None = None
        self.packet_observer = None
        from session_journal import SessionJournal
        self.packet_journal = SessionJournal()
        self.observer_bridge = None
        self.ready: bool = False
        self._auto_task: asyncio.Task | None = None
        self.login_startup = LoginStartup()
        self._proxy_tasks: list[asyncio.Task] = []
        self.settings: dict = load_settings()
        self.automation_paused = bool(self.settings.get("automation_paused", False))
        self.automation_pause_kind = self.settings.get("automation_pause_kind", "emergency") if self.automation_paused else None
        self._stopping_tasks = []
        self._emergency_stopping = False
        self.band_scheduler = BandScheduler()
        self.barrier_scheduler = None
        from speed_up import SpeedScheduler
        self.speed_scheduler = SpeedScheduler()
        self.potion_scheduler = PotionScheduler()
        self.potion_diagnostics = PotionDiagnostics()
        self._action_tasks: dict[str, asyncio.Task] = {}
        self._action_completed: set[str] = set()
        self._adaptive_prepare_token = None
        self.game_state: GameState = GameState()
        from game_alerts import AlertCenter
        self.alert_center = AlertCenter(self.settings.get("alerts"))
        self.alert_center.set_automation_paused(self.automation_paused)
        from bot_profiles import ProfileStore
        self.profile_store = ProfileStore()
        self.action_logs: dict[str, collections.deque] = {}

        # Cavebot recording state
        self.recording_active: bool = False
        self.recording_name: str = ""
        self.recording_waypoints: list[dict] = []
        self.recording_start_pos: tuple = (0, 0, 0)
        self.recording_start_time: float = 0
        self._recording_callback = None


    @property
    def connected(self) -> bool:
        return self.ready and self.game_proxy is not None


state = BotState()


async def _async_toggle_all():
    if state._emergency_stopping:
        return "Trwa zatrzymywanie akcji."
    if state.automation_paused:
        return await _async_resume_automation()
    return await _async_pause_automation()


async def _async_profiles(body):
    from bot_profiles import bot_config, validate_bot, applied_bot_settings, require_stopped
    from cavebot_combat import validate_combat
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
    from game_alerts import validate_config
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


# ── Bot context (passed to action scripts as `bot`) ────────────────

class BotContext:
    """API surface available to action scripts via the `bot` parameter."""

    def __init__(self, action_name: str = ""):
        self._log = logging.getLogger("action")
        self._action_name = action_name
        from session_lifetime import SessionLease
        self._session = SessionLease(state)

    # ── connection ──────────────────────────────────────────────────
    @property
    def is_connected(self) -> bool:
        return state.connected

    # ── low-level ───────────────────────────────────────────────────
    async def inject_to_server(self, packet: bytes, *, guard=None):
        self.last_receipt=None
        if state.automation_paused or not self._session.valid():
            raise asyncio.CancelledError()
        if state.game_proxy:
            from command_delivery import delivery
            from session_lifetime import SessionLease, current_task, task_alive
            lease=SessionLease(state); owner=current_task()
            def allowed():
                return (self._session.valid() and lease.valid() and task_alive(owner)
                        and not state.automation_paused and (guard is None or guard()))
            self.last_receipt=await state.game_proxy.inject_to_server(packet,guard=allowed)
            return await delivery(self.last_receipt)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        if state.automation_paused and self._action_name not in INTERNAL_ACTIONS:
            raise asyncio.CancelledError()

    # ── convenience helpers ─────────────────────────────────────────
    async def use_item_in_container(
        self, item_id: int, container: int = 0, slot: int = 0
    ) -> None:
        """Use an item from an open container.

        Args:
            item_id: The item's type/sprite ID.
            container: Container ID (64=first backpack, 65=second, ...).
                       Pass raw ID as captured from packets.
            slot: Slot position within the container.
        """
        pkt = build_use_item_packet(0xFFFF, container, slot, item_id, slot, 0)
        await self.inject_to_server(pkt)

    async def use_item_on_map(
        self, x: int, y: int, z: int, item_id: int,
        stack_pos: int = 0, index: int = 0,
    ) -> None:
        pkt = build_use_item_packet(x, y, z, item_id, stack_pos, index)
        await self.inject_to_server(pkt)

    async def say(self, text: str, *, guard=None):
        if guard is None:
            await self.inject_to_server(build_say_packet(text))
            return None
        return await self.inject_to_server(build_say_packet(text), guard=guard)

    async def walk(self, direction: str, steps: int = 1, delay: float = 0.3) -> None:
        d = _resolve_direction(direction)
        if d is None:
            return
        for _ in range(steps):
            await self.inject_to_server(build_walk_packet(d))
            if steps > 1:
                await self.sleep(delay)

    def log(self, msg: str) -> None:
        self._log.info(msg)
        if self._action_name:
            buf = state.action_logs.get(self._action_name)
            if buf is None:
                buf = collections.deque(maxlen=MAX_ACTION_LOGS)
                state.action_logs[self._action_name] = buf
            ts = _time.strftime("%H:%M:%S")
            buf.append(f"[{ts}] {msg}")

    # ── game state properties ────────────────────────────────────────
    @property
    def hp(self) -> int:
        return state.game_state.hp

    @property
    def max_hp(self) -> int:
        return state.game_state.max_hp

    @property
    def mana(self) -> int:
        return state.game_state.mana

    @property
    def max_mana(self) -> int:
        return state.game_state.max_mana

    @property
    def level(self) -> int:
        return state.game_state.level

    @property
    def experience(self) -> int:
        return state.game_state.experience

    @property
    def capacity(self) -> int:
        return state.game_state.capacity

    @property
    def speed(self) -> int:
        return state.game_state.speed

    @property
    def player_icons(self) -> int:
        return state.game_state.player_icons

    @property
    def position(self) -> tuple[int, int, int]:
        return state.game_state.position

    @property
    def player_id(self) -> int:
        return state.game_state.player_id

    @property
    def creatures(self) -> dict:
        return state.game_state.creatures

    @property
    def messages(self):
        return state.game_state.messages

    # ── advanced (for packet sniffing / hooks) ──────────────────────
    @property
    def game_proxy(self):
        return state.game_proxy

    @property
    def state(self):
        return state


bot_ctx = BotContext()


# ── Action loader / runner ─────────────────────────────────────────

def _load_action_module(name: str):
    """Dynamically load (or reload) actions/<name>.py and return the module."""
    if not all(c.isalnum() or c == '_' for c in name):
        return None
    path = ACTIONS_DIR / f"{name}.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(f"actions.{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _start_action(name: str) -> str | None:
    """Load an action script and launch its run(bot) as a background task.
    Returns an error string on failure, None on success."""
    if name=="adaptive_cavebot":
        controller=getattr(state,"adaptive_cavebot",None)
        if controller is None or controller.status not in {"ready","paused"} or not controller.path:
            return "Wybierz zapisaną trasę i użyj Start w Cavebot."
        try:
            controller.guard()
            if not controller.combat_only and list(state.game_state.position)!=controller.path[controller.index]:return "Pozycja zmieniła się podczas pauzy. Uruchom od początku trasy."
        except ValueError as exc:return str(exc)
    if state.automation_paused and name not in INTERNAL_ACTIONS:
        return "Emergency pause is active. Unlock it before enabling actions."
    if name == "speed_up":
        try:
            from speed_up import validate_config
            validate_config(state.settings.get("actions", {}).get(name, {}))
        except ValueError as exc: return str(exc)
    if name == "auto_potions":
        try:
            validate_potion_config(state.settings.get("actions", {}).get(name, {}), require_cooldowns=True)
        except ValueError as exc:
            return str(exc)
    if name == "barrier_spell":
        try: validate_barrier_config(state.settings.get("actions", {}).get(name, {}))
        except ValueError as exc: return str(exc)
    if name == "auto_barrier_band":
        try: validate_band_config(state.settings.get("actions", {}).get(name, {}))
        except ValueError as exc: return str(exc)
    # Don't double-start
    if name in state._action_tasks and not state._action_tasks[name].done():
        return f"'{name}' is already running."

    mod = _load_action_module(name)
    if mod is None:
        return f"actions/{name}.py not found."
    if not hasattr(mod, "run") or not asyncio.iscoroutinefunction(mod.run):
        return f"actions/{name}.py has no async def run(bot)."

    ctx = BotContext(action_name=name)
    state._action_completed.discard(name)

    async def _wrapper():
        try:
            log.info(f"[action:{name}] Started")
            if name not in INTERNAL_ACTIONS:
                session = state.game_state
                while (state.connected and state.game_state is session
                       and (not session.player_id or (session.client_state_required
                            and not session.client_player_id))):
                    await asyncio.sleep(.05)
                if not state.connected or state.game_state is not session:
                    return
            await mod.run(ctx)
            state._action_completed.add(name)
        except asyncio.CancelledError:
            state._action_completed.discard(name)
            log.info(f"[action:{name}] Stopped")
        except Exception:
            state._action_completed.discard(name)
            log.error(f"[action:{name}] Crashed:\n{traceback.format_exc()}")
        finally:
            if name=="adaptive_cavebot" and not state.automation_paused:
                state.settings.setdefault("actions",{}).setdefault(name,{})["enabled"]=False

    state._action_tasks[name] = asyncio.create_task(_wrapper())
    return None


def _stop_action(name: str) -> bool:
    """Cancel a running action task. Returns True if it was running."""
    if name=="adaptive_cavebot":state._adaptive_prepare_token=None
    if name=="adaptive_cavebot" and getattr(state,"adaptive_cavebot",None) and state.adaptive_cavebot.path:
        state.adaptive_cavebot.pause("Nawigacja wstrzymana.")
    task = state._action_tasks.pop(name, None)
    if task and not task.done():
        task.cancel()
        return True
    return False


def _start_all_enabled_actions() -> int:
    """Start background tasks for all enabled actions. Returns count started."""
    count = 0
    # Always start internal actions
    for name in INTERNAL_ACTIONS:
        err = _start_action(name)
        if err:
            log.warning(f"[action:{name}] Could not auto-start internal: {err}")
        else:
            count += 1
    # Start user-enabled actions
    for name, cfg in state.settings.get("actions", {}).items():
        if name=="adaptive_cavebot":
            cfg["enabled"]=False
            continue  # Navigation never resumes automatically after login.
        if name in INTERNAL_ACTIONS:
            continue  # already started above
        if cfg.get("enabled", False):
            err = _start_action(name)
            if err:
                log.warning(f"[action:{name}] Could not auto-start: {err}")
            else:
                count += 1
    return count


def _discover_actions() -> list[str]:
    """Return sorted list of action names from .py files in actions/ dir."""
    if not ACTIONS_DIR.exists():
        return []
    return sorted(p.stem for p in ACTIONS_DIR.glob("*.py") if p.stem != "__init__")


# ── Shared async functions (used by MCP tools AND dashboard_api) ────

async def _async_toggle_action(name: str, enabled: bool) -> str:
    """Enable or disable an action. Returns a status message."""
    if enabled and name=="adaptive_cavebot":
        return "Error: V2 uruchamiaj przez Start zapisanej trasy."
    if name in INTERNAL_ACTIONS:
        return f"Action '{name}' is an internal service and cannot be toggled."
    if enabled and state.automation_paused:
        return "Error: Emergency pause is active. Unlock it first."
    if enabled and name == "speed_up":
        try:
            from speed_up import validate_config
            validate_config(state.settings.get("actions", {}).get(name, {}))
        except ValueError as exc: return f"Error: {exc}"
    if enabled and name == "auto_potions":
        try:
            validate_potion_config(state.settings.get("actions", {}).get(name, {}), require_cooldowns=True)
        except ValueError as exc:
            return f"Error: {exc}"
    if enabled and name == "barrier_spell":
        try: validate_barrier_config(state.settings.get("actions", {}).get(name, {}))
        except ValueError as exc: return f"Error: {exc}"
    if enabled and name == "auto_barrier_band":
        try: validate_band_config(state.settings.get("actions", {}).get(name, {}))
        except ValueError as exc: return f"Error: {exc}"
    path = ACTIONS_DIR / f"{name}.py"
    if not path.exists():
        return f"actions/{name}.py not found."

    actions = state.settings.setdefault("actions", {})
    actions.setdefault(name, {})["enabled"] = enabled
    save_settings(state.settings)

    if enabled:
        if state.connected:
            err = _start_action(name)
            if err:
                return f"Enabled but failed to start: {err}"
            return f"Action '{name}' enabled and started."
        return f"Action '{name}' enabled. It will auto-start when the bot connects."
    else:
        was_running = _stop_action(name)
        return f"Action '{name}' disabled{' and stopped' if was_running else ''}."


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
        from speed_up import validate_config
        cfg = validate_config(cfg)
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
        task = state._action_tasks.get(name)
        if task and not task.done():
            tasks.append(task)
        _stop_action(name)
    if state._auto_task and not state._auto_task.done():
        state._auto_task.cancel()
        tasks.append(state._auto_task)
    state._auto_task = None
    state._stopping_tasks = tasks
    state.game_state.lure_active = False
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


async def _async_adaptive_preview(config=None) -> dict:
    from cavebot_combat import validate_combat,eligible_monsters,choose_target,target_policy,attack_distance_ok
    from combat_spell_conditions import evaluate_spells
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
    from adaptive_cavebot import AdaptiveCavebot,MANUAL_MOVES
    from route_identity import same_route
    name="adaptive_cavebot";controller=getattr(state,name,None)
    task=state._action_tasks.get(name)
    if command=='configure_combat':
        from cavebot_combat import validate_combat
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
                await state.game_proxy.inject_to_server(build_stop_walk_packet())
        state.settings.setdefault("actions",{}).setdefault(name,{})["enabled"]=False
        if controller and command=="stop":controller.stop(preserve_progress=True)
    else:raise ValueError("Nieznane polecenie V2.")
    return controller.snapshot() if controller else {"status":"idle","reason":"V2 jest wyłączony."}


async def _async_restart_action(name: str) -> str:
    """Stop and re-start an action. Returns a status message."""
    if name=="adaptive_cavebot":
        return "Użyj Start/Wznów w Cavebot."
    if name == "auto_barrier_band":
        try: validate_band_config(state.settings.get("actions", {}).get(name, {}))
        except ValueError as exc: return f"Error: {exc}"
    path = ACTIONS_DIR / f"{name}.py"
    if not path.exists():
        return f"actions/{name}.py not found."

    _stop_action(name)
    await asyncio.sleep(0.1)
    err = _start_action(name)
    if err:
        return f"Failed to restart: {err}"
    return f"Action '{name}' restarted (code reloaded from disk)."


_dashboard_launched = False
_dashboard_processes: dict[str, subprocess.Popen] = {}
DASHBOARD_PORT = 4747


def _dashboard_running(name: str) -> bool:
    process = _dashboard_processes.get(name)
    return process is not None and process.poll() is None


def _stop_dashboard() -> None:
    """Stop only process trees started by this MCP server, never other apps."""
    global _dashboard_launched
    for process in _dashboard_processes.values():
        if process.poll() is not None:
            continue
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    capture_output=True, timeout=10,
                )
            else:
                process.terminate()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            log.warning("Could not stop dashboard child PID %s", process.pid)
    _dashboard_processes.clear()
    _dashboard_launched = False


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
    """Check if a process with the given name is running (Windows)."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        return name.lower() in result.stdout.lower()
    except Exception:
        return False


def _check_port_listening(port: int) -> bool:
    """Probe loopback without shelling out to netstat."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _check_pipe_exists() -> bool:
    """Check if the dbvbot named pipe exists (DLL loaded and running)."""
    try:
        bridge = getattr(state.game_state, "dll_bridge", None)
        if bridge is not None:
            return bridge.connected
        from dll_bridge import DllBridge
        return DllBridge().pipe_exists()
    except Exception:
        return False


def _build_status_report() -> str:
    """Build a multi-line status report of all components."""
    lines = []

    # MCP Server — always OK if we're here
    lines.append("[OK] MCP Server: running")
    lines.append(ENGINE_LABEL)

    # Game client
    client_running = _check_process_running("dbvStart.exe")
    if client_running:
        lines.append("[OK] Game Client: dbvStart.exe detected")
    else:
        lines.append("[!!] Game Client: dbvStart.exe NOT found")

    # Proxy
    if state.ready and state.game_proxy:
        svr = state.game_proxy.packets_from_server
        cli = state.game_proxy.packets_from_client
        has_writers = (state.game_proxy.server_writer is not None
                       and state.game_proxy.client_writer is not None)
        logged_in = state.game_proxy.logged_in
        if logged_in and has_writers:
            lines.append(f"[OK] Proxy: connected (server={svr} client={cli} packets)")
        else:
            lines.append(f"[!!] Proxy: BROKEN (logged_in={logged_in} writers={has_writers} "
                         f"server={svr} client={cli})")
    elif state.game_proxy:
        lines.append("[..] Proxy: started, waiting for login")
    else:
        lines.append("[--] Proxy: not started")

    # DLL pipe
    pipe_ok = _check_pipe_exists()
    if pipe_ok:
        lines.append("[OK] DLL Pipe: dbvbot pipe available")
    else:
        lines.append("[--] DLL Pipe: not detected (dll_bridge action will inject)")

    # Dashboard / Electron
    dashboard_listening = _check_port_listening(DASHBOARD_PORT)
    electron_running = _dashboard_running("electron")
    if electron_running and dashboard_listening:
        lines.append(f"[OK] Dashboard: Electron running on port {DASHBOARD_PORT}")
    elif dashboard_listening:
        lines.append(f"[OK] Dashboard: server on port {DASHBOARD_PORT} (Electron starting...)")
    elif _dashboard_launched:
        lines.append("[..] Dashboard: launch requested, waiting for startup...")
    else:
        lines.append("[--] Dashboard: not launched")

    # Player info
    gs = state.game_state
    if gs.player_id:
        pos = gs.position
        lines.append(f"[OK] Player: id={gs.player_id:#010x} pos=({pos[0]},{pos[1]},{pos[2]}) "
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
    """Launch the built, loopback-only dashboard and its authenticated Electron UI."""
    global _dashboard_launched
    import shutil
    dashboard_dir = Path(__file__).parent / "dashboard"
    if os.name != "nt":
        log.warning("The game dashboard requires Windows.")
        return
    if not (dashboard_dir / ".next" / "BUILD_ID").exists():
        log.warning("Build the dashboard first: cd dashboard && npm ci && npm run build")
        return
    npm = shutil.which("npm.cmd")
    bundled = Path(__file__).parent / "runtime" / "node"
    if npm is None and (bundled / "npm.cmd").is_file() and (bundled / "node.exe").is_file():
        # A downloaded release ships Node.js; Codex or Claude may start MCP without it on PATH.
        npm = str(bundled / "npm.cmd")
        os.environ["PATH"] = str(bundled) + os.pathsep + os.environ.get("PATH", "")
    if npm is None:
        log.warning("npm.cmd not found on PATH — cannot launch dashboard.")
        return
    if not _dashboard_running("next") and _check_port_listening(DASHBOARD_PORT):
        log.error("Dashboard port %s is occupied by another process; refusing to reuse it.", DASHBOARD_PORT)
        return
    try:
        for name, script in (("next", "start"), ("electron", "electron:only")):
            if _dashboard_running(name):
                continue
            if name == "next":
                _activate_staged_dashboard(dashboard_dir)
            env = os.environ.copy()
            env.pop("DBV_DASHBOARD_TOKEN", None)
            env.pop(EXIT_FILE_ENV, None)
            if name == "electron":
                if getattr(state, "tray_exit_file", None):
                    env[EXIT_FILE_ENV] = str(state.tray_exit_file)
                env["DBV_DASHBOARD_TOKEN"] = SESSION_TOKEN
                env["DBV_ENGINE_LABEL"] = ENGINE_LABEL
            _dashboard_processes[name] = subprocess.Popen(
                f'"{npm}" run {script}', cwd=str(dashboard_dir), shell=True,
                env=env, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        _dashboard_launched = True
    except Exception as e:
        log.warning("Failed to launch dashboard: %s", e)
        _stop_dashboard()


# ── MCP Server ──────────────────────────────────────────────────────

mcp = FastMCP(
    "dbvictory-bot",
    instructions=(
        "DBVictory game bot. Inspect get_status and list_actions first. "
        "Call start_bot only when the user wants to connect to the Windows game client. "
        "It patches client memory and launches local proxies. Login happens in the "
        "game client; never request or log credentials. Enable only requested actions."
    ),
)

_VBOT_KNOWLEDGE = Path(__file__).resolve().parent / "docs" / "vbot"


@mcp.resource("dbvictory://knowledge/vbot/architecture", mime_type="text/markdown")
def vbot_architecture() -> str:
    """Audyt vBot 4.8, kontrakt z OTClientem i cykl sesji."""
    return (_VBOT_KNOWLEDGE / "architecture.md").read_text(encoding="utf-8")


@mcp.resource("dbvictory://knowledge/vbot/modules", mime_type="text/markdown")
def vbot_modules() -> str:
    """Pełny katalog plików, funkcji i zależności vBota."""
    return (_VBOT_KNOWLEDGE / "modules.md").read_text(encoding="utf-8")


@mcp.resource("dbvictory://knowledge/vbot/decisions", mime_type="text/markdown")
def vbot_decisions() -> str:
    """Decyzje dla DBVictory, dowody, pomiary i kryteria wdrożenia."""
    return (_VBOT_KNOWLEDGE / "decisions.md").read_text(encoding="utf-8")


# ── Direction helpers ───────────────────────────────────────────────

DIR_MAP = {
    "n": Direction.NORTH, "north": Direction.NORTH,
    "s": Direction.SOUTH, "south": Direction.SOUTH,
    "e": Direction.EAST,  "east": Direction.EAST,
    "w": Direction.WEST,  "west": Direction.WEST,
    "ne": Direction.NORTHEAST, "northeast": Direction.NORTHEAST,
    "se": Direction.SOUTHEAST, "southeast": Direction.SOUTHEAST,
    "sw": Direction.SOUTHWEST, "southwest": Direction.SOUTHWEST,
    "nw": Direction.NORTHWEST, "northwest": Direction.NORTHWEST,
}

TURN_DIR_MAP = {
    "n": Direction.NORTH, "north": Direction.NORTH,
    "s": Direction.SOUTH, "south": Direction.SOUTH,
    "e": Direction.EAST,  "east": Direction.EAST,
    "w": Direction.WEST,  "west": Direction.WEST,
}


def _resolve_direction(direction: str, allow_diagonal: bool = True) -> Direction | None:
    d = direction.strip().lower()
    if allow_diagonal:
        return DIR_MAP.get(d)
    return TURN_DIR_MAP.get(d)


# ── Bot reset / hot-reload ───────────────────────────────────────────

async def _reset_bot() -> str:
    """Tear down the running bot, hot-reload modules, and reset state."""
    global _dashboard_launched
    import game_state as _gs_mod

    log.info("=== RESETTING BOT ===")
    pending_start = state.login_startup.cancel()
    if pending_start is not None:
        await asyncio.gather(pending_start, return_exceptions=True)

    state._adaptive_prepare_token=None
    state.packet_journal.stop("reset")
    # 1. Stop all running actions
    history = getattr(state.game_state, "movement_history", None)
    if history:history.close()
    for name in list(state._action_tasks):
        _stop_action(name)
    if getattr(state,"adaptive_cavebot",None):state.adaptive_cavebot.stop("Sesja została zresetowana.")
    if getattr(state,"packet_observer",None):
        await state.packet_observer.close()
        state.packet_observer = None
    await asyncio.sleep(0.1)

    # 2. Close listening servers FIRST (prevents old server accepting new clients)
    for proxy in (state.game_proxy, state.login_proxy):
        if proxy:
            if hasattr(proxy, 'close_server'):
                proxy.close_server()
            # Also close client/server sockets (unblocks relay reads)
            # Must await wait_closed() on Windows to ensure TCP sockets are
            # actually released, otherwise the game client stays connected
            # to a dead socket and never reconnects to the new proxy.
            for writer_attr in ('client_writer', 'server_writer'):
                w = getattr(proxy, writer_attr, None)
                if w:
                    try:
                        w.close()
                        await asyncio.wait_for(w.wait_closed(), timeout=1.0)
                    except Exception:
                        pass

    # 2b. Cancel active connection handler tasks (now unblocked by socket close)
    tasks_to_await = []
    for proxy in (state.game_proxy, state.login_proxy):
        if proxy:
            ct = getattr(proxy, '_connection_task', None)
            if ct and not ct.done():
                ct.cancel()
                tasks_to_await.append(ct)

    # 2c. Cancel proxy tasks (serve_forever)
    for task in state._proxy_tasks:
        if not task.done():
            task.cancel()
            tasks_to_await.append(task)
    state._proxy_tasks.clear()

    # 2d. Actually await all cancelled tasks to ensure they're fully stopped
    for t in tasks_to_await:
        try:
            await asyncio.wait_for(t, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass

    # 3. Nil out proxy references (sockets already closed above)
    for proxy in (state.game_proxy, state.login_proxy):
        if proxy:
            proxy.client_writer = None
            proxy.server_writer = None
            proxy.logged_in = False

    await asyncio.sleep(1.0)  # Windows needs time for port release

    # 4. Stop only our dashboard process trees.
    _stop_dashboard()

    # 5. Hot-reload Python modules
    try:
        importlib.reload(_gs_mod)
        log.info("Reloaded game_state.py")
    except Exception as e:
        log.warning(f"Failed to reload game_state: {e}")
    # Keep dashboard_api alive: its loopback listeners, credentials and client
    # registry belong to this MCP process, not to one game connection.
    import cavebot as _cb_mod
    try:
        importlib.reload(_cb_mod)
        log.info("Reloaded cavebot.py")
    except Exception as e:
        log.warning(f"Failed to reload cavebot: {e}")

    # Re-import after reload (use the already-reloaded module refs)
    globals()['scan_packet'] = _gs_mod.scan_packet
    globals()['parse_server_packet'] = _gs_mod.parse_server_packet
    from game_state import GameState

    # 6. Reset global state
    state.login_proxy = None
    state.game_proxy = None
    state.ready = False
    state._auto_task = None
    state._action_tasks.clear()
    state.game_state = GameState()
    state.settings = load_settings()
    state.automation_paused = bool(state.settings.get("automation_paused", False))
    state.alert_center.set_automation_paused(state.automation_paused)
    state.automation_pause_kind = state.settings.get("automation_pause_kind","emergency") if state.automation_paused else None
    state._stopping_tasks = []

    # Reset cavebot state
    state.recording_active = False
    state.recording_name = ""
    state.recording_waypoints = []
    state.recording_start_pos = (0, 0, 0)
    state.recording_start_time = 0
    state._recording_callback = None

    log.info("Bot reset complete.")
    return "Bot reset. Call start_bot to reconnect."


# ── Force-close game TCP connections ─────────────────────────────────

def _close_game_connections(pid: int) -> int:
    """Force-close active TCP connections belonging to the game process.

    Uses the Windows SetTcpEntry API to reset TCP connections, causing the
    game client to disconnect and return to the login screen.  Requires
    admin privileges (which we already have since pymem works).

    Returns the number of connections closed.
    """
    import ctypes
    import ctypes.wintypes
    import struct as _struct
    import socket

    # MIB_TCPROW_OWNER_PID for GetExtendedTcpTable
    class MIB_TCPROW_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("dwState", ctypes.wintypes.DWORD),
            ("dwLocalAddr", ctypes.wintypes.DWORD),
            ("dwLocalPort", ctypes.wintypes.DWORD),
            ("dwRemoteAddr", ctypes.wintypes.DWORD),
            ("dwRemotePort", ctypes.wintypes.DWORD),
            ("dwOwningPid", ctypes.wintypes.DWORD),
        ]

    # MIB_TCPROW for SetTcpEntry (no PID field)
    class MIB_TCPROW(ctypes.Structure):
        _fields_ = [
            ("dwState", ctypes.wintypes.DWORD),
            ("dwLocalAddr", ctypes.wintypes.DWORD),
            ("dwLocalPort", ctypes.wintypes.DWORD),
            ("dwRemoteAddr", ctypes.wintypes.DWORD),
            ("dwRemotePort", ctypes.wintypes.DWORD),
        ]

    TCP_TABLE_OWNER_PID_ALL = 5
    AF_INET = 2
    MIB_TCP_STATE_DELETE_TCB = 12

    iphlpapi = ctypes.windll.iphlpapi

    # First call to get buffer size
    buf_size = ctypes.wintypes.DWORD(0)
    iphlpapi.GetExtendedTcpTable(None, ctypes.byref(buf_size), False,
                                  AF_INET, TCP_TABLE_OWNER_PID_ALL, 0)

    buf = ctypes.create_string_buffer(buf_size.value)
    ret = iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(buf_size), False,
                                        AF_INET, TCP_TABLE_OWNER_PID_ALL, 0)
    if ret != 0:
        log.warning(f"GetExtendedTcpTable failed: {ret}")
        return 0

    # Parse the table
    num_entries = _struct.unpack_from('<I', buf.raw, 0)[0]
    row_size = ctypes.sizeof(MIB_TCPROW_OWNER_PID)
    closed = 0

    for i in range(num_entries):
        offset = 4 + i * row_size
        row_data = buf.raw[offset:offset + row_size]
        if len(row_data) < row_size:
            break

        row = MIB_TCPROW_OWNER_PID.from_buffer_copy(row_data)

        if row.dwOwningPid != pid:
            continue

        # Convert port from network byte order
        remote_port = socket.ntohs(row.dwRemotePort & 0xFFFF)
        local_port = socket.ntohs(row.dwLocalPort & 0xFFFF)
        remote_ip = socket.inet_ntoa(_struct.pack('<I', row.dwRemoteAddr))

        # Close ALL connections on game/login server ports (including localhost proxy).
        # This is called after _reset_bot() tears down the old proxy and before
        # new proxies are created, so closing localhost connections is safe and
        # necessary — otherwise the game client stays connected to a dead socket.
        if remote_port in (GAME_PORT, LOGIN_PORT):
            log.info(f"Closing game connection: {remote_ip}:{remote_port} (state={row.dwState})")
            close_row = MIB_TCPROW()
            close_row.dwState = MIB_TCP_STATE_DELETE_TCB
            close_row.dwLocalAddr = row.dwLocalAddr
            close_row.dwLocalPort = row.dwLocalPort
            close_row.dwRemoteAddr = row.dwRemoteAddr
            close_row.dwRemotePort = row.dwRemotePort
            result = iphlpapi.SetTcpEntry(ctypes.byref(close_row))
            if result == 0:
                closed += 1
                log.info(f"  -> Closed successfully")
            else:
                log.warning(f"  -> SetTcpEntry failed: {result}")

    return closed


# ── Tools ───────────────────────────────────────────────────────────

@mcp.tool()
async def start_bot() -> str:
    """Patch the game client's memory and start the login + game proxies.

    The game client (dbvStart.exe) must already be running and sitting at
    the login screen.  This tool patches the server IP in memory to
    redirect traffic through the local proxy, then waits for the player
    to log in through the game client.
    """
    if os.name != "nt":
        return "ERROR: start_bot requires native Windows and the running DBVictory client."

    # Fail before any patch, socket closure or session teardown on unknown builds.
    try:
        from passive_map_win32 import ReadOnlyClient
        verified_client = ReadOnlyClient()
        try:
            client_profile = verified_client.profile
            observer = client_profile.observer
        finally:
            verified_client.close()
        if observer and not Path(__file__).with_name("dll").joinpath(client_profile.observer_dll).is_file():
            return f"ERROR: Build dll/{client_profile.observer_dll} before starting this client version."
    except Exception as exc:
        return f"ERROR: Client compatibility check failed: {exc}"

    if state.ready or state.game_proxy or state.login_proxy:
        # Auto-reset: tear down everything and continue to fresh start
        log.info("Bot already running — auto-resetting...")
        await _reset_bot()

    # ── Patch client memory ─────────────────────────────────────────
    try:
        import pymem
    except ImportError:
        return "ERROR: pymem is not installed. Run: pip install pymem"

    try:
        pm = pymem.Pymem("dbvStart.exe")
    except Exception as e:
        return f"ERROR: Could not attach to dbvStart.exe — is the game running? ({e})"

    game_pid = pm.process_id
    log.info(f"Attached to client (PID: {game_pid})")

    # ── Force-close existing game connections ──────────────────────
    # If the client is already logged in, close its TCP connections so it
    # drops back to the login screen and reconnects through our proxy.
    closed = _close_game_connections(game_pid)
    if closed > 0:
        log.info(f"Closed {closed} existing game connection(s) — client should return to login screen")
        await asyncio.sleep(2)  # Give the client time to process the disconnect

    from patcher import find_server_address_in_memory, patch_memory

    ip_locs = find_server_address_in_memory(pm)
    localhost = b"127.0.0.1"
    patched = sum(1 for addr, old in ip_locs if patch_memory(pm, addr, old, localhost))
    pm.close_process()

    if patched == 0:
        # Client may already be patched from a previous session — continue anyway
        log.warning("No IPs patched (client may already be patched). Continuing...")

    log.info(f"Patched {patched} server IP(s)")

    # ── Create proxies ──────────────────────────────────────────────
    state.login_proxy = OTProxy(SERVER_HOST, LOGIN_PORT, LOGIN_PORT, is_login_proxy=True)
    state.game_proxy = OTProxy(SERVER_HOST, GAME_PORT, GAME_PORT, is_login_proxy=False)
    from client_profiles import NOVEMBER_26
    if client_profile == NOVEMBER_26:
        from client_protocol import read_live_protocol_key
        state.game_proxy.client_key_reader = lambda: read_live_protocol_key(game_pid)
    state.game_proxy.injections_paused = state.automation_paused

    # Callbacks
    # The bounded observer handles each server frame once.

    def on_client_packet(opcode, reader):
        try:
            name = ClientOpcode(opcode).name
        except ValueError:
            name = "?"
        log.debug(f"[C->S] 0x{opcode:02X} ({name})")
        if opcode in (ClientOpcode.MOVE_THING, ClientOpcode.REQUEST_TRADE, ClientOpcode.ACCEPT_TRADE,
                      ClientOpcode.BUY_ITEM, ClientOpcode.SELL_ITEM):
            session_observations(state.game_state).publish("client_action", ("inventory",))
            state.game_state.message_history.invalidate_inventory()
            state.potion_diagnostics.invalidate_inventory()
            usage = getattr(state.game_state, 'senzu_usage', None)
            if usage is not None:
                usage.invalidate_inventory()
        # Track current attack target in game_state
        if opcode == ClientOpcode.ATTACK:
            try:
                cid = reader.read_u32()
                state.game_state.attack_target_id = cid
                session_observations(state.game_state).publish("client_action", ("target",))
            except Exception:
                pass
        # Log USE_ITEM details so we can discover item IDs
        elif opcode in (ClientOpcode.USE_ITEM, ClientOpcode.USE_ITEM_EX, ClientOpcode.USE_ON_CREATURE):
            try:
                pos = reader.read_position()
                item_id = reader.read_u16()
                stack_pos = reader.read_u8()
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
                log.info(f"[SNIFF] USE_ITEM pos={pos} item_id={item_id} stack={stack_pos}")
            except Exception:
                pass

    def on_login_success(keys):
        if state.game_proxy is not observation_proxy or state.login_startup.owns(observation_proxy):
            return
        if getattr(state,"packet_observer",None):state.packet_observer.invalidate()
        previous = getattr(state.game_state, "movement_history", None)
        if previous:previous.close()
        # New identity/stats/conditions for every login, including reconnects.
        state.game_state = GameState()
        login_state = state.game_state
        from movement_history import MovementHistory
        login_state.movement_history = MovementHistory()
        login_state.movement_history.start_worker()
        login_state.client_state_required = observer
        login_state.session_id = observation_proxy.session_generation
        if getattr(state, "packet_journal", None):state.packet_journal.session_changed(observation_proxy.session_generation)
        session_observations(login_state)
        monitor = getattr(state, "passive_map_monitor", None)
        if monitor:
            monitor.reset_learning_session()
        state.ready = True
        state.game_state.lure_active = False  # always clear stale lure state on reconnect
        log.info("=== BOT READY — game session established ===")

        state.login_startup.schedule(state, _start_all_enabled_actions)

    # Only the owner loop can publish parser results or notify other domains.
    from packet_observer import PacketObserver, publish_server_feedback
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

    state.game_proxy.register_client_packet_callback(on_client_packet)
    def observe_navigation_command(opcode, reader):
        monitor = getattr(state, "passive_map_monitor", None)
        if monitor:
            monitor.record_client_packet(opcode, reader)
    state.game_proxy.register_client_packet_callback(observe_navigation_command)

    def journal_client_packet(opcode, reader):
        if getattr(state, "packet_journal", None) and state.packet_journal.enabled:
            state.packet_journal.client_packet(opcode, reader.remaining + 1)
    state.game_proxy.register_client_packet_callback(journal_client_packet)
    state.game_proxy.on_login_success = on_login_success
    state.game_proxy.on_game_disconnected = on_disconnect
    state.game_proxy.on_encrypted_server_data = state.packet_observer.offer

    # ── Launch proxies as background tasks ──────────────────────────
    state._proxy_tasks = [
        asyncio.create_task(state.login_proxy.start()),
        asyncio.create_task(state.game_proxy.start()),
    ]

    log.info("Proxies started — waiting for player to log in via the game client...")

    # Launch dashboard API and Electron immediately (don't wait for login)
    import dashboard_api
    dashboard_api.start_api(asyncio.get_running_loop(), state)
    _launch_dashboard()
    _publish_observer_bridge()

    status = _build_status_report()
    return (
        f"Bot launched. Patched {patched} IP(s). Proxies listening.\n"
        f"Log in through the game client — actions will auto-start.\n\n"
        f"=== Component Status ===\n{status}"
    )


@mcp.tool()
async def walk(direction: str, steps: int = 1) -> str:
    """Walk the character in a direction.

    Args:
        direction: One of n, s, e, w, ne, se, sw, nw (or full name like north).
        steps: Number of tiles to walk (default 1).
    """
    if not state.connected:
        return "Bot is not connected. Call start_bot first."

    d = _resolve_direction(direction)
    if d is None:
        return f"Unknown direction: {direction}. Use n/s/e/w/ne/se/sw/nw."

    for i in range(steps):
        await state.game_proxy.inject_to_server(build_walk_packet(d))
        if steps > 1:
            await asyncio.sleep(0.3)

    return f"Walked {d.name} {steps} step(s)."


@mcp.tool()
async def turn(direction: str) -> str:
    """Turn the character to face a cardinal direction.

    Args:
        direction: One of n, s, e, w (or full name).
    """
    if not state.connected:
        return "Bot is not connected. Call start_bot first."

    d = _resolve_direction(direction, allow_diagonal=False)
    if d is None:
        return f"Unknown direction: {direction}. Turn only supports n/s/e/w."

    await state.game_proxy.inject_to_server(build_turn_packet(d))
    return f"Turned {d.name}."


@mcp.tool()
async def say(text: str) -> str:
    """Send a chat message in the game.

    Args:
        text: The message to say.
    """
    if not state.connected:
        return "Bot is not connected. Call start_bot first."

    await state.game_proxy.inject_to_server(build_say_packet(text))
    return f"Said: {text}"


@mcp.tool()
async def attack(creature_id: int) -> str:
    """Attack a creature by its ID.

    Args:
        creature_id: The numeric creature ID to attack.
    """
    if not state.connected:
        return "Bot is not connected. Call start_bot first."

    await state.game_proxy.inject_to_server(build_attack_packet(creature_id))
    return f"Attacking creature {creature_id}."


@mcp.tool()
async def follow(creature_id: int) -> str:
    """Follow a creature by its ID.

    Args:
        creature_id: The numeric creature ID to follow.
    """
    if not state.connected:
        return "Bot is not connected. Call start_bot first."

    await state.game_proxy.inject_to_server(build_follow_packet(creature_id))
    return f"Following creature {creature_id}."


@mcp.tool()
async def stop() -> str:
    """Stop the character from walking."""
    if not state.connected:
        return "Bot is not connected. Call start_bot first."

    await state.game_proxy.inject_to_server(build_stop_walk_packet())
    return "Stopped walking."


@mcp.tool()
async def autowalk(direction: str, steps: int = 100, delay: float = 0.5) -> str:
    """Auto-walk in a direction as a background task.

    Args:
        direction: One of n, s, e, w, ne, se, sw, nw.
        steps: Maximum number of steps (default 100).
        delay: Seconds between each step (default 0.5).
    """
    navigation=state._action_tasks.get("adaptive_cavebot")
    if navigation and not navigation.done():
        return "Zatrzymaj Cavebot przed uruchomieniem auto-walk."
    if state.automation_paused:
        return "Emergency pause is active. Unlock it first."
    if not state.connected:
        return "Bot is not connected. Call start_bot first."

    d = _resolve_direction(direction)
    if d is None:
        return f"Unknown direction: {direction}."

    # Cancel existing auto-walk
    if state._auto_task and not state._auto_task.done():
        state._auto_task.cancel()

    async def _loop():
        for _ in range(steps):
            if not state.connected:
                break
            await state.game_proxy.inject_to_server(build_walk_packet(d))
            await asyncio.sleep(delay)

    state._auto_task = asyncio.create_task(_loop())
    return f"Auto-walking {d.name} for up to {steps} steps (delay={delay}s). Use stop_autowalk to cancel."


@mcp.tool()
async def stop_autowalk() -> str:
    """Cancel a running auto-walk task."""
    if state._auto_task and not state._auto_task.done():
        state._auto_task.cancel()
        state._auto_task = None
        if state.connected:
            await state.game_proxy.inject_to_server(build_stop_walk_packet())
        return "Auto-walk cancelled."
    return "No auto-walk is running."


@mcp.tool()
async def use_item(x: int, y: int, z: int, item_id: int, stack_pos: int = 0, index: int = 0) -> str:
    """Use an item at a map position.

    Args:
        x: Map X coordinate.
        y: Map Y coordinate.
        z: Map Z (floor) coordinate.
        item_id: The item's type/sprite ID.
        stack_pos: Stack position on the tile (default 0).
        index: Container index (default 0).
    """
    if not state.connected:
        return "Bot is not connected. Call start_bot first."

    await state.game_proxy.inject_to_server(
        build_use_item_packet(x, y, z, item_id, stack_pos, index)
    )
    return f"Used item {item_id} at ({x}, {y}, {z})."


@mcp.tool()
async def move_item(
    from_x: int, from_y: int, from_z: int,
    item_id: int, from_stack: int,
    to_x: int, to_y: int, to_z: int,
    count: int = 1,
) -> str:
    """Move/drag an item from one position to another.

    Args:
        from_x: Source X coordinate.
        from_y: Source Y coordinate.
        from_z: Source Z coordinate.
        item_id: The item's type/sprite ID.
        from_stack: Stack position at the source.
        to_x: Destination X coordinate.
        to_y: Destination Y coordinate.
        to_z: Destination Z coordinate.
        count: Number of items to move (default 1).
    """
    if not state.connected:
        return "Bot is not connected. Call start_bot first."

    await state.game_proxy.inject_to_server(
        build_move_item_packet((from_x, from_y, from_z), item_id, from_stack, (to_x, to_y, to_z), count)
    )
    return f"Moved {count}x item {item_id} from ({from_x},{from_y},{from_z}) to ({to_x},{to_y},{to_z})."


@mcp.tool()
async def look_at(x: int, y: int, z: int, item_id: int, stack_pos: int = 0) -> str:
    """Look at a tile or item on the map.

    Args:
        x: Map X coordinate.
        y: Map Y coordinate.
        z: Map Z coordinate.
        item_id: The item/object ID on the tile.
        stack_pos: Stack position (default 0).
    """
    if not state.connected:
        return "Bot is not connected. Call start_bot first."

    await state.game_proxy.inject_to_server(build_look_packet(x, y, z, item_id, stack_pos))
    return f"Looking at item {item_id} at ({x}, {y}, {z})."


# ── Automated action tools ──────────────────────────────────────────

@mcp.tool()
async def list_actions() -> str:
    """List all action scripts in the actions/ folder with their enabled/running status and source preview.

    To create a new action, write a .py file to the actions/ folder with an
    async def run(bot) entry point.  The `bot` object provides:
      bot.use_item_in_container(item_id, container, slot)
      bot.say(text)
      bot.walk(direction, steps, delay)
      bot.inject_to_server(packet)
      bot.sleep(seconds)
      bot.is_connected
      bot.log(msg)
    """
    names = [n for n in _discover_actions() if n not in INTERNAL_ACTIONS]
    if not names:
        return "No actions found. Create a .py file in the actions/ folder with async def run(bot)."

    settings_actions = state.settings.get("actions", {})

    # Gather data for each action
    rows = []
    for name in names:
        cfg = settings_actions.get(name, {})
        enabled = cfg.get("enabled", False)
        task = state._action_tasks.get(name)
        running = task is not None and not task.done()

        if enabled and running:
            status = ">>> RUNNING"
        elif enabled:
            status = "ON (idle)"
        else:
            status = "OFF"

        # Read docstring as description
        path = ACTIONS_DIR / f"{name}.py"
        desc = ""
        source = ""
        try:
            source = path.read_text(encoding="utf-8")
            for line in source.splitlines():
                s = line.strip().strip('"').strip("'")
                if s:
                    desc = s[:50]
                    break
        except OSError:
            pass

        rows.append((name, status, desc, source))

    # Build ASCII table
    name_w = max(max(len(r[0]) for r in rows), 6)
    stat_w = max(max(len(r[1]) for r in rows), 6)
    desc_w = max(max(len(r[2]) for r in rows), 11)

    sep = f"+{'-' * (name_w + 2)}+{'-' * (stat_w + 2)}+{'-' * (desc_w + 2)}+"
    hdr = f"| {'Action':<{name_w}} | {'Status':<{stat_w}} | {'Description':<{desc_w}} |"

    lines = [sep, hdr, sep]
    for name, status, desc, source in rows:
        lines.append(f"| {name:<{name_w}} | {status:<{stat_w}} | {desc:<{desc_w}} |")
    lines.append(sep)

    # Append source preview for each action
    for name, _, _, source in rows:
        preview = source.rstrip()
        if len(preview) > 500:
            preview = preview[:500] + "\n... (truncated)"
        lines.append(f"\n--- {name}.py ---")
        lines.append(preview)

    return "\n".join(lines)


@mcp.tool()
async def toggle_action(name: str, enabled: bool = True) -> str:
    """Enable or disable an action, starting or stopping its background task.

    Args:
        name: Action name (filename without .py, e.g. "eat_food").
        enabled: True to enable and start, False to disable and stop.
    """
    return await _async_toggle_action(name, enabled)


@mcp.tool()
async def remove_action(name: str) -> str:
    """Delete an action script and its settings.

    Args:
        name: Action name (filename without .py, e.g. "eat_food").
    """
    if name in INTERNAL_ACTIONS:
        return f"Action '{name}' is an internal service and cannot be removed."
    if not all(c.isalnum() or c == '_' for c in name):
        return "Invalid action name. Only alphanumeric characters and underscores are allowed."

    _stop_action(name)

    path = ACTIONS_DIR / f"{name}.py"
    existed = path.exists()
    if existed:
        path.unlink()

    actions = state.settings.get("actions", {})
    was_in_settings = actions.pop(name, None) is not None
    if was_in_settings:
        save_settings(state.settings)

    if not existed and not was_in_settings:
        return f"Action '{name}' not found."
    return f"Removed action '{name}'."


@mcp.tool()
async def restart_action(name: str) -> str:
    """Stop and re-start a running action (reloads the .py file from disk).

    Args:
        name: Action name (filename without .py).
    """
    return await _async_restart_action(name)


@mcp.tool()
async def set_fight_modes(fight_mode: int = 1, chase_mode: int = 0, safe_mode: int = 1) -> str:
    """Set combat/fight modes.

    Args:
        fight_mode: 1 = offensive, 2 = balanced, 3 = defensive.
        chase_mode: 0 = stand still, 1 = chase opponent.
        safe_mode: 0 = PvP enabled, 1 = safe mode (no PvP).
    """
    if not state.connected:
        return "Bot is not connected. Call start_bot first."

    await state.game_proxy.inject_to_server(
        build_set_fight_modes_packet(fight_mode, chase_mode, safe_mode)
    )
    mode_names = {1: "offensive", 2: "balanced", 3: "defensive"}
    chase_names = {0: "stand", 1: "chase"}
    safe_names = {0: "PvP", 1: "safe"}
    return (
        f"Fight modes set: {mode_names.get(fight_mode, fight_mode)}, "
        f"{chase_names.get(chase_mode, chase_mode)}, "
        f"{safe_names.get(safe_mode, safe_mode)}."
    )


@mcp.tool()
async def send_ping() -> str:
    """Send a ping packet to the game server."""
    if not state.connected:
        return "Bot is not connected. Call start_bot first."

    await state.game_proxy.inject_to_server(build_ping_packet())
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
    """Get the current connection state and packet statistics."""
    status = _build_status_report()
    local = await state.local_transport.snapshot()
    status += f"\nLocal transport: {local['state']}"
    if local.get("port"):
        status += (f" · 127.0.0.1:{local['port']} · connections {local['connections']}"
                   f" · failures {local['failures']}")
    status += f"\nEmergency pause: {'ACTIVE' if state.automation_paused else 'off'}"

    # Running actions
    running = [n for n, t in state._action_tasks.items() if t and not t.done()]
    if running:
        status += f"\n\nRunning actions: {', '.join(running)}"

    auto = "running" if (state._auto_task and not state._auto_task.done()) else "idle"
    status += f"\nAuto-walk: {auto}"
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
    from session_journal import _plain
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
    from session_bridge import ObserverBridge
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
    import session_bridge
    return "launcher", await asyncio.to_thread(session_bridge.call, op, **args)


@mcp.tool()
async def attach_status() -> dict:
    """Find the game session (this process or the launcher's) and return its status. Read-only."""
    import session_bridge
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
    import session_bridge
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
    import session_bridge
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
    from recovery_preview import preview
    return await preview(state)


@mcp.tool()
async def open_dashboard(standalone: bool = False) -> str:
    """Open the local panel. Standalone mode also works before game login."""
    if state.game_proxy is None:
        if not standalone:
            return "ERROR: Bot session is not started. Start from the character list first."
        import dashboard_api
        if not dashboard_api.start_api(asyncio.get_running_loop(), state, panel_only=True):
            return "ERROR: Could not open the local dashboard API."
    _launch_dashboard()
    if _dashboard_running('electron'):
        return "Panel opened. Game connection and actions unchanged."
    return "ERROR: Could not open panel. Check the installed dashboard and Node.js."


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
    """Send a logout packet to the game server."""
    if not state.connected:
        return "Bot is not connected. Call start_bot first."

    pw = PacketWriter()
    pw.write_u8(ClientOpcode.LOGOUT)
    await state.game_proxy.inject_to_server(pw.data)
    return "Logout packet sent."


# ── Cavebot recording / playback tools ─────────────────────────────

@mcp.tool()
async def start_recording(name: str) -> str:
    """Begin recording navigation waypoints (walk, use items, stairs, doors).

    Args:
        name: Name for the recording (alphanumeric, hyphens, underscores).
    """
    if not state.connected:
        return "Bot is not connected. Call start_bot first."
    err = cavebot.start_recording(state, name)
    if err:
        return f"Error: {err}"
    return f"Recording '{name}' started. Walk around, use doors/stairs/ladders. Call stop_recording when done."


@mcp.tool()
async def stop_recording() -> str:
    """Stop recording and save waypoints to disk."""
    if not state.recording_active:
        return "No recording in progress."
    rec = cavebot.stop_recording(state)
    if rec is None:
        return "Recording stopped (no data saved)."
    count = len(rec.get("waypoints", []))
    return f"Recording '{rec['name']}' saved with {count} waypoint(s)."


@mcp.tool()
async def list_recordings() -> str:
    """List all saved cavebot recordings."""
    recs = cavebot.list_recordings()
    if not recs:
        return "No recordings found. Use start_recording to create one."
    lines = []
    for r in recs:
        lines.append(f"  {r['name']}  ({r['count']} waypoints)  created: {r['created_at']}")
    return "Saved recordings:\n" + "\n".join(lines)


@mcp.tool()
async def delete_recording(name: str) -> str:
    """Delete a saved cavebot recording.

    Args:
        name: Name of the recording to delete.
    """
    if cavebot.delete_recording(name):
        return f"Recording '{name}' deleted."
    return f"Recording '{name}' not found."


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
