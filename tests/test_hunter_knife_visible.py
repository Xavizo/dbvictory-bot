"""Exercise the normal item worker on a deterministic map. The transport stays in memory."""
import asyncio
from types import SimpleNamespace

import pytest

from dbv.brain.combat import hunter_knife_action as knife
from dbv.client_bridge.command_delivery import CommandReceipt
from dbv.brain.state.game_state import GameState
from dbv.brain.combat.hunter_knife import FRESH, SPENT, publish_feedback
from test_hunter_knife import NAME, tile


CENTER = (1934, 1276, 7)
FOREIGN = (1935, 1277, 7)
OWN = ((1933, 1275, 7), (1934, 1275, 7), (1935, 1275, 7),
       (1933, 1276, 7), (1933, 1277, 7), (1934, 1277, 7))
DISTANT = (1936, 1276, 7)
OTHER_FLOOR = (1934, 1275, 6)
OCCUPIED = (1935, 1276, 7)


class KnifeReplay:
    def __init__(self, monkeypatch, cavebot=False):
        self.now = 100.
        self.tick = 0
        self.game = GameState()
        self.game.position, self.game.hp = CENTER, 100
        self.contents = {}
        self.occupied = set()
        self.attempts = []
        self.monitor = SimpleNamespace()
        self.config = {"enabled": True}
        combat = SimpleNamespace(config={"enabled": True}, target=None, sent_target=0)
        controller = SimpleNamespace(status="running", combat=combat) if cavebot else None
        state = SimpleNamespace(game_state=self.game, game_proxy=object(), automation_paused=False,
                                adaptive_cavebot=controller, passive_map_monitor=self.monitor,
                                settings={"actions": {knife.NAME: self.config}})
        self.bot = SimpleNamespace(state=state, player_id=10, position=CENTER, is_connected=True)
        self.bot.inject_to_server = self.dispatch
        self.outcome = None
        monkeypatch.setattr(knife, "time", SimpleNamespace(monotonic=lambda: self.now, time=lambda: self.now))
        monkeypatch.setattr("dbv.client_bridge.passive_map_win32.ReadOnlyClient", lambda: SimpleNamespace(close=lambda: None))
        monkeypatch.setattr("dbv.client_bridge.client_player_state.ClientPlayerReader", lambda *_: object())
        reader = SimpleNamespace(snapshot=lambda: {"slots": {6: 5908}, "sources": []})
        monkeypatch.setattr("dbv.client_bridge.client_equipment.EquipmentReader", lambda *_: reader)

    def publish(self):
        cells = []
        for p, bodies in self.contents.items():
            count = int(p in self.occupied)
            cells.append(dict(tile(p, *bodies), occupants=count, occupant_types={"monster": count}))
        self.monitor._result = dict(status="ready", center=self.game.position, cells=cells, captured_at=self.now)

    async def dispatch(self, _payload, guard):
        assert guard()
        attempt = self.game.hunter_knife.pending
        assert attempt.drop is None
        self.attempts.append((attempt.source, attempt.item["stack"]))
        receipt = CommandReceipt(len(self.attempts), 1, "server", 0)
        receipt.finish("dispatched")
        self.bot.last_receipt = receipt
        assert self.outcome is None
        self.outcome = attempt

    async def run(self, monkeypatch, step, limit=150):
        async def advance(*_args):
            self.tick += 1
            self.now = 100. + self.tick / 10.
            worker = self.game.hunter_knife
            self.worker = worker
            step(self)
            self.publish()
            if self.tick >= limit:
                self.config["enabled"] = False

        self.publish()
        monkeypatch.setattr(knife, "wait_for_inputs", advance)
        await knife.run_knife(self.bot)

    def answer(self, *, denied):
        a, self.outcome = self.outcome, None
        if denied:
            message = SimpleNamespace(messages=[{"text": "Sorry, you are not owner."}], knife_feedback=[])
            publish_feedback(self.game, message, (), self.now)
        else:
            index = a.item["stack"] - 2  # tile() adds the ground and decoration first.
            assert self.contents[a.source][index] == FRESH
            self.contents[a.source][index] = SPENT


@pytest.mark.parametrize("cavebot", [False, True], ids=["manual", "cavebot"])
def test_six_simultaneous_kills_one_foreign_no_loot(monkeypatch, cavebot):
    sim = KnifeReplay(monkeypatch, cavebot)
    for p in (OWN[0], FOREIGN, *OWN[1:], DISTANT, OTHER_FLOOR, OCCUPIED):
        sim.contents[p] = []
    sim.occupied.add(OCCUPIED)

    def step(s):
        if s.tick == 1:
            for idx, p in enumerate(OWN):
                death = {"cid": idx+1, "name": NAME, "x": p[0], "y": p[1], "z": p[2]}
                s.worker.queue.death(death, s.now)
            for p in s.contents:
                s.contents[p] = [FRESH]
        if s.outcome:
            assert s.outcome.source in (*OWN, FOREIGN)
            s.answer(denied=s.outcome.source == FOREIGN)

    asyncio.run(sim.run(monkeypatch, step))
    assert sorted(p for p, _ in sim.attempts) == sorted([*OWN, FOREIGN])
    assert sim.attempts[-1][0] != FOREIGN  # continue with untried bodies after the denial
    assert sim.worker.done == 6
    assert sim.game.knife_loot_count == 0 and sim.game.knife_denials == 1
    assert all(sim.contents[p] == [SPENT] for p in OWN)
    assert all(sim.contents[p] == [FRESH] for p in (FOREIGN, DISTANT, OTHER_FLOOR, OCCUPIED))


