"""Ownership remains tied to the dead creature when its battle-list row has vanished."""
from collections import defaultdict

import pytest

from dbv.brain.state.game_state import GameState
from dbv.brain.combat.hunter_knife import FRESH
from dbv.brain.combat.hunter_knife_action import KnifeAutomation
from test_hunter_knife import NAME, tile


CENTER = (1949, 1290, 4)
BODIES = ((1948, 1290, 4), (1950, 1290, 4), (1950, 1289, 4), (1948, 1289, 4))
FOREIGN = (1949, 1291, 4)
IDS = tuple(0x40000110 + i for i in range(4))


def frame(game, now, actors, corpses, *, points=None, stale_name=False):
    actors = tuple(actors)
    positions = defaultdict(list)
    for cid, p in actors:
        positions[p].append(cid)
    cells = []
    slots = {}
    for p in (points if points is not None else (*BODIES, FOREIGN)):
        occupants = positions[p]
        cell = tile(p, *([FRESH] if p in corpses else []))
        cell.update(kind="clear", occupants=len(occupants), occupant_types={"monster": len(occupants)})
        for index, cid in enumerate(occupants, 2):
            slots[(*p, index)] = cid
        cells.append(cell)
    game.creatures = {cid: dict(name=NAME, health=50, source="dll", t=now - int(stale_name),
                                x=p[0], y=p[1], z=p[2]) for cid, p in actors}
    generation = game.pets.observe_map(cells, now, slots, game.creatures)
    return dict(cells=cells, captured_at=now, pet_map_generation=generation)


def fight():
    game = GameState()
    game.position = CENTER
    return game, KnifeAutomation(game)


def report(queue, cid, at, point=(0, 0, 0), name=""):
    queue.death(dict(cid=cid, name=name, x=point[0], y=point[1], z=point[2]), at)


@pytest.mark.parametrize("death_before_map", [True, False])
def test_four_disappearing_monsters_match_personal_loot_by_cid(death_before_map):
    game, worker = fight()
    queue = worker.queue
    before = frame(game, 100., zip(IDS, BODIES), {FOREIGN})
    worker.next(before, 100.)
    for cid in IDS:
        queue.credit(NAME.casefold(), 100.1)
        if death_before_map:
            report(queue, cid, 100.1)
    after = frame(game, 100.2, [], {*BODIES, FOREIGN})
    worker.next(after, 100.2)
    if not death_before_map:
        for cid in IDS:
            report(queue, cid, 100.24)
    worker.next(after, 100.5)
    assert set(queue.stacks) == set(BODIES)
    assert all(s.fresh == 1 for s in queue.stacks.values())
    assert queue.unassigned_bodies == 0


@pytest.mark.parametrize("problem", ["foreign", "unknown_cid", "stale_name", "stale_frame",
                                     "moved", "overlap", "unreadable", "conflict", "pet",
                                     "generation_gap", "superseded_frame", "reappears", "floor_change", "new_game"])
def test_missing_death_never_guesses_the_victim_from_loot_and_tile_alone(problem, monkeypatch):
    monkeypatch.setattr("dbv.brain.state.pet_tracking.time.time", lambda: 100.1)
    game, worker = fight()
    queue = worker.queue
    cid, p = IDS[0], BODIES[0]
    actors = [(cid, p)]
    if problem == "overlap":
        actors.append((IDS[1], p))
    before = frame(game, 100., actors, set(), stale_name=problem == "stale_name")
    worker.next(before, 100.)
    if problem == "pet":
        cell = next(c for c in before["cells"] if c.get("pet_ref"))
        game.pets.label_visible(p, cell["pet_ref"], NAME)
    if problem == "new_game":
        game = GameState()
        game.position = CENTER
        worker.game = game
    if problem == "floor_change":
        game.position = (*CENTER[:2], CENTER[2] - 1)
    if problem == "generation_gap":
        frame(game, 100.05, [], set())
    if problem != "foreign":
        queue.credit(NAME.casefold(), 100.1)
    report(queue, IDS[2] if problem == "unknown_cid" else cid, 100.1,
           FOREIGN if problem == "conflict" else (0, 0, 0))
    current_actors = [(cid, FOREIGN)] if problem == "moved" else []
    after = frame(game, 100.8 if problem == "stale_frame" else 100.2, current_actors, {p},
                  points=(FOREIGN,) if problem == "unreadable" else None)
    if problem == "superseded_frame":
        frame(game, 100.21, [], {p})
    worker.next(after, after["captured_at"])
    if problem == "reappears":
        after = frame(game, 100.3, [(cid, FOREIGN)], {p})
        worker.next(after, 100.3)
    worker.next(after, 102.)
    assert not queue.stacks
