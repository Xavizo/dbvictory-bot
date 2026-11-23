"""Personal ownership evidence from manual area kills, independent of knife selection."""
from types import SimpleNamespace

import pytest

from hunter_knife import FRESH, SPENT, SkinningQueue
from test_hunter_knife import NAME, tile


ORIGIN = (1949, 1290, 4)
POINTS = [(1948, 1290, 4), (1950, 1290, 4), (1950, 1289, 4), (1948, 1289, 4)]
FOREIGN = (1949, 1291, 4)
REWARD = 289801


@pytest.mark.parametrize(("case", "expected"), [
    ("aggregate", 4), ("separate_awards", 4), ("foreign_present", 0),
    ("five_deaths_four_awards", 0), ("untrained", 0), ("changed_reward", 0),
    ("partial_loot", 0), ("no_target", 0), ("spent_manually", 0), ("party", 0), ("bad_stats_before_wave", 4),
])
def test_measured_rewards_for_isolated_wave(case, expected):
    game = SimpleNamespace(position=ORIGIN, experience=4000000000, attack_target_id=0, creatures={})
    queue = SkinningQueue()
    contents = {p: [] for p in [*POINTS, FOREIGN, ORIGIN]}
    contents[FOREIGN] = [FRESH]  # already present when we arrived
    start = 100.

    def frame(at):
        cells = []
        for p, items in contents.items():
            monsters = [c for c in game.creatures.values() if (c["x"], c["y"], c["z"]) == p]
            for c in monsters:
                c["t"] = at
            occupants = {"monster": len(monsters), "player": int(case == "foreign_present" and at >= 105. and p == FOREIGN)}
            cells.append(dict(tile(p, *items), occupants=sum(occupants.values()), occupant_types=occupants))
        queue.observe(cells, at, game=game, target_id=game.attack_target_id, sampled_at=at)

    def appear(cid, p):
        game.creatures[cid] = dict(name=NAME, x=p[0], y=p[1], z=p[2], health=85, t=0.)

    def kill(cid, p, at):
        game.creatures.pop(cid)
        queue.death(dict(name=NAME, cid=cid, x=p[0], y=p[1], z=p[2]), at)
        contents[p].append(FRESH)

    # Learn this session's per-monster reward from two isolated successful transformations.
    for i in range(2 if case != "untrained" else 1):
        p = POINTS[i]
        cid = 100+i
        game.attack_target_id = cid
        appear(cid, p)
        frame(start)
        kill(cid, p, start+.1)
        game.experience += REWARD
        frame(start+.1)
        frame(start+.31)
        assert set(queue.stacks) == {p} and queue.stacks[p].reward_sample == (NAME.casefold(), REWARD)
        contents[p] = [SPENT]
        queue.consumed(p, confirmed=case != "spent_manually")
        frame(start+.45)
        contents[p].clear()
        # Continuous map samples keep the source alive between fights.
        for step in range(2, 9):
            frame(start + step*.25 + .2)
        start += 2.5

    if case == "bad_stats_before_wave":
        game.level = 623
        good = game.experience
        game.experience = 266642023582697  # out-of-level spike observed in the live session
        frame(start-.15)
        game.experience = good
        frame(start-.05)
    before = queue.from_combat
    game.player_party_shield = 1 if case == "party" else 0
    game.attack_target_id = 0 if case == "no_target" else 200
    wave = [*POINTS, ORIGIN] if case == "five_deaths_four_awards" else POINTS
    for i, p in enumerate(wave):
        appear(200+i, p)
    frame(start)
    for i, p in enumerate(wave):
        kill(200+i, p, start+.1)
    if case == "partial_loot":
        queue.personal_loot(f"[{NAME}]: nothing.", start+.1)
    if case != "separate_awards":
        game.experience += REWARD * 4 + int(case == "changed_reward")
    for tick in range(1, 61):
        if case == "separate_awards" and tick <= 4:
            game.experience += REWARD
        frame(start+tick*.05)
        if tick == 16:
            assert queue.possible_work(ORIGIN, start+tick*.05, include_owned=False) == bool(expected)
    assert queue.from_combat - before == expected
    assert set(queue.stacks) == (set(POINTS) if expected else set())
    assert FOREIGN not in queue.stacks
