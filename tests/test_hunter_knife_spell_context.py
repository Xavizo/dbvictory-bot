"""Spell context describes an item attempt without adding an item cooldown."""
from types import SimpleNamespace as NS

import pytest

import hunter_knife_action as knife
from hunter_knife import FRESH, SPENT, OwnedStack
from test_hunter_knife import A, tile


@pytest.mark.parametrize("elapsed, spell_shown", [(0.07, True), (3.5, False), (None, False)])
@pytest.mark.parametrize("changed", [True, False])
def test_adjacent_owned_body_after_spell(monkeypatch, elapsed, spell_shown, changed):
    last_spell = None if elapsed is None else dict(words="shunkan", at=1000.-elapsed)
    combat = NS(config={"enabled":True}, target=None, sent_target=None,
                last_spell_attempt=last_spell)
    game = NS(attack_target_id=None, creatures={}, position=A, experience=100)
    monkeypatch.setattr(knife, "time", NS(time=lambda: 1000., monotonic=lambda: 10.))
    worker = knife.KnifeAutomation(game, NS(combat=combat, status="running"))
    worker.tool_ready = True
    worker.queue.stacks[A] = OwnedStack(1, 18., 9.8, 9.9)
    attempt = worker.next(dict(captured_at=1000., cells=[tile(A, FRESH)]), 10.)
    assert attempt and attempt.source == A
    worker.pending = attempt
    body = SPENT if changed else FRESH
    worker.acknowledge({A:tile(A, body)}, 1001.6, 11.6)
    if changed:
        assert worker.done == 1 and worker.last_issue is None
    else:
        assert worker.done == 0 and worker.pending is None
        issue = worker.last_issue
        assert issue["position"] == A and issue["reason"] == "Brak potwierdzenia zmiany pola"
        assert ("spell" in issue) == spell_shown
        if spell_shown:
            assert issue["since_spell_ms"] == 70


@pytest.mark.parametrize("size", [4, 8])
def test_after_area_spell_all_owned_neighbors_are_processed_without_pausing_combat(monkeypatch, size):
    from test_hunter_knife import NAME

    clock = [100.]
    monkeypatch.setattr(knife, "time", NS(time=lambda: clock[0], monotonic=lambda: clock[0]))
    spots = [(A[0]+dx, A[1]+dy, A[2]) for dy in (-1, 0, 1) for dx in (-1, 0, 1)
             if dx or dy][:size]
    target = dict(id=700, name="Wolf", x=A[0]+2, y=A[1], z=A[2], health=80, t=100.)
    combat = NS(config={"enabled": True}, target=target, sent_target=700,
                last_spell_attempt=dict(words="shunkan", at=100.))
    game = NS(attack_target_id=700, creatures={700:target}, position=A, experience=1000)
    worker = knife.KnifeAutomation(game, NS(combat=combat, status="running"))
    worker.tool_ready = True
    worker.next(dict(captured_at=100., cells=[tile(p) for p in spots]), 100.)
    for cid, p in enumerate(spots, 1):
        worker.queue.death(dict(name=NAME, cid=cid, x=p[0], y=p[1], z=p[2]), 100.01)
        worker.queue.personal_loot(f"[{NAME}]: nothing.", 100.01)
    finished = set()
    for tick in range(1, 25):
        clock[0] = target["t"] = 100. + tick / 20
        # Each distinct use is followed by an observed transformation on its tile.
        cells = [tile(p, SPENT if p in finished else FRESH) for p in spots]
        attempt = worker.next(dict(captured_at=clock[0], cells=cells), clock[0])
        if attempt:
            assert attempt.source not in finished and attempt.drop is None
            worker.pending = attempt
            finished.add(attempt.source)
        if worker.done == size:
            break
    assert finished == set(spots) and worker.done == size
    assert worker.fighting() and not worker.hold()
