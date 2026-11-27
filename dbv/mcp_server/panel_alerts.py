"""Alert sounds for the panel window, which keep playing while it sits in the tray.

Ported from the Electron main process (alert-sounds.js and notification-audio.js,
up to v2.0.17). Polls the authenticated engine state every 750 ms and
plays sounds through dashboard/electron/windows_alert.py. No game input, no UI
focus, and no chat text or credentials in logs.
"""
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

STATE_URL = "http://127.0.0.1:8089/api/state"
PANEL_ORIGIN = "http://127.0.0.1:4747"
POLL_SECONDS = 0.75
from dbv import paths

ALERT_HELPER = paths.DASHBOARD_DIR / "electron" / "windows_alert.py"


def _safe_int(value):
    return type(value) is int and -(2 ** 53 - 1) <= value <= 2 ** 53 - 1


def _number(value):
    return type(value) in (int, float) and value == value and value not in (float("inf"), float("-inf"))


def visible_players(data, now_ms):
    """Typed players on the player's floor of a fresh, aligned passive map."""
    if not isinstance(data, dict):
        return []
    passive, player = data.get("passive_map"), data.get("player") or {}
    position = player.get("position") if isinstance(player, dict) else None
    if (not data.get("connected") or not isinstance(passive, dict) or passive.get("status") != "ready"
            or passive.get("stale") or not isinstance(position, list) or not isinstance(passive.get("center"), list)
            or len(passive["center"]) != len(position) or passive["center"] != position
            or not _number(passive.get("captured_at"))):
        return []
    age = now_ms / 1000 - passive["captured_at"]
    if age < 0 or age >= 2:
        return []
    seen = []
    for cell in passive.get("cells") or []:
        if not isinstance(cell, dict) or cell.get("z") != position[2]:
            continue
        for found in cell.get("alert_players") or []:
            ident = found.get("id") if isinstance(found, dict) else None
            if _safe_int(ident) and 0x10000000 <= ident < 0x40000000 and ident not in seen:
                seen.append(ident)
    return seen


class SoundConsumer:
    """Decides which sound an alert snapshot deserves. play(kind) returns False when busy."""

    def __init__(self, play, now=lambda: time.time() * 1000):
        self.play, self.now = play, now
        self.session, self.cursor = None, 0
        self.last_player = self.last_message = self.last_test = self.last_alarm = float("-inf")
        self.queue, self.alarm_id = [], None

    @staticmethod
    def _allowed(state, kind):
        cfg = (state.get("config") or {}).get(kind)
        return (isinstance(cfg, dict) and cfg.get("enabled") is True and cfg.get("sound") is True
                and (cfg.get("scope") == "always" or (cfg.get("scope") == "cavebot" and state.get("cavebot_active") is True)))

    def consume(self, state, players=()):
        if (not isinstance(state, dict) or not isinstance(state.get("session"), str)
                or not _safe_int(state.get("latest_id")) or not isinstance(state.get("events"), list)):
            return
        now = self.now
        if state["session"] != self.session:
            self.session, self.alarm_id, self.cursor = state["session"], None, state["latest_id"]
            self.queue, self.last_player = [], float("-inf")
        if state.get("paused"):
            self.cursor, self.queue, self.last_player = state["latest_id"], [], float("-inf")
            return
        silenced = state.get("silenced_through")
        silenced = 0 if silenced is None else silenced
        ki = state.get("ki_alarm_id")
        ki_alarm = ki if (_number(ki) and ki > silenced and self._allowed(state, "ki_loss")) else None
        vitals = state.get("vitals_alarm_id")
        active = vitals if vitals is not None else ki_alarm
        if active != self.alarm_id:
            self.alarm_id, self.last_alarm = active, float("-inf")
        if self.alarm_id is not None and now() - self.last_alarm >= 2000 and self.play("vitals") is not False:
            self.last_alarm = now()
            return
        self.cursor = max(self.cursor, silenced)
        if state["latest_id"] > self.cursor:
            for event in state["events"]:
                if not isinstance(event, dict) or not _number(event.get("at")):
                    continue
                age = now() / 1000 - event["at"]
                if (_safe_int(event.get("id")) and self.cursor < event["id"] <= state["latest_id"]
                        and event.get("kind") in ("private", "default") and self._allowed(state, event["kind"])
                        and 0 <= age < 10):
                    self.queue.append({"id": event["id"], "kind": event["kind"], "expires": now() + 30000})
            self.cursor = max(self.cursor, state["latest_id"])
            self.queue = self.queue[-100:]
        self.queue = [e for e in self.queue
                      if e["id"] > silenced and e["expires"] >= now() and self._allowed(state, e["kind"])]
        if self.queue and now() - self.last_message >= 700:
            if self.play("message") is not False:
                self.queue.pop(0)
                self.last_message = now()
            return
        if not players or not self._allowed(state, "player"):
            self.last_player = float("-inf")
            return
        if now() - self.last_player >= 1500 and now() - self.last_message >= 700 and self.play("player") is not False:
            self.last_player = now()

    def test_sound(self):
        if self.now() - self.last_test < 1000:
            return False
        self.last_test = self.now()
        self.play("player")
        return True


def fetch_state(token, timeout=2):
    request = urllib.request.Request(STATE_URL, headers={
        "Authorization": f"Bearer {token}", "Origin": PANEL_ORIGIN, "Cache-Control": "no-store"})

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        if response.status != 200:
            return None
        return json.loads(response.read())


