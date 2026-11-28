"""Opt-in packet journal: metadata of one game session, never payloads or keys.

The journal is off for every new game session. While on, it keeps bounded
in-memory rings and appends the same rows to a rolling JSONL file. Rows hold
time, direction, opcode name and frame size, plus parsed events the bot already
derives. Raw bytes, XTEA/session keys and the login connection never reach it.
Other players' chat keeps sender and kind; its text only with include_chat.
All methods run on the owner event loop.
"""
from collections import deque
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import time
from dbv import paths
from dbv.paths import INSTALL_ROOT

log = logging.getLogger(__name__)

ROOT = INSTALL_ROOT
JOURNAL_DIR = paths.JOURNAL_DIR
FILE_LIMIT = 5 * 1024 * 1024
FILE_COUNT = 5
RING = {"packets": 2000, "events": 1000, "chat": 300, "messages": 300}


def _opcode_name(enum, opcode):
    try:
        return enum(opcode).name
    except (ValueError, TypeError):
        return f"0x{opcode:02X}" if isinstance(opcode, int) else None


def _plain(value):
    """JSON-safe copy of parsed event details; drop bytes outright."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return None
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class RollingFile:
    def __init__(self, directory=None, limit=FILE_LIMIT, count=FILE_COUNT):
        self.directory = Path(directory or JOURNAL_DIR)
        self.limit, self.count = limit, count
        self.path = None
        self.handle = None
        self.written = 0
        self.sequence = 0

    def _open(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.sequence += 1
        self.path = self.directory / f"journal-{stamp}-{self.sequence:04d}.jsonl"
        self.handle = self.path.open("a", encoding="utf-8", newline="\n")
        self.written = 0
        files = sorted(self.directory.glob("journal-*.jsonl"))
        for old in files[:-self.count]:
            try:
                old.unlink()
            except OSError:
                pass

    def write(self, row):
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        size = len(line.encode("utf-8"))
        if self.handle is None or self.written + size > self.limit:
            self.close()
            self._open()
        self.handle.write(line)
        self.written += size

    def flush(self):
        if self.handle is not None:
            self.handle.flush()

    def close(self):
        if self.handle is not None:
            try:
                self.handle.close()
            finally:
                self.handle = None


class SessionJournal:
    def __init__(self, directory=None, clock=time.time):
        self._chosen = Path(directory) if directory else None
        self.directory = self._chosen or JOURNAL_DIR
        self.clock = clock
        self.enabled = False
        self.include_chat = False
        self.session = None
        self.started_at = None
        self.rings = {name: deque(maxlen=size) for name, size in RING.items()}
        self.counts = {name: 0 for name in RING}
        self.file = None
        self._last_flush = 0.

    # ── control ──────────────────────────────────────────────────────
    def start(self, session, include_chat=False, write_file=True):
        """Turn on for the given game session. Clears the previous session's rows."""
        self.stop()
        for ring in self.rings.values():
            ring.clear()
        self.counts = {name: 0 for name in RING}
        self.enabled, self.include_chat = True, bool(include_chat)
        self.session, self.started_at = session, self.clock()
        if self._chosen is None:  # the launcher may have moved .codex to .dbvictory since this process started
            self.directory = paths.state_dir(ROOT) / "journal"
        self.file = RollingFile(self.directory) if write_file else None
        self._row("events", {"kind": "journal_started", "include_chat": self.include_chat})
        return self.status()

    def stop(self, reason="stopped"):
        if self.enabled:
            self._row("events", {"kind": "journal_stopped", "reason": reason})
        self.enabled = False
        if self.file is not None:
            self.file.close()
        return self.status()

    def session_changed(self, session):
        """A journal switched on before login adopts that login; a later one ends it."""
        if not self.enabled:
            return
        if self.session is None:
            self.session = session
            self._row("events", {"kind": "session_adopted"})
        elif session != self.session:
            self.stop("session_changed")

    def status(self):
        return {"enabled": self.enabled, "include_chat": self.include_chat, "session": self.session,
                "started_at": self.started_at, "counts": dict(self.counts),
                "retained": {name: len(ring) for name, ring in self.rings.items()},
                "file": str(self.file.path) if self.file is not None and self.file.path else None,
                "directory": str(self.directory)}

    # ── recording ────────────────────────────────────────────────────
    def _row(self, ring, row):
        row = {"t": round(self.clock(), 3), "session": self.session, **row}
        self.rings[ring].append(row)
        self.counts[ring] += 1
        if self.file is not None:
            try:
                self.file.write({"ring": ring, **row})
                if row["t"] - self._last_flush > 1:
                    self.file.flush()
                    self._last_flush = row["t"]
            except OSError as error:
                log.warning("Packet journal file disabled: %s", error)
                self.file.close()
                self.file = None

    def client_packet(self, opcode, size):
        if self.enabled:
            from dbv.client_bridge.protocol import ClientOpcode
            self._row("packets", {"dir": "C>S", "opcode": opcode, "name": _opcode_name(ClientOpcode, opcode),
                                  "size": size})

    def server_frame(self, opcode, size, complete=True, lag_ms=None):
        if self.enabled:
            from dbv.client_bridge.protocol import ServerOpcode
            self._row("packets", {"dir": "S>C", "opcode": opcode, "name": _opcode_name(ServerOpcode, opcode),
                                  "size": size, "complete": complete, "lag_ms": lag_ms})

    def gap(self, reason):
        if self.enabled:
            self._row("events", {"kind": "observation_gap", "reason": reason})

    def server_events(self, events):
        if self.enabled:
            for at, kind, details in events:
                self._row("events", {"kind": kind, "at": at, "details": _plain(details)})

    def kills(self, kills):
        if self.enabled:
            for kill in kills:
                self._row("events", {"kind": "kill", "details": _plain(kill)})

    def chat(self, message):
        if self.enabled:
            row = {"kind": message.get("kind"), "sender": message.get("sender")}
            # Personal loot rows are the server's notes to this player, not someone's chat.
            if self.include_chat or message.get("kind") == "personal_loot":
                row["text"] = message.get("text")
            else:
                row["text_length"] = len(message.get("text") or "")
            self._row("chat", row)

    def server_messages(self, rows):
        """System text messages addressed to this player (loot, damage, look)."""
        if self.enabled:
            for row in rows:
                self._row("messages", {"type": row.get("type"), "kind": row.get("kind"), "text": row.get("text")})

    def note(self, kind, **details):
        if self.enabled:
            self._row("events", {"kind": kind, "details": _plain(details)})

    # ── export ───────────────────────────────────────────────────────
    def dump(self, extra=None, directory=None):
        """Write rings (and caller-supplied snapshots) to one JSON file; return its path."""
        if self.file is not None:
            self.file.flush()
        folder = Path(directory or self.directory)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"dump-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.json"
        body = {"dumped_at": self.clock(), "journal": self.status(),
                **{name: list(ring) for name, ring in self.rings.items()}, **_plain(extra or {})}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temporary, path)
        return path
