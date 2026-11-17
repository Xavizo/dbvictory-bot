"""Temporal pursuit traces only; no client, network or bot action is started."""
import pytest
from cavebot_lure import PursuitTracker


def mob(x, y=10, cid=1, name='Wolf', z=7):
    return dict(id=cid, name=name, position=[x, y, z], distance=1)


def confirmed():
    tracker = PursuitTracker()
    assert not tracker.update((10, 10, 7), [mob(8)], 0)
    assert not tracker.update((11, 10, 7), [mob(9)], .3)
    assert tracker.update((12, 10, 7), [mob(10)], .6)
    return tracker


def test_visible_stationary_mobs_and_player_approach_never_count():
    tracker = PursuitTracker()
    for step in range(5):
        mobs = [mob(8, 8+i, i+1) for i in range(4)]
        assert not tracker.update((10+step, 10, 7), mobs, step*.3)
    tracker.reset()
    for step in range(3):
        assert not tracker.update((10-step, 10, 7), [mob(6)], step*.3)


def test_two_separate_responses_required_and_duplicate_snapshots_do_not_count():
    tracker = PursuitTracker()
    tracker.update((10, 10, 7), [mob(7)], 0)
    assert not tracker.update((11, 10, 7), [mob(8)], .3)
    assert not tracker.update((11, 10, 7), [mob(8)], .4)
    # Another monster step after the same player step is not a second proof.
    assert not tracker.update((11, 10, 7), [mob(9)], .5)
    assert tracker.update((12, 10, 7), [mob(10)], .6)


def test_delayed_followers_can_catch_up_while_player_waits():
    tracker = PursuitTracker()
    tracker.update((10, 10, 7), [mob(8)], 0)
    tracker.update((11, 10, 7), [mob(8)], .2)
    assert not tracker.update((11, 10, 7), [mob(9)], .4)
    tracker.update((12, 10, 7), [mob(9)], .6)
    assert tracker.update((12, 10, 7), [mob(10)], .8)


def test_crossing_and_opposite_motion_do_not_confirm():
    tracker = PursuitTracker()
    tracker.update((10, 10, 7), [mob(10, 13)], 0)
    assert not tracker.update((11, 10, 7), [mob(10, 12)], .3)
    assert not tracker.update((12, 10, 7), [mob(10, 13)], .6)
    assert not tracker.update((13, 10, 7), [mob(10, 14)], .9)


def test_followers_survive_turns_and_diagonal_steps():
    tracker = confirmed()
    assert tracker.update((13, 11, 7), [mob(11, 11)], .9)
    assert tracker.update((13, 12, 7), [mob(12, 11)], 1.2)


@pytest.mark.parametrize('break_kind', ['lost', 'floor', 'player_jump', 'monster_jump', 'identity', 'gap', 'reset'])
def test_lost_identity_or_discontinuity_drops_old_confirmation(break_kind):
    tracker = confirmed()
    pos, mobs, now = (12, 10, 7), [mob(10)], .8
    if break_kind == 'lost':
        assert not tracker.update(pos, [], now)
    elif break_kind == 'floor':
        pos, mobs = (12, 10, 6), [mob(10, z=6)]
    elif break_kind == 'player_jump':
        pos = (15, 10, 7)
    elif break_kind == 'monster_jump':
        mobs = [mob(7)]
    elif break_kind == 'identity':
        mobs = [mob(10, name='Other Wolf')]
    elif break_kind == 'gap':
        now = 4
    else:
        tracker.reset()
    assert not tracker.update(pos, mobs, now)


def test_nonfollowing_confirmation_expires_but_adjacent_attacker_remains():
    tracker = confirmed()
    for now in (1, 2, 3):
        tracker.update((12, 10, 7), [mob(10)], now)
    assert not tracker.update((12, 10, 7), [mob(10)], 4)
    tracker = confirmed()
    for now in (1, 2, 3, 4, 5):
        assert tracker.update((12, 10, 7), [mob(11)], now)
