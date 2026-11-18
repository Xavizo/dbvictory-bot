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


def _engine_label(root):
    started = datetime.now().astimezone().isoformat(sep=" ", timespec="seconds")
    revision = "wersja nieznana"
    if (root / ".git").exists():
        try:
            revision = subprocess.check_output(
                ["git", "rev-parse", "--short=7", "--verify", "HEAD"], cwd=root,
                stderr=subprocess.DEVNULL, timeout=2, encoding="ascii",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return f"Silnik {revision} · start {started}"


# Capture once, before loading the engine. Reopening the panel reuses this identity.
ENGINE_LABEL = _engine_label(Path(__file__).resolve().parent)

# ── Ensure project root is on sys.path ──────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP

from proxy import OTProxy
from game_state import GameState, parse_server_packet, scan_packet
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
from potions import PotionScheduler, PotionDiagnostics, ITEMS as HEALING_ITEMS, LEGACY_HEALING_ACTIONS, validate_config as validate_potion_config
from barrier_band import BandScheduler, validate_config as validate_band_config
from route_safety import route_entry
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
        self.login_proxy: OTProxy | None = None
        self.game_proxy: OTProxy | None = None
        self.ready: bool = False
        self._auto_task: asyncio.Task | None = None
        self._login_wait_task: asyncio.Task | None = None
        self._login_event: asyncio.Event | None = None
        self._proxy_tasks: list[asyncio.Task] = []
        self.settings: dict = load_settings()
        self.automation_paused = bool(self.settings.get("automation_paused", False))
        self.automation_pause_kind = self.settings.get("automation_pause_kind", "emergency") if self.automation_paused else None
        self._paused_playback = None
        self._stopping_tasks = []
        self._emergency_stopping = False
        self.band_scheduler = BandScheduler()
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

        # Cavebot playback state
        self.playback_active: bool = False
        self.playback_recording_name: str = ""
        self.playback_index: int = 0
        self.playback_total: int = 0
        self.playback_loop: bool = False
        self.playback_actions_map: list[dict] = []
        self.playback_minimap: dict | None = None
        self.playback_failed_nodes: set[int] = set()

        # Playback session statistics
        self.playback_loop_count: int = 0
        self.playback_kills: int = 0
        self.playback_senzu_used: int = 0
        self.playback_start_time: float = 0       # time.time() when playback started
        self.playback_start_experience: int | None = None  # XP snapshot at playback start
        self.playback_start_level: int = 0        # level snapshot at playback start

        # Senzu/h time-series for charting
        self.playback_senzu_series: list[list] = []     # [[elapsed_sec, senzu_per_hour], ...]
        self._last_senzu_sample_time: float = 0

        # Route telemetry — per-segment timing and kill stats
        self.segment_enter_time: dict[int, float] = {}   # {segment_index: enter_timestamp}
        self.segment_stats: dict[int, dict] = {}          # accumulated per-segment stats

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

    async def say(self, text: str) -> None:
        await self.inject_to_server(build_say_packet(text))

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


def _healing_conflict(name: str) -> str | None:
    others = LEGACY_HEALING_ACTIONS if name == "auto_potions" else {"auto_potions"} if name in LEGACY_HEALING_ACTIONS else set()
    conflicts = [other for other in sorted(others)
                 if state.settings.get("actions", {}).get(other, {}).get("enabled")
                 or (state._action_tasks.get(other) and not state._action_tasks[other].done())]
    if conflicts:
        return "Disable conflicting healing actions first: " + ", ".join(conflicts)
    return None


def _start_action(name: str) -> str | None:
    """Load an action script and launch its run(bot) as a background task.
    Returns an error string on failure, None on success."""
    if name in {"cavebot","cavebot2"}:
        return "Ten starszy silnik jest wyłączony. Użyj Cavebot."
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
    conflict = _healing_conflict(name)
    if conflict:
        return conflict
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
        # Always clear lure_active when cavebot is stopped — the try/finally
        # in cavebot.run() also does this, but task.cancel() is async so the
        # finally block may not have run yet when callers check lure_active.
        if name == "cavebot":
            was = state.game_state.lure_active
            state.game_state.lure_active = False
            log.info(f"_stop_action(cavebot): lure_active {was} -> False")
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
        if name in {"cavebot","cavebot2","adaptive_cavebot"}:
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
    if enabled and name in {"cavebot","cavebot2","adaptive_cavebot"}:
        return "Error: Starsze silniki są wyłączone; V2 uruchamiaj przez Start zapisanej trasy."
    if name in INTERNAL_ACTIONS:
        return f"Action '{name}' is an internal service and cannot be toggled."
    if enabled and state.automation_paused:
        return "Error: Emergency pause is active. Unlock it first."
    if enabled and (conflict := _healing_conflict(name)):
        return f"Error: {conflict}"
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
        if state._paused_playback and state._paused_playback["engine"]==name:
            state._paused_playback=None
        if name in {"cavebot","cavebot2"}:
            state.playback_active=state.playback_loop=False
            state.game_state.lure_active=False
            if state.game_proxy and was_running:
                await state.game_proxy.send_emergency_stop()
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
            state._paused_playback=None
            for name,cfg in state.settings.get("actions",{}).items():
                if name not in INTERNAL_ACTIONS:cfg["enabled"]=False
            save_settings(state.settings)
        return "Emergency pause is already stopping actions."
    state._emergency_stopping = True
    state.automation_paused = True
    state.settings["automation_paused"] = True
    state.automation_pause_kind = "manual" if preserve_enabled else "emergency"
    state.settings["automation_pause_kind"] = state.automation_pause_kind
    if preserve_enabled:
        state.settings["paused_actions"] = sorted((set(n for n,c in state.settings.get("actions",{}).items() if c.get("enabled")) | set(state._action_tasks)) - INTERNAL_ACTIONS - state._action_completed)
        state._paused_playback = ({"name":state.playback_recording_name,"loop":state.playback_loop,
                                   "engine":"cavebot2" if "cavebot2" in state._action_tasks else "cavebot"}
                                  if state.playback_active else None)
    else:
        state.settings.pop("paused_actions",None)
        state._paused_playback = None
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
    state.playback_active = False
    state.playback_loop = False
    state.game_state.lure_active = False
    state.game_state.force_target = None
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
    route = state._paused_playback if state.connected else None
    if route:
        rec = cavebot.load_recording(route["name"])
        if rec is None: raise ValueError("Paused route no longer exists")
        _,error = route_entry(cavebot.build_actions_map(rec),state.game_state.position)
        if error: raise ValueError(error)
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
        if route:
            state.playback_active=True
            state.playback_recording_name=route["name"]
            state.playback_loop=route["loop"]
            names=list(dict.fromkeys([*names,route["engine"]]))
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
    state._paused_playback=None
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
    if name in {"cavebot","cavebot2","adaptive_cavebot"}:
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
            env = os.environ.copy()
            env.pop("DBV_DASHBOARD_TOKEN", None)
            if name == "electron":
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

    state._adaptive_prepare_token=None
    # 1. Stop all running actions
    for name in list(state._action_tasks):
        _stop_action(name)
    if getattr(state,"adaptive_cavebot",None):state.adaptive_cavebot.stop("Sesja została zresetowana.")
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

    # 5b. Cancel the login-wait task
    login_task = getattr(state, '_login_wait_task', None)
    if login_task and not login_task.done():
        login_task.cancel()
        try:
            await asyncio.wait_for(login_task, timeout=1.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass

    # 6. Reset global state
    state.login_proxy = None
    state.game_proxy = None
    state.ready = False
    state._login_event = asyncio.Event()
    state._auto_task = None
    state._action_tasks.clear()
    state.game_state = GameState()
    state.settings = load_settings()
    state.automation_paused = bool(state.settings.get("automation_paused", False))
    state.automation_pause_kind = state.settings.get("automation_pause_kind","emergency") if state.automation_paused else None
    state._paused_playback = None
    state._stopping_tasks = []

    # Reset cavebot state
    state.recording_active = False
    state.recording_name = ""
    state.recording_waypoints = []
    state.recording_start_pos = (0, 0, 0)
    state.recording_start_time = 0
    state._recording_callback = None
    state.playback_active = False
    state.playback_recording_name = ""
    state.playback_index = 0
    state.playback_total = 0
    state.playback_loop = False
    state.playback_actions_map = []
    state.playback_minimap = None
    state.playback_failed_nodes = set()
    state.playback_loop_count = 0
    state.playback_kills = 0
    state.playback_senzu_used = 0
    state.playback_start_time = 0
    state.playback_start_experience = None
    state.playback_start_level = 0

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
            observer = verified_client.profile.observer
        finally:
            verified_client.close()
        if observer and not Path(__file__).with_name("dll").joinpath("dbvobserver_20261116.dll").is_file():
            return "ERROR: Build dll/dbvobserver_20261116.dll before starting this client version."
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
    state.game_proxy.injections_paused = state.automation_paused

    # Callbacks
    # NOTE: on_server_packet removed — scan_packet handles all opcodes via
    # on_raw_server_data, avoiding redundant double-parsing of every packet.

    def on_client_packet(opcode, reader):
        try:
            name = ClientOpcode(opcode).name
        except ValueError:
            name = "?"
        log.debug(f"[C->S] 0x{opcode:02X} ({name})")
        if opcode in (ClientOpcode.MOVE_THING, ClientOpcode.REQUEST_TRADE, ClientOpcode.ACCEPT_TRADE,
                      ClientOpcode.BUY_ITEM, ClientOpcode.SELL_ITEM):
            state.potion_diagnostics.invalidate_inventory()
        # Track current attack target in game_state
        if opcode == ClientOpcode.ATTACK:
            try:
                cid = reader.read_u32()
                state.game_state.attack_target_id = cid
            except Exception:
                pass
        # Log USE_ITEM details so we can discover item IDs
        elif opcode in (ClientOpcode.USE_ITEM, ClientOpcode.USE_ITEM_EX, ClientOpcode.USE_ON_CREATURE):
            try:
                pos = reader.read_position()
                item_id = reader.read_u16()
                stack_pos = reader.read_u8()
                if item_id in HEALING_ITEMS:
                    mode = "self" if opcode == ClientOpcode.USE_ON_CREATURE else "use" if opcode == ClientOpcode.USE_ITEM else "use_with"
                    if opcode == ClientOpcode.USE_ON_CREATURE and reader.read_u32() != state.game_state.player_id:
                        mode = "other_creature"
                    state.potion_diagnostics.record_use(item_id, mode, state.game_state)
                    cfg = validate_potion_config(state.settings.get("actions", {}).get("auto_potions", {}))
                    state.potion_scheduler.record_item_use(item_id, cfg, _time.monotonic(), state.game_state.stats_updated_at)
                log.info(f"[SNIFF] USE_ITEM pos={pos} item_id={item_id} stack={stack_pos}")
            except Exception:
                pass

    def on_login_success(keys):
        # New identity/stats/conditions for every login, including reconnects.
        state.game_state = GameState()
        login_state = state.game_state
        login_state.client_state_required = observer
        login_state.session_id = getattr(state.game_proxy,'session_generation',0)
        monitor = getattr(state, "passive_map_monitor", None)
        if monitor:
            monitor.reset_learning_session()
        state.ready = True
        state.game_state.lure_active = False  # always clear stale lure state on reconnect
        log.info("=== BOT READY — game session established ===")

        # Signal the login-wait task immediately (no polling delay)
        event = getattr(state, '_login_event', None)
        if event:
            event.set()
        # Direct auto-start: schedule action startup from the callback itself.
        # This is the bulletproof path — no watcher task, no race condition.
        async def _direct_start():
            # Brief delay for proxy to finish setting up server_writer
            for _ in range(10):
                await asyncio.sleep(0.5)
                if (state.game_proxy and state.game_proxy.logged_in
                        and state.game_proxy.server_writer is not None):
                    break
            if (not state.connected or not state.game_proxy.logged_in
                    or state.game_state is not login_state):
                return
            n = _start_all_enabled_actions()
            log.info(f"on_login_success: {n} action(s) auto-started")
        asyncio.ensure_future(_direct_start())

    # Single-thread executor for packet scanning — prevents event loop blocking
    # during cavebot walking (constant MAP_SLICE packets with pure-Python XTEA
    # decryption + byte-by-byte tile update search).
    from concurrent.futures import ThreadPoolExecutor
    _scan_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pkt-scan")

    def on_raw_server_data(data):
        # Fire-and-forget: scan in thread pool so event loop stays responsive
        # for healing (auto_senzu), combat (auto_combat), and targeting actions.
        loop = asyncio.get_event_loop()
        gs = state.game_state
        def scan_and_observe():
            gs.alert_chat_callback = lambda message, source: state.alert_center.observe_chat(message, state, source) if gs is state.game_state else None
            gs.potion_message_callback = lambda *args, **kwargs: state.potion_diagnostics.observe_message(*args, **kwargs) if gs is state.game_state else None
            if gs is not state.game_state:return
            scan_packet(data, gs)
            if gs is state.game_state:state.potion_diagnostics.observe_stats(gs)
        loop.run_in_executor(_scan_pool, scan_and_observe)

    def on_disconnect():
        state._adaptive_prepare_token=None
        state.potion_diagnostics.invalidate_inventory()
        monitor = getattr(state, "passive_map_monitor", None)
        if monitor:
            monitor.reset_learning_session()
        state.ready = False
        state._paused_playback = None
        state.playback_active = False
        state.game_state.stats_updated_at = 0
        state.game_state.lure_active = False
        state.game_state.force_target = None
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
    state.game_proxy.on_login_success = on_login_success
    state.game_proxy.on_game_disconnected = on_disconnect
    state.game_proxy.on_raw_server_data = on_raw_server_data

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

    # Background task: wait for login, then start actions automatically
    # Uses an asyncio.Event for instant wakeup (no polling race condition).
    state._login_event = asyncio.Event()
    # If already ready (e.g. fast login), pre-set the event
    if state.ready:
        state._login_event.set()

    async def _wait_for_login_and_start():
        t0 = time.time()
        log.info(f"[TIMING] waiting for login... t=0.0s")
        try:
            await asyncio.wait_for(state._login_event.wait(), timeout=120.0)
        except asyncio.TimeoutError:
            log.warning(f"[TIMING] No login detected after {time.time()-t0:.1f}s — actions not started.")
            return

        t1 = time.time()
        log.info(f"[TIMING] login event fired at t={t1-t0:.1f}s — verifying proxy...")

        # Verify proxy connection is functional
        for i in range(10):
            await asyncio.sleep(0.5)
            if (state.game_proxy and state.game_proxy.logged_in
                    and state.game_proxy.server_writer is not None):
                t2 = time.time()
                log.info(f"[TIMING] proxy verified at t={t2-t0:.1f}s (waited {t2-t1:.1f}s)")
                break
        else:
            t2 = time.time()
            log.info(f"[TIMING] proxy verify timeout at t={t2-t0:.1f}s — starting actions anyway")

        actions_started = _start_all_enabled_actions()
        t3 = time.time()
        log.info(f"[TIMING] {actions_started} action(s) started at t={t3-t0:.1f}s")

    state._login_wait_task = asyncio.create_task(_wait_for_login_and_start())

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
async def get_status() -> str:
    """Get the current connection state and packet statistics."""
    status = _build_status_report()
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


@mcp.tool()
async def open_dashboard() -> str:
    """Reopen this session's panel without touching game sockets or actions."""
    if state.game_proxy is None:
        return "ERROR: Bot session is not started. Start from the character list first."
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


async def _async_play_recording(name: str, loop: bool = False) -> str:
    """Start playback (shared by MCP tool and dashboard API)."""
    if state.automation_paused:
        return "Error: Emergency pause is active. Unlock it first."
    if not state.connected:
        return "Bot is not connected. Call start_bot first."
    if state.playback_active:
        return f"Already playing '{state.playback_recording_name}'. Stop it first."

    rec = cavebot.load_recording(name)
    if rec is None:
        return f"Recording '{name}' not found."

    waypoints = rec.get("waypoints", [])
    if not waypoints:
        return f"Recording '{name}' has no waypoints."

    _, error = route_entry(cavebot.build_actions_map(rec), state.game_state.position)
    if error:
        return f"Error: {error}"

    state.playback_active = True
    state.playback_recording_name = name
    state.playback_index = 0
    state.playback_total = len(waypoints)
    state.playback_loop = loop

    # Initialize session stats
    state.playback_loop_count = 0
    state.playback_kills = 0
    state.playback_senzu_used = 0
    state.playback_start_time = _time.time()
    state.playback_start_experience = state.game_state.experience
    state.playback_start_level = state.game_state.level
    state.game_state.session_kills = 0

    # Initialize route telemetry
    state.game_state.kill_log.clear()
    state.game_state._prev_experience = state.game_state.experience
    state.segment_enter_time = {}
    state.segment_stats = {}

    # Start the cavebot action
    err = _start_action("cavebot")
    if err:
        state.playback_active = False
        return f"Failed to start playback: {err}"

    loop_str = " (looping)" if loop else ""
    return f"Playing recording '{name}' ({len(waypoints)} waypoints){loop_str}."


async def _async_stop_playback() -> str:
    """Stop playback (shared by MCP tool and dashboard API)."""
    state._paused_playback = None
    # Always clear lure_active, even if playback_active is already False
    # (cavebot may have crashed/ended without clearing it)
    was = state.game_state.lure_active
    state.game_state.lure_active = False
    log.info(f"_async_stop_playback: lure_active {was} -> False (playback_active={state.playback_active})")
    if not state.playback_active:
        return "No playback in progress."
    _stop_action("cavebot")
    name = state.playback_recording_name
    state.playback_active = False
    state.playback_recording_name = ""
    state.playback_index = 0
    state.playback_total = 0
    state.playback_loop = False
    state.playback_actions_map = []
    state.playback_minimap = None
    state.playback_failed_nodes = set()
    state.playback_loop_count = 0
    state.playback_kills = 0
    state.playback_senzu_used = 0
    state.playback_start_time = 0
    state.playback_start_experience = None
    state.playback_start_level = 0
    state.playback_senzu_series = []
    state._last_senzu_sample_time = 0
    state.segment_enter_time = {}
    state.segment_stats = {}
    state.game_state.kill_log.clear()
    state.game_state.lure_active = False
    return f"Playback of '{name}' stopped."


@mcp.tool()
async def play_recording(name: str, loop: bool = False) -> str:
    """Start playing back a saved cavebot recording.

    Args:
        name: Name of the recording to play.
        loop: Whether to loop the recording (default False).
    """
    return await _async_play_recording(name, loop)


@mcp.tool()
async def stop_playback() -> str:
    """Stop cavebot playback."""
    return await _async_stop_playback()


@mcp.tool()
async def analyze_route() -> str:
    """Analyze route efficiency from kill telemetry collected during cavebot playback.

    Requires active playback with at least 1 completed loop. Returns per-segment
    kill counts, XP rates, time spent, and efficiency ratings (hot/warm/cold/dead).
    Includes recommendations for segments that could be skipped.
    """
    if not state.playback_active:
        return "No playback in progress. Start a looping playback first."
    if state.playback_loop_count < 1:
        return (f"Need at least 1 completed loop for analysis. "
                f"Currently on first pass (loop_count={state.playback_loop_count}).")

    seg_stats = state.segment_stats
    actions_map = state.playback_actions_map
    gs = state.game_state

    if not seg_stats:
        return "No segment stats collected yet. Kills may not have been detected."

    loops = state.playback_loop_count
    elapsed = _time.time() - state.playback_start_time if state.playback_start_time else 0
    elapsed_min = elapsed / 60
    xp_gained = gs.experience - state.playback_start_experience if state.playback_start_experience is not None else 0
    xp_per_hour = int(xp_gained / (elapsed / 3600)) if elapsed > 0 else 0
    total_kills = sum(s["kills"] for s in seg_stats.values())
    kills_per_loop = round(total_kills / max(loops, 1), 1)

    lines = []
    lines.append(f"Route Analysis ({loops} loops, {elapsed_min:.0f}min):")
    lines.append(f"Overall: {xp_per_hour:,} XP/h, {kills_per_loop} kills/loop, {gs.session_kills} total kills")
    lines.append("")

    # Header
    total_segs = len(actions_map)
    lines.append(f"{'Seg':<8} {'Type':<12} {'Target':<18} {'Kills':<7} {'XP/s':<7} {'AvgT':<7} {'Rating'}")
    lines.append("-" * 70)

    # Compute average XP/s for rating thresholds
    total_xp = sum(s["xp"] for s in seg_stats.values())
    total_time = sum(s["time_total"] for s in seg_stats.values())
    avg_xps = total_xp / total_time if total_time > 0 else 0

    cold_segments = []
    dead_segments = []

    for seg_idx in sorted(seg_stats.keys()):
        stats = seg_stats[seg_idx]
        node = actions_map[seg_idx] if seg_idx < len(actions_map) else {}
        entries = max(stats["entries"], 1)
        avg_time = stats["time_total"] / entries
        xps = stats["xp"] / stats["time_total"] if stats["time_total"] > 0 else 0
        avg_kills = stats["kills"] / entries

        ntype = node.get("type", "?")
        target = node.get("target")
        target_str = f"({target[0]},{target[1]},{target[2]})" if target else "--"

        if stats["kills"] == 0:
            rating = "--"
            dead_segments.append((seg_idx, avg_time))
        elif xps >= avg_xps * 1.2:
            rating = "HOT"
        elif xps >= avg_xps * 0.5:
            rating = "WARM"
        else:
            rating = "COLD"
            cold_segments.append((seg_idx, avg_kills, avg_time))

        lines.append(
            f"{seg_idx+1}/{total_segs:<5} {ntype:<12} {target_str:<18} "
            f"{avg_kills:<7.1f} {xps:<7.1f} {avg_time:<7.1f} {rating}"
        )

    # Recommendations
    if cold_segments or dead_segments:
        lines.append("")
        lines.append("Recommendations:")
        wasted = 0
        for seg_idx, avg_kills, avg_time in cold_segments:
            lines.append(f"  - Segment {seg_idx+1} is COLD ({avg_kills:.1f} kills/loop, {avg_time:.0f}s)")
            wasted += avg_time
        for seg_idx, avg_time in dead_segments:
            node = actions_map[seg_idx] if seg_idx < len(actions_map) else {}
            ntype = node.get("type", "?")
            # Don't flag stairs/use_item as dead — they're navigation, not hunting
            if ntype in ("use_item", "use_item_ex", "walk_steps"):
                continue
            lines.append(f"  - Segment {seg_idx+1} is DEAD (0 kills, {avg_time:.0f}s)")
            wasted += avg_time
        if wasted > 0 and total_time > 0:
            pct = wasted / (total_time / max(loops, 1)) * 100
            lines.append(f"  - Estimated wasted time: {wasted:.0f}s/loop ({pct:.0f}% of loop)")

    return "\n".join(lines)


# ── Entry point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
