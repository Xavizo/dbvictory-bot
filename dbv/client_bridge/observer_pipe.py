r"""Client side of the observer DLL's named pipe.

The observer DLL (``dll/marker_observer.cpp``) runs inside the game client and
publishes one JSON snapshot per line on ``\\.\pipe\dbvbot_marker_<build>``::

    {"mode": "observer", "px": .., "py": .., "pz": .., "target_marker": true,
     "client_target": .., "marked_target": .., "target_marker_result": ..,
     "creatures": [{"id": .., "name": .., "hp": .., "x": .., "y": .., "z": ..}, ...]}

The only thing ever written back is the visual target marker lease::

    M <creature id or 0> <client target the request expects> <sequence>\n

Nothing else can be sent: there is no generic command channel, so the bot
cannot make the client run code or write memory through this pipe.
"""
from __future__ import annotations

import json
import logging
import time

try:  # The pipe only exists on Windows; tests swap in a fake API.
    import _winapi as _api
except ImportError:  # pragma: no cover - non-Windows hosts
    _api = None

log = logging.getLogger("observer_pipe")

_GENERIC_RW = 0x80000000 | 0x40000000
_OPEN_EXISTING = 3
_ERROR_SEM_TIMEOUT = 121
_ERROR_PIPE_BUSY = 231
_ERROR_MORE_DATA = 234

NO_TARGET = 0xFFFFFFFF
MONSTER_IDS = range(0x40000000, 0x80000000)
# The DLL publishes a fresh snapshot every few milliseconds; a marker request is
# only meaningful while that stream is live, and is repeated at most this often.
SNAPSHOT_FRESH_FOR = 0.5
MARKER_REPEAT_AFTER = 0.3


def _as_u32(value, fallback):
    return value if type(value) is int and 0 <= value <= 0xFFFFFFFF else fallback


class ObserverPipe:
    """One connection to the observer DLL of a verified client build."""

    def __init__(self, profile):
        from dbv.client_bridge.client_profiles import select_profile
        if not profile.observer or not profile.observer_pipe:
            raise ValueError("This client build has no observer DLL")
        if select_profile(profile.sha256) is not profile:
            raise ValueError("DLL profile does not match the verified client")
        self.profile = profile
        self.name = profile.observer_pipe.decode("ascii")
        self._handle = None
        self._pending = b""
        self.position = (0, 0, 0)
        # Marker state reported by the DLL ...
        self.marker_ready = False
        self.marker_seen = 0.0
        self.client_target = NO_TARGET
        self.marked_target = 0
        self.marker_result = 0
        # ... and what the bot wants it to show.
        self.marker_desired = 0
        self._sequence = 0
        self._last_sent = None
        self._last_sent_at = float("-inf")

    # ── connection ────────────────────────────────────────────────
    @property
    def connected(self) -> bool:
        return self._handle is not None

    def open(self) -> bool:
        if self._handle is not None:
            return True
        if _api is None:
            return False
        try:
            self._handle = _api.CreateFile(self.name, _GENERIC_RW, 0, 0, _OPEN_EXISTING, 0, 0)
        except OSError as exc:
            log.debug("observer pipe not available: %s", exc)
            return False
        self._pending = b""
        log.info("observer pipe connected")
        return True

    def exists(self) -> bool:
        """True while the DLL serves the pipe. Never takes its only connection."""
        if self._handle is not None:
            return True
        if _api is None:
            return False
        try:
            _api.WaitNamedPipe(self.name, 1)
        except OSError as exc:
            # A busy or slow pipe is still there; only a missing one is not.
            return getattr(exc, "winerror", None) in (_ERROR_SEM_TIMEOUT, _ERROR_PIPE_BUSY)
        return True

    def close(self):
        """Drop the connection; the DLL clears its marker when the lease runs out."""
        self.marker_desired = 0
        self.marker_ready = False
        self.marker_seen = 0.0
        self._last_sent = None
        handle, self._handle = self._handle, None
        self._pending = b""
        if handle is not None:
            try:
                _api.CloseHandle(handle)
            except OSError:
                pass
            log.info("observer pipe closed")

    # ── reading ───────────────────────────────────────────────────
    def _read_available(self) -> bytes:
        try:
            waiting, _ = _api.PeekNamedPipe(self._handle)
            if not waiting:
                return b""
            data, err = _api.ReadFile(self._handle, waiting)
        except OSError as exc:
            log.warning("observer pipe lost: %s", exc)
            self.close()
            return b""
        if err not in (0, _ERROR_MORE_DATA) or not data:
            self.close()
            return b""
        return data

    def poll(self) -> list[dict] | None:
        """Creatures from the newest complete snapshot, or None if nothing new arrived."""
        if self._handle is None:
            return None
        lines = (self._pending + self._read_available()).split(b"\n")
        self._pending = lines.pop()
        newest = None
        for raw in reversed(lines):
            try:
                # Names come from the client's memory and need not be UTF-8; one odd
                # byte must not hide the whole snapshot.
                message = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if isinstance(message, dict) and isinstance(message.get("creatures"), list):
                newest = message
                break
        if newest is None:
            return None
        self._absorb(newest)
        return newest["creatures"]

    def _absorb(self, snapshot):
        self.marker_ready = snapshot.get("target_marker") is True
        self.marker_seen = time.monotonic()
        self.client_target = _as_u32(snapshot.get("client_target"), NO_TARGET)
        self.marked_target = _as_u32(snapshot.get("marked_target"), 0)
        self.marker_result = snapshot.get("target_marker_result", 0)
        x = snapshot.get("px")
        if type(x) is int and x > 0:
            self.position = (x, snapshot.get("py", 0), snapshot.get("pz", 0))

    # ── visual target marker ──────────────────────────────────────
    def request_marker(self, creature_id) -> bool:
        """Remember which monster should be marked (0 clears). Does no pipe IO."""
        if type(creature_id) is not int or (creature_id and creature_id not in MONSTER_IDS):
            return False
        self.marker_desired = creature_id
        return True

    def renew_marker(self, *, active: bool) -> bool:
        """Send or extend the marker lease. Returns True when a request was written."""
        if not active:
            self.marker_desired = 0
        now = time.monotonic()
        live = self.marker_ready and 0 <= now - self.marker_seen < SNAPSHOT_FRESH_FOR
        if self._handle is None or not live or self.client_target == NO_TARGET:
            return False
        wanted = self.marker_desired
        if wanted == self._last_sent and now - self._last_sent_at < MARKER_REPEAT_AFTER:
            return False
        self._sequence += 1
        request = f"M {wanted} {self.client_target} {self._sequence}\n".encode("ascii")
        try:
            written, err = _api.WriteFile(self._handle, request)
        except OSError:
            return False
        if err or written != len(request):
            return False
        self._last_sent, self._last_sent_at = wanted, now
        return True