class AlertSounds:
    """The tray's polling loop. Call poll_once() yourself, or start() a thread for it."""

    def __init__(self, token, play, *, fetch=fetch_state, now=lambda: time.time() * 1000,
                 on_hunt_ready=None, on_mute=None):
        self.token, self.fetch, self.now = token, fetch, now
        self.on_hunt_ready, self.on_mute = on_hunt_ready, on_mute
        self.consumer = SoundConsumer(play, now)
        self.stopped = False
        self.notified_recording = None
        self.automation_paused = False
        # Older engines have independent alert preferences but no session-wide mute.
        self.panel_paused, self.alert_session, self.silenced_through = True, None, 0
        self.last_id, self.resumed_at = 0, None
        self._lock = threading.Lock()
        self._wake = threading.Event()

    def muted(self):
        return self.panel_paused or self.automation_paused

    def panel_pause(self, value=None):
        with self._lock:
            was_muted = self.muted()
            if value is not None:
                if type(value) is not bool:
                    raise TypeError("Invalid alert pause")
                if value != self.panel_paused:
                    self.panel_paused = value
                    self.silenced_through = self.last_id
                    self.resumed_at = None if value else self.now() / 1000
            if not was_muted and self.muted() and self.on_mute:
                self.on_mute()
            return {"paused": self.muted(), "session": self.alert_session, "through": self.silenced_through}

    def test_sound(self):
        with self._lock:
            return not self.muted() and self.consumer.test_sound()

    def poll_once(self):
        if self.stopped or not self.token:
            return
        try:
            data = self.fetch(self.token)
        except (OSError, ValueError, urllib.error.URLError):
            return  # No chat or credentials in logs. No repetition without a fresh response.
        if not isinstance(data, dict):
            return
        with self._lock:
            if self.stopped:
                return
            self._apply(data)

    def _apply(self, data):
        was_muted = self.muted()
        alerts = data.get("alerts")
        self.automation_paused = (data.get("automation_paused") is True
                                  or (isinstance(alerts, dict) and alerts.get("automation_paused") is True))
        if isinstance(alerts, dict):
            if self.alert_session != alerts.get("session"):
                if self.alert_session is not None:
                    self.panel_paused = True
                self.alert_session, self.silenced_through, self.resumed_at = alerts.get("session"), 0, None
            if type(alerts.get("paused")) is not bool:
                self.last_id = alerts.get("latest_id")
                if self.panel_paused:
                    self.silenced_through = self.last_id
                elif self.resumed_at is not None:
                    for event in alerts.get("events") or []:
                        if isinstance(event, dict) and _number(event.get("at")) and event["at"] <= self.resumed_at:
                            self.silenced_through = max(self.silenced_through, event.get("id", 0))
                alerts = {**alerts, "paused": self.panel_paused, "silenced_through": self.silenced_through}
            else:
                self.panel_paused = alerts["paused"]
            if self.automation_paused:
                alerts = {**alerts, "paused": True}
            self.consumer.consume(alerts, [] if alerts.get("paused") else visible_players(data, self.now()))
        if not was_muted and self.muted() and self.on_mute:
            self.on_mute()
        recording = (data.get("cavebot") or {}).get("recording") if isinstance(data.get("cavebot"), dict) else None
        if not isinstance(recording, dict) or not recording.get("active") or recording.get("mode") != "adaptive":
            self.notified_recording = None
        elif (recording.get("name") and (recording.get("hunt_progress") or {}).get("stage") == 4
              and self.notified_recording != recording["name"]):
            self.notified_recording = recording["name"]
            if isinstance(alerts, dict) and not self.muted() and self.on_hunt_ready:
                self.on_hunt_ready()

    def start(self):
        def run():
            while not self.stopped:
                self.poll_once()
                self._wake.wait(POLL_SECONDS)
        threading.Thread(target=run, name="panel-alerts", daemon=True).start()
        return self

    def stop(self):
        self.stopped = True
        self._wake.set()


class NotificationAudio:
    """At most one sound at a time, through the hidden Windows helper."""

    def __init__(self, fallback, *, run=None, platform=sys.platform, helper=ALERT_HELPER, python=None):
        self.fallback, self.platform, self.helper = fallback, platform, Path(helper)
        self.python = python or str(Path(sys.executable).with_name("pythonw.exe"))
        self.run = run or self._spawn
        self.child, self.stopped = None, False
        self._lock = threading.RLock()

    def _spawn(self, args, on_exit):
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

        def wait():
            try:
                code = proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()
                code = 1
            on_exit(code)
        threading.Thread(target=wait, daemon=True).start()
        return proc

    def interrupt(self):
        with self._lock:
            playing, self.child = self.child, None
        if playing is not None:
            try:
                playing.kill()
            except OSError:
                pass

    def play(self, kind):
        with self._lock:
            if self.stopped or self.child is not None:
                return False
            if self.platform != "win32":
                self.fallback()
                return True
            kind = kind if kind in ("message", "vitals") else "player"
            holder = {}

            def done(code):
                with self._lock:
                    if self.child is not holder.get("proc"):
                        return
                    self.child = None
                if code and not self.stopped:
                    self.fallback()
            try:
                holder["proc"] = self.child = self.run([self.python, "-I", "-B", str(self.helper), kind], done)
            except OSError:
                self.child = None
                self.fallback()
            return True

    def stop(self):
        self.stopped = True
        self.interrupt()