def test_refused_body_survives_new_body_then_uncover_and_repeated_frames(monkeypatch):
    sim = KnifeReplay(monkeypatch)
    sim.contents[FOREIGN] = []
    # Isolate the stack. Repeated observations, including those after the
    # ownership evidence has expired, must never retry its denied bottom body.
    def step(s):
        if s.tick == 1:
            s.contents[FOREIGN] = [FRESH]
        elif s.tick == 100:
            s.contents[FOREIGN].append(FRESH)
        if s.outcome:
            s.answer(denied=len(s.attempts) == 1)
        if s.tick == 120:
            assert s.contents[FOREIGN] == [FRESH, SPENT]
            s.contents[FOREIGN].pop()
        if s.tick == 140:
            assert s.contents[FOREIGN] == [FRESH]
            s.contents[FOREIGN].clear()
        if s.tick == 145:
            s.contents[FOREIGN].append(FRESH)

    asyncio.run(sim.run(monkeypatch, step, limit=170))
    assert sim.attempts == [(FOREIGN, 2), (FOREIGN, 3), (FOREIGN, 2)]
    assert sim.worker.done == 2 and sim.game.knife_denials == 1


def test_return_to_range_after_timeout_allows_only_one_more_use(monkeypatch):
    sim = KnifeReplay(monkeypatch)
    sim.contents[FOREIGN] = [FRESH]

    def step(s):
        s.outcome = None
        if s.tick == 2:
            s.bot.position = s.game.position = DISTANT[0]+2, DISTANT[1], DISTANT[2]
        if s.tick == 25:
            del s.contents[FOREIGN]
        if s.tick == 60:
            s.contents[FOREIGN] = [FRESH]
        if s.tick == 90:
            s.bot.position = s.game.position = CENTER
        if s.tick <= 91:
            assert len(s.attempts) == 1
        if s.tick == 92:
            assert len(s.attempts) == 2

    asyncio.run(sim.run(monkeypatch, step))
    assert sim.attempts == [(FOREIGN, 2)] * 2
    assert sim.worker.done == 0 and sim.game.knife_denials == 0


@pytest.mark.parametrize("outcome", ["success", "denied", "late_success", "late_denial"])
def test_retry_requires_two_distinct_in_range_frames_after_timeout(monkeypatch, outcome):
    sim = KnifeReplay(monkeypatch)
    worker = sim.game.hunter_knife = knife.KnifeAutomation(sim.game)
    worker.tool_ready = True

    def frame(stamp, *items, now=None, occupied=False):
        cell = dict(tile(FOREIGN, *items), occupants=int(occupied), occupant_types={"monster": int(occupied)})
        return worker.next(dict(captured_at=stamp, cells=[cell]), stamp if now is None else now)

    first = worker.pending = frame(100., FRESH)
    assert first and not first.retry
    assert frame(101.5, FRESH) is None  # first attempt times out
    assert frame(101.6, FRESH) is None
    assert frame(101.6, FRESH, now=101.65) is None  # same map read twice
    assert frame(101.7, FRESH, occupied=True) is None
    assert frame(101.8, FRESH) is None
    assert frame(101.9, FRESH, SPENT) is None
    assert frame(102., FRESH) is None
    assert frame(103., FRESH) is None  # the earlier read is already stale

    def refuse(at):
        message = SimpleNamespace(messages=[{"text":"Sorry, you are not owner."}], knife_feedback=[])
        publish_feedback(sim.game, message, (), at)
        assert sim.game.knife_denials == 1

    if outcome == "late_denial":
        refuse(103.05)
    if outcome.startswith("late_"):
        body = SPENT if outcome == "late_success" else FRESH
        for t in (103.1, 103.2, 105.):
            assert frame(t, body) is None
        return

    retry = worker.pending = frame(103.1, FRESH)
    assert retry and retry.retry
    if outcome == "denied":
        refuse(103.15)
        assert frame(103.2, FRESH) is None
        assert frame(103.3, FRESH) is None
        assert worker.done == 0
    else:
        assert frame(103.2, SPENT) is None
        assert worker.done == 1


def test_group_finishes_after_first_body_times_out_and_one_foreign_refuses(monkeypatch):
    sim = KnifeReplay(monkeypatch, cavebot=True)
    for p in (OWN[0], FOREIGN, *OWN[1:]):
        sim.contents[p] = [FRESH]

    def step(s):
        if s.outcome is None:
            return
        if len(s.attempts) == 1:
            s.outcome = None  # the first fresh body receives no game response
        else:
            s.answer(denied=s.outcome.source == FOREIGN)

    asyncio.run(sim.run(monkeypatch, step))
    calls = [p for p, _ in sim.attempts]
    assert calls.count(OWN[0]) == 2
    assert all(calls.count(p) == 1 for p in (*OWN[1:], FOREIGN))
    assert sim.worker.done == 6 and sim.game.knife_denials == 1
    assert sim.game.knife_loot_count == 0 and sim.contents[FOREIGN] == [FRESH]


def test_toggling_module_does_not_repeat_denied_use_in_same_session(monkeypatch):
    sim = KnifeReplay(monkeypatch)
    sim.contents[FOREIGN] = [FRESH]

    def step(s):
        if s.outcome:
            s.answer(denied=True)

    asyncio.run(sim.run(monkeypatch, step, limit=3))
    sim.config["enabled"] = True
    asyncio.run(sim.run(monkeypatch, step, limit=10))
    assert sim.attempts == [(FOREIGN, 2)]
    assert sim.game.knife_denials == 1
