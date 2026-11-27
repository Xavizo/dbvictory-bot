"""Session-owned recovery metadata. Only the transport owner issues controller leases."""
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4


class StaleController(RuntimeError):
    pass


class RecoveryOwner:
    """One private directory per supervisor; a new login always starts empty."""
    def __init__(self, directory):
        self.path = Path(directory) / "engine-recovery.sqlite3"
        # The relay keeps the WAL open across short controller transactions.
        # A machine reboot always ends this login; only process loss is recoverable.
        self._keeper = sqlite3.connect(self.path, check_same_thread=False)
        try:
            db = self._keeper
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS session (
                    id INTEGER PRIMARY KEY CHECK(id=1), login TEXT, lease TEXT, player INTEGER, route TEXT, plan TEXT
                );
                CREATE TABLE IF NOT EXISTS uses (
                    ticket TEXT PRIMARY KEY, item INTEGER, at REAL, gap REAL, shared REAL, pending INTEGER
                );
                CREATE INDEX IF NOT EXISTS uses_at ON uses(at);
            """)
        except BaseException:
            self.close()
            raise

    def close(self):
        self._keeper.close()

    def begin(self, login):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM uses")
            db.execute("INSERT OR REPLACE INTO session(id,login) VALUES (1,?)", (login,))

    def handover(self, login):
        """Call after the old controller and its outstanding writes have stopped."""
        lease = uuid4().hex
        with closing(sqlite3.connect(self.path)) as db, db:
            if not db.execute("UPDATE session SET lease=? WHERE id=1 AND login=?", (lease, login)).rowcount:
                raise StaleController("Session is no longer active")
            # A killed controller may have reached the sink before its receipt.
            db.execute("UPDATE uses SET pending=0, at=? WHERE pending=1", (time.monotonic(),))
        return lease

    def end(self, login):
        with closing(sqlite3.connect(self.path)) as db, db:
            if db.execute("DELETE FROM session WHERE login=?", (login,)).rowcount:
                db.execute("DELETE FROM uses")

    def revoke(self, lease):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE session SET lease=NULL WHERE lease=?", (lease,))


class EngineJournal:
    def __init__(self, path, login, lease, player):
        if type(player) is not int or player <= 0:
            raise ValueError("A fresh player identity is required")
        self.path, self.login, self.lease, self.player = Path(path), login, lease, player
        self._uri = self.path.resolve().as_uri() + "?mode=rw"
        with self.transaction() as db:
            db.execute("UPDATE session SET player=? WHERE player IS NULL", (player,))

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(self._uri, uri=True, timeout=.25)) as db, db:
            db.execute("PRAGMA synchronous=NORMAL")
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM session WHERE id=1 AND login=? AND lease=? "
                              "AND (player IS NULL OR player=?)", (self.login, self.lease, self.player)).fetchone():
                raise StaleController("Controller, login or player changed")
            yield db

    def current(self):
        try:
            with closing(sqlite3.connect(self._uri, uri=True, timeout=.05)) as db:
                return db.execute("SELECT 1 FROM session WHERE login=? AND lease=? AND player=?",
                                  (self.login, self.lease, self.player)).fetchone() is not None
        except sqlite3.Error:
            return False

    def save_route(self, route, plan=None):
        data = json.dumps(route, allow_nan=False, separators=(",", ":")) if route is not None else None
        with self.transaction() as db:
            if plan is not None or route is None:
                db.execute("UPDATE session SET plan=? WHERE id=1",
                           (json.dumps(plan, allow_nan=False, separators=(",", ":")) if plan else None,))
            db.execute("UPDATE session SET route=? WHERE id=1", (data,))

    def route(self):
        with self.transaction() as db:
            raw, plan = db.execute("SELECT route,plan FROM session WHERE id=1").fetchone()
        return {**json.loads(raw), **json.loads(plan)} if raw and plan else None

    def reserve(self, item, gap, shared):
        with self.transaction() as db:
            now = time.monotonic()
            # All supported configured intervals are at most an hour. Keep
            # previous uses so later configuration changes still apply to them.
            db.execute("DELETE FROM uses WHERE pending=0 AND at < ?", (now - 3600,))
            blocked = db.execute("""
                SELECT 1 FROM uses WHERE pending=1 OR at + max(shared, ?) > ?
                OR (item=? AND at + max(gap, ?) > ?) LIMIT 1
            """, (shared, now, item, gap, now)).fetchone()
            if blocked:
                return None
            ticket = uuid4().hex
            db.execute("INSERT INTO uses VALUES(?,?,?,?,?,1)", (ticket, item, now, gap, shared))
            return ticket

    def settle(self, ticket, possible_write):
        with self.transaction() as db:
            if possible_write:
                db.execute("UPDATE uses SET pending=0, at=? WHERE ticket=? AND pending=1",
                           (time.monotonic(), ticket))
            else:
                db.execute("DELETE FROM uses WHERE ticket=? AND pending=1", (ticket,))

    def note_use(self, item, scheduler):
        with self.transaction() as db:
            db.execute("DELETE FROM uses WHERE pending=0 AND at < ?", (time.monotonic() - 3600,))
            db.execute("INSERT INTO uses VALUES (?,?,?,?,?,0)", (
                uuid4().hex, item, scheduler.last_use[item], scheduler.last_gap[item], scheduler.last_shared))

    def restore_cooldowns(self, scheduler):
        with self.transaction() as db:
            rows = db.execute("SELECT item,at,gap,shared FROM uses ORDER BY at").fetchall()
        for item, at, gap, shared in rows:
            end = max(at + gap, scheduler.last_use.get(item, float("-inf")) + scheduler.last_gap.get(item, 0))
            shared_end = max(at + shared, scheduler.last_any + scheduler.last_shared)
            scheduler.last_use[item] = max(at, scheduler.last_use.get(item, at))
            scheduler.last_gap[item] = end - scheduler.last_use[item]
            scheduler.last_any = max(at, scheduler.last_any)
            scheduler.last_shared = shared_end - scheduler.last_any

    def copy_observed_cooldowns(self, scheduler):
        """Seed a disposable diagnostic journal from already observed uses."""
        with self.transaction() as db:
            for item, at in scheduler.last_use.items():
                shared = max(0, scheduler.last_any + scheduler.last_shared - at) if at == scheduler.last_any else 0
                db.execute("INSERT INTO uses VALUES (?,?,?,?,?,0)", (
                    uuid4().hex, item, at, scheduler.last_gap.get(item, 0), shared))
