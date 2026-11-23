"""Single-target knife ownership with simultaneous and delayed kills, without a client."""
import pytest

from hunter_knife import FRESH, SPENT, SkinningQueue
from test_hunter_knife import A, B, NAME, tile

FURY = "Fury Mutant Fire Elemental"


def death(queue, point, name, cid, now):
    queue.death(dict(name=name, cid=cid, x=point[0], y=point[1], z=point[2]), now)


def test_unrelated_species_on_another_tile_never_delays_the_first_owned_body():
    def first_attempt(second):
        queue = SkinningQueue()
        queue.observe([tile(A), tile(B)], 99.9)
        death(queue, A, NAME, 1, 100.)
        queue.personal_loot(f"[{NAME}]: nothing.", 100.)
        if second:
            death(queue, B, FURY, 2, 100.)
        ready = {}
        for ms in range(0, 1701, 50):
            now = 100. + ms / 1000
            if second and ms == 1200:
                queue.personal_loot(f"[{FURY}]: nothing.", now)
            # Simulate processing A as soon as it is ready, while B waits for Loot.
            a = tile(A, SPENT if A in ready else FRESH)
            b = tile(B, FRESH) if second else tile(B)
            queue.observe([a, b], now)
            for p, cell in ((A, a), (B, b)):
                if queue.next_body(p, cell["items"]) and p not in ready:
                    ready[p] = ms
                    queue.consumed(p)
        assert queue.unassigned_bodies == 0
        return ready
    assert first_attempt(False) == {A: 200}
    assert first_attempt(True) == {A: 200, B: 1400}


@pytest.mark.parametrize("same_tile", [False, True])
def test_simultaneous_same_species_needs_one_personal_credit_per_death(same_tile):
    queue = SkinningQueue()
    queue.observe([tile(A), tile(B)], 20.)
    death(queue, A, NAME, 1, 20.05)
    death(queue, A if same_tile else B, NAME, 2, 20.05)
    queue.personal_loot(f"[{NAME}]: nothing.", 20.05)
    cells = [tile(A,FRESH,FRESH),tile(B)] if same_tile else [tile(A,FRESH),tile(B,FRESH)]
    for now in (20.1, 20.49, 21.2):
        queue.observe(cells, now)
        assert not queue.stacks
        assert queue.unassigned_bodies == 0
    queue.personal_loot(f"[{NAME}]: nothing.", 21.25)
    queue.observe(cells, 21.45)
    assert sum(s.fresh for s in queue.stacks.values()) == 2
    assert queue.unassigned_bodies == 0


def test_different_species_sharing_tile_wait_for_every_personal_credit():
    queue = SkinningQueue()
    queue.observe([tile(A)], 100.)
    for name, cid in ((NAME, 1), (FURY, 2)):
        death(queue, A, name, cid, 100.1)
    queue.personal_loot(f"[{NAME}]: nothing.", 100.1)
    cells = [tile(A,FRESH,FRESH)]
    queue.observe(cells, 100.1)
    queue.settle(100.4)
    assert not queue.stacks
    queue.personal_loot(f"[{FURY}]: nothing.", 101.1)
    queue.settle(101.3)
    assert queue.stacks[A].fresh == 2
    assert queue.unassigned_bodies == 0


def test_independent_windows_do_not_discard_later_complete_species():
    queue = SkinningQueue()
    queue.observe([tile(A),tile(B)], 10.)
    death(queue, A, NAME, 10, 10.1)
    queue.observe([tile(A,FRESH),tile(B)],10.1)
    death(queue, B, FURY, 20, 11.5)
    queue.observe([tile(A,FRESH),tile(B,FRESH)], 11.5)
    queue.personal_loot(f"[{FURY}]: nothing.", 11.8)
    queue.settle(12.1)
    assert set(queue.stacks) == {B}
    assert queue.unassigned_bodies == 1  # A was observed but had no personal credit.


def test_connected_mixed_stack_without_full_evidence_never_grants_or_double_counts():
    queue = SkinningQueue()
    queue.observe([tile(A),tile(B,FRESH)], 10.)
    death(queue, A, NAME, 1, 10.2)
    death(queue, A, FURY, 2, 10.2)
    queue.personal_loot(f"[{NAME}]: nothing.", 10.2)
    cells = [tile(A,FRESH,FRESH),tile(B,FRESH)]
    for now in (10.2,10.5,11.4,11.9,12.5,13.,14.):
        queue.observe(cells, now)
    assert not queue.stacks and queue.unassigned_bodies == 2


def test_unobserved_or_already_processed_body_is_not_counted_as_unassigned():
    queue = SkinningQueue()
    queue.observe([tile(A),tile(B,FRESH)], 10.)
    death(queue, A, NAME, 1, 10.1)
    queue.observe([tile(A,FRESH),tile(B,FRESH)], 10.1)
    queue.combat_claims[10.1, A] = 1
    queue.observe([tile(A,SPENT),tile(B,FRESH)], 10.45)
    queue.settle(11.8)
    assert queue.unassigned_bodies == 0

    death(queue, A, FURY, 2, 12.)
    queue.observe([tile(A,FRESH),tile(B,FRESH)], 12.)
    queue.observe([tile(B,FRESH)], 13.7)
    assert queue.unassigned_bodies == 0 and not queue.stacks


@pytest.mark.parametrize("uncertain", ["unnamed_death", "extra_body"])
def test_unknown_nearby_death_or_unaccounted_body_cannot_claim_another_players_kill(uncertain):
    queue = SkinningQueue()
    queue.observe([tile(A),tile(B)], 22.)
    death(queue, A, NAME, 1, 22.1)
    queue.personal_loot(f"[{NAME}]: nothing.", 22.1)
    if uncertain == "unnamed_death":
        death(queue, B, "", 2, 22.1)
    cells = [tile(A,FRESH),tile(B,FRESH)]
    queue.observe(cells, 22.1)
    queue.settle(22.4)
    queue.settle(23.8)
    assert not queue.stacks and queue.unassigned_bodies == 2


@pytest.mark.parametrize("count", [4, 8])
def test_owned_wave_at_distinct_adjacent_tiles_with_all_personal_credits(count):
    points = [(A[0]+dx,A[1]+dy,A[2]) for dy in (-1,0,1) for dx in (-1,0,1)
              if dx or dy][:count]
    queue = SkinningQueue()
    queue.observe([tile(p) for p in points], 30.)
    for cid, point in enumerate(points, 1):
        death(queue, point, NAME, cid, 30.1)
        queue.personal_loot(f"[{NAME}]: nothing.", 30.1)
    cells = [tile(p,FRESH) for p in points]
    queue.observe(cells, 30.1)
    queue.observe(cells, 30.3)
    assert set(queue.stacks) == set(points)
    for p, cell in zip(points,cells):
        assert queue.next_body(p,cell["items"])[0] == "skin"
        queue.consumed(p)
    assert not queue.stacks and queue.unassigned_bodies == 0


def test_lost_then_restored_tile_does_not_grant_an_unobserved_replacement_body():
    queue = SkinningQueue()
    queue.observe([tile(A),tile(B)], 10.)
    death(queue, A, NAME, 1, 10.1)
    queue.observe([tile(A,FRESH),tile(B)], 10.1)
    queue.observe([tile(B)], 10.15)
    queue.credit(NAME.casefold(),10.2)
    for now in (10.3,10.6,12.1):
        queue.observe([tile(A,FRESH),tile(B)], now)
    assert not queue.stacks and queue.unassigned_bodies == 0
