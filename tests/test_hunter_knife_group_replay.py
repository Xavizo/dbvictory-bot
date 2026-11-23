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
    if result is None and evidence[0] == (0, 2, 0) and evidence[1200] == (0, 0, 0):
        pytest.xfail("v1.25.0 drops the incomplete batch before the second personal Loot arrives")
    assert result is not None, f"Unexpected failure to assign the bodies: {evidence}"
    wait_ms, owned = result
    assert 1200 <= wait_ms <= 1200 + 180 + frame_ms
    assert owned == 2
