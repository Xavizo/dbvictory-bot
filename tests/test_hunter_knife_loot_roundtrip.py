"""Replay personal Loot through the normal event reader and the knife's tile acknowledgements."""
import struct
import time
from types import SimpleNamespace

import pytest

from game_state import GameState
from hunter_knife import FRESH, SPENT, publish_feedback
from hunter_knife_action import KnifeAutomation
from protocol import ServerOpcode
from test_hunter_knife import NAME, tile
from test_personal_loot_channels import channel_message, receive, roster


PLAYER = (1949, 1289, 4)
OWN = ((1948, 1290, 4), (1950, 1290, 4), (1950, 1289, 4), (1948, 1289, 4))
FOREIGN = (1949, 1290, 4)
LOOT_LINE = f"18:34 [{NAME}]: nothing."


@pytest.mark.parametrize("foreign_already_dead", [True, False], ids=["old-foreign", "simultaneous-foreign"])
def test_four_personal_loot_rows_to_verified_corpse_changes(foreign_already_dead):
    game = GameState()
    game.position = PLAYER
    worker = game.hunter_knife = KnifeAutomation(game)
    worker.tool_ready = True
    start = worker.started
    wall_start = time.time()
    points = (*OWN, FOREIGN)
    corpses = {FOREIGN: FRESH} if foreign_already_dead else {}

    def frame(elapsed):
        cells = [tile(point, corpses[point]) if point in corpses else tile(point) for point in points]
        return worker.next(dict(captured_at=wall_start + elapsed, cells=cells), start + elapsed)

    attempted = []

    def dispatch(attempt, elapsed):
        assert attempt.source not in attempted and attempt.drop is None and attempt.item["id"] == FRESH
        attempted.append(attempt.source)
        worker.pending = attempt
        if attempt.source == FOREIGN:
            feedback = SimpleNamespace(messages=[{"text": "Sorry, you are not owner."}], knife_feedback=[])
            publish_feedback(game, feedback, [], start + elapsed + .001)
        else:
            assert attempt.source in OWN
            corpses[attempt.source] = SPENT

    first = frame(0.)
    if first:
        assert foreign_already_dead and first.source == FOREIGN
        dispatch(first, 0.)
    assert receive(game, roster((12, "Trade"), (41, "Loot")), at=start + .01)
    # All kills look identical. Only the four anonymous rows from Loot are personal.
    deaths = bytearray()
    for cid, point in enumerate(OWN if foreign_already_dead else points, start=0x40000001):
        x, y, z = point
        game.creatures[cid] = dict(name=NAME, x=x, y=y, z=z, health=50, t=wall_start)
        deaths.extend(struct.pack("<BIB", ServerOpcode.CREATURE_HEALTH, cid, 0))
    noise = (channel_message(12, LOOT_LINE)
             + channel_message(41, LOOT_LINE, sender="Another player", level=100))
    assert receive(game, bytes(deaths) + noise + channel_message(41, LOOT_LINE) * 4, at=start + .1)
    assert game.session_kills == (4 if foreign_already_dead else 5)
    assert game.knife_loot_count == 4
    corpses.update(dict.fromkeys(points, FRESH))

    for tick in range(1, 23):
        attempt = frame(tick / 10)
        if attempt:
            dispatch(attempt, tick / 10)

    assert set(attempted) == set(points)
    assert worker.done == len(OWN) and game.knife_denials == 1
    assert corpses[FOREIGN] == FRESH and worker.pending is None
    assert FOREIGN not in worker.queue.stacks and worker.queue.from_combat == 0
