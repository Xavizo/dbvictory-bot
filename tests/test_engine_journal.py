import asyncio
from types import SimpleNamespace

import pytest

from dbv.brain.state import engine_recovery
from dbv.brain.cavebot.adaptive_cavebot import AdaptiveCavebot
from dbv.brain.state.engine_recovery import EngineJournal, RecoveryOwner, StaleController
from test_adaptive_cavebot import setup
from test_automation_safety import state


def test_dispatched_uncertain_rejected_and_expired_reservations(tmp_path, monkeypatch):
    now = [10.]
    monkeypatch.setattr(engine_recovery, "time", SimpleNamespace(monotonic=lambda: now[0]))
    owner = RecoveryOwner(tmp_path)
    owner.begin("one")
    def replace():
        return EngineJournal(owner.path, "one", owner.handover("one"), 101)
    old = replace()
    rejected = old.reserve(10161, 60, 5)
    old.settle(rejected, False)
    assert old.reserve(10161, 60, 5)  # dies between reservation and receipt
    now[0] += 2
    new = replace()
    with pytest.raises(StaleController):
        old.save_route(None)
    assert new.reserve(10161, 60, 5) is None
    assert new.reserve(10160, 5, 5) is None
    now[0] += 5
    ticket = new.reserve(10160, 5, 5)
    assert ticket
    new.settle(ticket, True)
    now[0] += 54.9
    assert new.reserve(10161, 60, 5) is None
    now[0] += .2
    assert new.reserve(10161, 60, 5)
    owner.end("one")
    with pytest.raises(StaleController):
        new.route()
    owner.begin("two")
    lease = owner.handover("two")
    fresh = EngineJournal(owner.path, "two", lease, 101)
    assert fresh.route() is None and fresh.reserve(10161, 60, 5)
    with pytest.raises(StaleController):
        EngineJournal(owner.path, "two", lease, 999)


def test_changed_draft_and_unconfirmed_destination_do_not_resume(state, tmp_path):
    route, _, draft = setup(state, tmp_path)
    owner = RecoveryOwner(tmp_path)
    owner.begin("one")
    journal = EngineJournal(owner.path, "one", owner.handover("one"), 101)
    state.game_state.engine_journal = journal
    route.save_session_cursor(pending=[11, 10, 7])
    restored = AdaptiveCavebot.from_session_cursor(state)
    assert restored.status == "paused"
    with pytest.raises(ValueError, match="Brak potwierdzenia"):
        asyncio.run(restored.resume_candidate())
    atlas = state.passive_map_monitor.atlas
    records = atlas.drafts()
    assert records[0]["id"] == draft
    records[0]["points"] = [[10, 10, 7], [11, 11, 7]]
    atlas.drafts = lambda: records
    assert AdaptiveCavebot.from_session_cursor(state) is None
