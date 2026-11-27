"""Cross-domain replays using the public world state, with no connected client."""
import asyncio
from types import SimpleNamespace

from dbv.brain.cavebot.adaptive_cavebot import AdaptiveCavebot
from dbv.brain.state.game_state import GameState
from dbv.brain.combat.hunter_knife import FRESH
from dbv.brain.combat.hunter_knife_action import KnifeAutomation
from dbv.brain.combat.potions import PotionScheduler, validate_config
from dbv.brain.state.session_observations import note_observation_gap, session_observations
from test_hunter_knife import A, NAME, tile
from test_potions import rule
from test_relogin_identity import callback


def test_step_at_owned_corpse_lost_map_health_and_late_old_session_result():
    async def exercise():
        source = A
        adjacent = (source[0] + 1, source[1], source[2])
        session = GameState()
        session.session_id = 40
        session.position = source
        session.experience = 100
        observations = session_observations(session)
        state = SimpleNamespace(game_state=session)
        controller = AdaptiveCavebot(state)
        controller.status = "running"
        worker = session.hunter_knife = KnifeAutomation(session)
        worker.tool_ready = True

        def frame(stamp, bodies):
            return worker.next(dict(captured_at=stamp, cells=[tile(source, *bodies)]), stamp)

        frame(10, [])
        worker.queue.death(dict(cid=77, name=NAME, x=A[0], y=A[1], z=A[2]), 10.2)
        worker.queue.personal_loot(f"[{NAME}]: nothing.", 10.3)
        frame(10.4, [FRESH])
        assert frame(10.7, [FRESH]).source == source

        session.position = adjacent
        families = ("map",)
        revision = observations.revision_for(families)
        pending_map = asyncio.create_task(controller.wait_for_navigation(
            "Oczekiwanie na pole", families, 5., after=revision))
        await asyncio.sleep(0)
        note_observation_gap(session, "missing_copy")
        session.hp, session.max_hp, session.stats_updated_at = 40, 100, 101
        observations.publish("client_snapshot", ("health", "position"))
        cfg = validate_config({"rules": [rule()]})
        assert PotionScheduler().next_rule(cfg, session, 10.8, 101)["id"] == "hp"
        assert not pending_map.done() and controller.wait_snapshot()["observations"] == families

        observations.publish("map", families)
        await asyncio.wait_for(pending_map, 1.)
        assert controller.wait_snapshot() is None
        assert frame(10.9, [FRESH]).source == source

        state.game_state = GameState()
        state.game_state.session_id = 41
        next_worker = KnifeAutomation(state.game_state)
        next_worker.tool_ready = True
        state.game_state.position = adjacent
        # Call the same publication guard the real consumer uses. The old
        # result must be discarded before any field or consumer is touched.
        apply = callback("apply_packet_observations", {"state": state})
        apply(session, object(), [])
        assert session_observations(state.game_state).snapshot(41)["revision"] == 0
        # A current, freshly observed 9068 can be inspected after reconnect.
        # The previous session's receipt cannot supply ownership or confirm it.
        fresh_attempt = next_worker.next(dict(captured_at=11., cells=[tile(source, FRESH)]), 11.)
        assert fresh_attempt and fresh_attempt.source == source and fresh_attempt.item["id"] == FRESH
        assert next_worker.done == 0 and next_worker.queue.from_combat == 0
        assert not next_worker.queue.stacks and next_worker.pending is None

    asyncio.run(exercise())
