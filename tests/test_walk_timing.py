import asyncio
import logging
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dbv.brain.cavebot import adaptive_cavebot as nav
from dbv.brain.navigation import passive_map as pm
from dbv.brain.navigation.passive_map import PassiveMapMonitor


def test_confirmed_walk_measures_only_position_and_following_map(monkeypatch, caplog):
    now = [30.]
    monkeypatch.setattr(nav, "perf_counter", lambda: now[0])
    target = [11, 10, 7]
    state = SimpleNamespace(game_state=SimpleNamespace(position=tuple(target)))
    controller = nav.AdaptiveCavebot(state)
    caplog.set_level(logging.INFO, logger=nav.__name__)

    async def position(*args):
        now[0] += .030
        return nav.WalkOutcome.ARRIVED

    async def current_map():
        now[0] += .017
        return {"center": target}

    controller.wait_position = position
    controller._await_fresh_map = current_map

    async def scenario():
        assert await controller._confirm_walk(target, [10, 10, 7])
        now[0] += 2  # Combat or an idle interval does not belong to either phase.
        await controller.fresh_map()
        await controller.fresh_map()

    asyncio.run(scenario())
    timings = [r.getMessage() for r in caplog.records if r.msg.startswith("Walk timing")]
    assert len(timings) == 1 and "position_ms=30.0 map_ms=17.0 outcome=ready" in timings[0]


def test_stale_frame_wakes_reader_once_and_waits_for_matching_center():
    target = (11, 10, 7)
    monitor = Mock()
    frames = [dict(status="ready", center=[10, 10, 7], age_seconds=0)] * 2
    frames.append(dict(status="ready", center=list(target), age_seconds=0))
    monitor.snapshot.side_effect = frames
    controller = nav.AdaptiveCavebot(SimpleNamespace(
        passive_map_monitor=monitor, game_state=SimpleNamespace(position=target)))
    controller.guard = lambda: None
    controller.status = "running"
    assert asyncio.run(controller.fresh_map())["center"] == list(target)
    monitor.request_refresh.assert_called_once_with()
    assert monitor.snapshot.call_count == 3


@pytest.mark.parametrize("interrupted", [True, False])
def test_unconfirmed_walk_has_no_map_measurement(interrupted, caplog):
    controller = nav.AdaptiveCavebot(SimpleNamespace())
    caplog.set_level(logging.INFO, logger=nav.__name__)

    async def position(*args):
        if interrupted:
            raise asyncio.CancelledError()
        return nav.WalkOutcome.TIMEOUT

    controller.wait_position = position
    step = controller._confirm_walk([11, 10, 7], [10, 10, 7])
    if interrupted:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(step)
    else:
        assert asyncio.run(step) is nav.WalkOutcome.TIMEOUT
    assert controller._map_after_walk is None
    assert "map_ms=- outcome=position_" in caplog.text


def test_live_annotated_frame_available_before_atlas_write_and_scoped_to_session():
    gs = object()
    monitor = PassiveMapMonitor(SimpleNamespace(connected=True, game_state=gs))
    sample = dict(status="ready", center=[11, 10, 7], cells=[])
    annotated = {**sample, "cells": [{"kind": "blocked"}]}
    monitor._learn = Mock(return_value=annotated)
    monitor.atlas = Mock()

    def save(frame):
        assert frame is annotated
        assert monitor.snapshot()["cells"] is frame["cells"]
        raise OSError("offline test")

    monitor.atlas.observe.side_effect = save
    monitor._publish(sample, {}, gs)
    assert monitor.snapshot()["status"] == "ready" and monitor._learning_error
    monitor.state.game_state = object()
    monitor._publish({**sample, "center": [12, 10, 7]}, {}, gs)
    monitor.atlas.observe.assert_called_once()


def test_torn_map_gets_one_quick_retry_without_stacking_poll_delays(monkeypatch, tmp_path):
    task = SimpleNamespace(done=lambda: False)
    monitor = PassiveMapMonitor(SimpleNamespace(
        connected=True, game_state=object(), _action_tasks={"adaptive_cavebot": task}))
    source = SimpleNamespace(path=tmp_path / "fake", profile=SimpleNamespace(sha256="offline"),
                             read=None, base=0, close=Mock())
    monkeypatch.setitem(sys.modules, "dbv.client_bridge.passive_map_win32", SimpleNamespace(ReadOnlyClient=lambda: source))
    monkeypatch.setattr(pm, "inspect_dat", lambda path: {})
    reader = Mock()
    reader.snapshot.side_effect = pm.MapChanged("test")
    monkeypatch.setattr(pm, "MapReader", lambda *args, **kwargs: reader)
    delays = []

    def backoff(seconds):
        delays.append(seconds)
        return len(delays) == 2

    monitor._stop.wait = backoff
    monitor._refresh.wait = Mock(side_effect=AssertionError("unexpected second poll delay"))
    monitor._run()
    assert delays == [.01, monitor.ACTIVE_INTERVAL]
    source.close.assert_called_once()


def test_map_retry_excludes_time_between_attempts(monkeypatch, caplog):
    now = [1.]
    monkeypatch.setattr(nav, "perf_counter", lambda: now[0])
    caplog.set_level(logging.INFO, logger=nav.__name__)
    target = (11, 10, 7)
    controller = nav.AdaptiveCavebot(SimpleNamespace(game_state=SimpleNamespace(position=target)))
    controller._map_after_walk = nav.WalkTiming(17, target, 20.)

    async def late():
        now[0] += .8
        raise nav.InputsUnavailable()

    async def ready():
        now[0] += .025
        return {"center": target}

    controller._await_fresh_map = late
    with pytest.raises(nav.InputsUnavailable):
        asyncio.run(controller.fresh_map())
    now[0] += 2
    controller._await_fresh_map = ready
    asyncio.run(controller.fresh_map())
    assert "map_ms=800.0 outcome=map_timeout" in caplog.text
    assert "map_ms=825.0 outcome=ready" in caplog.text


def test_unavailable_position_at_deadline_is_distinct_from_a_timeout():
    controller = nav.AdaptiveCavebot(SimpleNamespace(game_state=SimpleNamespace(position=(10,10,7))))
    controller.status = "running"
    sample = Mock(side_effect=nav.InputsUnavailable("Czekam na próbkę"))
    controller.guard = sample
    target, origin = [11,10,7], [10,10,7]
    async def observe():
        without_input = await controller._confirm_walk(target, origin, timeout=.02, recover=True)
        assert without_input is nav.WalkOutcome.OBSERVATION_GAP
        await controller.handle_walk_failure(without_input, origin, target)
        assert not controller._failed_exits and not controller.blocked
        controller.guard = lambda: None
        with_input = await controller._confirm_walk(target, origin, timeout=.02, recover=True)
        assert with_input is nav.WalkOutcome.TIMEOUT
        await controller.handle_walk_failure(with_input, origin, target)
        assert controller.delayed_departures(origin) == {tuple(target)} and not controller.blocked
    asyncio.run(observe())
