"""Offline timelines for ownership recognition with several corpses on one tile."""
import pytest

from hunter_knife import FRESH, SkinningQueue
from test_hunter_knife import A, NAME, tile


def replay_group(*, bodies, loot_times, frame_ms, before_credit=None):
    """Return (frame ms since bodies appeared, owned bodies), or None.

    Bodies and the reported deaths first appear together. Loot inputs arrive
    independently of map frames. No command is issued and no XP is invented.
    """
    queue = SkinningQueue()
    start = 100
    queue.observe([tile(A)], start - 0.1)
    for cid in range(1, bodies + 1):
        queue.death(dict(name=NAME, cid=cid, x=A[0], y=A[1], z=A[2]), start)

    stack = tile(A, *([FRESH] * bodies))
    frames = set(range(0, 3001, frame_ms))
    for ms in sorted(frames.union(loot_times)):
        now = start + ms / 1000
        for _ in range(loot_times.count(ms)):
            if before_credit is not None:
                before_credit[ms] = (len(queue.births), len(queue.deaths), len(queue.loot))
            queue.personal_loot(f"[{NAME}]: nothing.", now)
        if ms not in frames:
            continue
        queue.observe([stack], now)
        if A in queue.stacks:
            return ms, queue.stacks[A].fresh
    return None


@pytest.mark.parametrize("frame_ms", [50, 100, 250])
@pytest.mark.parametrize("bodies", [2, 4])
def test_matched_group_at_three_map_rates(bodies, frame_ms):
    loot_times = [0] * (bodies - 1) + [100]
    result = replay_group(bodies=bodies, loot_times=loot_times, frame_ms=frame_ms)
    assert result is not None
    wait_ms, owned = result
    assert 100 <= wait_ms <= 100 + 180 + frame_ms
    assert owned == bodies


@pytest.mark.parametrize("frame_ms", [50, 100, 250])
@pytest.mark.parametrize("loot_times", [[100], [0, 1800]])
def test_unmatched_or_out_of_window_group_keeps_ownership_unknown(loot_times, frame_ms):
    assert replay_group(bodies=2, loot_times=loot_times, frame_ms=frame_ms) is None


@pytest.mark.parametrize("frame_ms", [50, 100, 250])
def test_late_matching_own_loot_can_complete_group_within_evidence_window(frame_ms):
    # Control: identical initial map/death inputs, both personal credits arrive promptly.
    assert replay_group(bodies=2, loot_times=[0, 100], frame_ms=frame_ms) is not None
    # Both personal credits occur within the existing 1.5-second matching window.
    evidence = {}
    result = replay_group(bodies=2, loot_times=[0, 1200], frame_ms=frame_ms, before_credit=evidence)
    assert evidence[1200] == (1, 2, 1)
    assert result is not None, f"Unexpected failure to assign the bodies: {evidence}"
    wait_ms, owned = result
    assert 1200 <= wait_ms <= 1200 + 180 + frame_ms
    assert owned == 2


def test_new_personal_credit_resolves_on_still_fresh_map_without_counting_the_frame_twice():
    from types import SimpleNamespace
    from hunter_knife_action import KnifeAutomation

    game = SimpleNamespace(position=A, attack_target_id=None, creatures={}, experience=100)
    worker = KnifeAutomation(game)
    worker.next(dict(captured_at=100., cells=[tile(A)]), 100.)
    for cid in (3, 4):
        worker.queue.death(dict(name=NAME, cid=cid, x=A[0], y=A[1], z=A[2]), 100.1)
    stack = dict(captured_at=100.15, cells=[tile(A, FRESH, FRESH)])
    worker.queue.credit(NAME.casefold(), 100.15)
    assert worker.next(stack, 100.15) is None
    assert A not in worker.queue.stacks
    worker.queue.credit(NAME.casefold(), 100.2)
    assert worker.next(stack, 100.29) is None
    assert A not in worker.queue.stacks
    worker.tool_ready = True
    assert worker.next(stack, 100.4).source == A
    assert worker.queue.stacks[A].fresh == 2
    assert not worker.queue.births
    # An older pre-kill sample must not revoke a newer confirmed claim.
    assert worker.next(dict(captured_at=100.05, cells=[tile(A)]), 100.42) is None
    assert worker.frame == stack["captured_at"] and worker.queue.stacks[A].fresh == 2


def test_early_unmatched_loot_does_not_clear_the_following_batch():
    queue = SkinningQueue()
    queue.observe([tile(A)], 100.)
    queue.credit(NAME.casefold(), 100.)
    # After a gap the next frame has its own death, credit and body.
    queue.death(dict(name=NAME, cid=3, x=A[0], y=A[1], z=A[2]), 102.)
    queue.credit(NAME.casefold(), 102.)
    queue.observe([tile(A, FRESH)], 102.)
    queue.observe([tile(A, FRESH)], 102.25)
    assert queue.stacks[A].fresh == 1
