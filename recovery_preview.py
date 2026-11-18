"""Read-only rehearsal on one live sample. No client or transport imports in the reader."""
import asyncio
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
from uuid import uuid4

def inspect(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        row = db.execute("SELECT route,plan FROM session WHERE id=1").fetchone()
        if not row:
            raise ValueError("Sesja została zamknięta.")
        last = upcoming = None
        if row[0] and row[1]:
            cursor, plan = map(json.loads, row)
            last = cursor["last_waypoint"]
            upcoming = next((c["waypoint"] for c in plan["checkpoints"] if c["index"] > cursor["index"]), last)
        uses = db.execute("SELECT item, max(at+gap) FROM uses GROUP BY item").fetchall()
        shared = db.execute("SELECT max(at+shared) FROM uses").fetchone()[0]
    return dict(last=last, upcoming=upcoming, deadlines={str(k): v for k, v in uses}, shared=shared)


async def preview(state):
    from session_lifetime import SessionLease
    lease = SessionLease(state)
    gs = state.game_state

    def fresh():
        sample = getattr(gs, "client_snapshot", None)
        if (not lease.valid() or not lease.player or gs.hp <= 0 or
                not 0 <= time.time() - gs.stats_updated_at <= 2 or
                (gs.client_state_required and (not sample or not 0 <= time.monotonic() - sample["monotonic"] <= .5))):
            raise ValueError("Poczekaj na świeży odczyt zalogowanej postaci.")
        return {"hp": gs.hp, "position": list(gs.position)}

    fresh()
    controller = getattr(state, "adaptive_cavebot", None)
    route = None
    last = upcoming = None
    if controller and controller.path and controller.route_draft and not controller.combat_only:
        route = deepcopy(controller.session_cursor())
        progress = controller.waypoint_progress()
        last, upcoming = progress["last_reached"], progress["current"]
    scheduler = state.potion_scheduler
    timers = SimpleNamespace(last_use=scheduler.last_use.copy(), last_gap=scheduler.last_gap.copy(),
                             last_any=scheduler.last_any, last_shared=scheduler.last_shared)
    # The copied sample never grants ownership of the user's active session.
    def inspect_sample():
        with TemporaryDirectory(prefix="dbv-recovery-preview-") as folder:
            from engine_recovery import EngineJournal, RecoveryOwner
            with closing(RecoveryOwner(folder)) as owner:
                login = uuid4().hex
                owner.begin(login)
                journal = EngineJournal(owner.path, login, owner.handover(login), lease.player)
                if route:
                    journal.save_route(*route)
                journal.copy_observed_cooldowns(timers)
                result = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve()), str(owner.path)],
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        timeout=5, check=True,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return json.loads(result.stdout)

    report = await asyncio.to_thread(inspect_sample)
    expected = {str(item): at + timers.last_gap.get(item, 0) for item, at in timers.last_use.items()}
    if (report["last"] != last or report["upcoming"] != upcoming or report["deadlines"] != expected or
            (expected and abs(report["shared"] - (timers.last_any + timers.last_shared)) > .001)):
        raise ValueError("Proces odczytu otrzymał inny postęp lub terminy mikstur.")
    return {"mode": "read_only_sample", **fresh(),
            "last_waypoint": last + 1 if last is not None else None,
            "next_waypoint": upcoming + 1 if upcoming is not None else None,
            "potion_seconds": {item: round(max(0, end - time.monotonic()), 2) for item, end in expected.items()}}


if __name__ == "__main__":
    print(json.dumps(inspect(sys.argv[1])))
