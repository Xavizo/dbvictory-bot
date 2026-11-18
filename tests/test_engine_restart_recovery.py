"""Release checks for a real MCP process replacement, with synthetic game inputs."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

from test_mcp_local_transport import LOCAL, echo
from transport_control import controller_endpoint
from transport_session import LocalTransportSession

ROOT = Path(__file__).resolve().parents[1]
PEER = Path(__file__).with_name("engine_restart_peer.py")


async def call(session, name, args=None):
    result = await session.call_tool(name, args or {})
    assert not result.isError, result.content
    if result.structuredContent is not None:
        return result.structuredContent
    if name == "session_recovery_preview" or (name.startswith("fixture_") and name != "fixture_exit"):
        return json.loads(result.content[0].text)
    return None


@asynccontextmanager
async def engine(tree, folder, env, *, abrupt_exit=False):
    params = StdioServerParameters(command=sys.executable, args=["-B", str(PEER), str(tree), str(folder)],
                                   cwd=folder, env={**env, "PYTHONUTF8": "1"})
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        await call(session, "get_status")
        await call(session, "list_actions")
        yield session
        if abrupt_exit:
            await call(session, "fixture_exit")
            async with asyncio.timeout(3):
                while reader.statistics().open_send_streams:
                    await asyncio.sleep(.02)


async def probe(tree, folder, abrupt, hold_potion_receipt=False):
    manager = LocalTransportSession()
    reader = writer = None
    local = (tree / "transport_control.py").is_file()
    service = await asyncio.start_server(echo, LOCAL, 0)
    try:
        upstream = service.sockets[0].getsockname()[1]
        async def observation(hp, x):
            if not local:
                return {}
            async with asyncio.timeout(3):
                while (status := manager.snapshot())["state"] != "connected":
                    await asyncio.sleep(.01)
            sample = dict(connection_id=status["connection_id"], player_id=101, hp=hp, max_hp=100,
                          position=[x, 10, 7])
            writer.write(json.dumps(sample).encode() + b"\n")
            await writer.drain()
            return {"observation": json.loads(await reader.readline())}
        async with controller_endpoint(manager, upstream) as env, engine(tree, folder, env, abrupt_exit=abrupt) as first:
            if local:
                initial = await call(first, "start_local_transport", {"upstream_port": upstream})
                reader, writer = await asyncio.open_connection(LOCAL, initial["port"])
                writer.write(b"before")
                await writer.drain()
                assert await reader.readexactly(6) == b"before"
            before = await call(first, "fixture_prime", {**await observation(42, 10),
                                "hold_potion_receipt": hold_potion_receipt})
            preview = await call(first, "session_recovery_preview") if (tree / "recovery_preview.py").exists() else None
            started = time.perf_counter()
        async with controller_endpoint(manager, upstream) as env, engine(tree, folder, env) as second:
            if local:
                same = await call(second, "start_local_transport", {"upstream_port": upstream})
                assert (same["pid"], same["port"], same["connections"]) == (initial["pid"], initial["port"], 1)
                writer.write(b"after")
                await writer.drain()
                assert await reader.readexactly(5) == b"after"
            cold = await call(second, "fixture_inspect")
            ready_ms = (time.perf_counter() - started) * 1000
            observed = await call(second, "fixture_observe_again", await observation(37, 12))
            observed_ms = (time.perf_counter() - started) * 1000
            restarted = await call(second, "fixture_restart_healing")
            resumed = await call(second, "fixture_finish_route") if observed["waypoint"] else None
    finally:
        service.close()
        if writer:
            writer.close()
            await writer.wait_closed()
        await manager.stop()
        await service.wait_closed()
    writes = [json.loads(line) for line in (folder / "writes.jsonl").read_text().splitlines()]
    return dict(before=before, cold=cold, observed=observed, restarted=restarted, resumed=resumed,
                ready_ms=ready_ms, observed_ms=observed_ms, writes=writes, preview=preview,
                held_receipt=hold_potion_receipt)


@pytest.fixture(scope="module", params=[(False, False), (True, False), (True, True)],
                ids=["graceful", "abrupt", "abrupt_before_potion_receipt"])
def handover(tmp_path_factory, request):
    return asyncio.run(asyncio.wait_for(probe(ROOT, tmp_path_factory.mktemp("restart"), *request.param), 35))


def test_restart_uses_fresh_observations_and_does_not_replay_old_queue(handover):
    before, cold, fresh = (handover[key] for key in ("before", "cold", "observed"))
    assert (before["hp"], before["position"]) == (42, [11, 10, 7])
    assert before["pid"] != cold["pid"] and before["queued"] == 1
    assert before["waypoint"]["last_reached"] == 1 and before["waypoint"]["current"] == 2
    assert cold["hp"] != before["hp"] and cold["position"] != before["position"]
    assert (fresh["hp"], fresh["position"]) == (37, [12, 10, 7])
    assert cold["queued"] == cold["writes"] == fresh["queued"] == fresh["writes"] == 0
    original = [event for pid, event, _ in handover["writes"] if pid == before["pid"]]
    replacement = [event for pid, event, _ in handover["writes"] if pid == cold["pid"]]
    assert sorted(original) == ["potion", "walk", "walk"]
    assert handover["restarted"]["writes"] == 0
    assert replacement == ["walk"]  # the explicitly resumed route only
    assert all(event != "queued-only" for _, event, _ in handover["writes"])


def test_restarted_engine_recovers_the_confirmed_waypoint(handover):
    assert handover["observed"]["waypoint"] == handover["before"]["waypoint"]
    assert handover["resumed"]["status"] == "complete"
    assert handover["resumed"]["position"] == [13, 10, 7]


def test_restart_respects_already_dispatched_potion(handover):
    uses = [stamp for _, event, stamp in handover["writes"] if event == "potion"]
    assert uses and all(b - a >= 60 for a, b in zip(uses, uses[1:]))


def test_passive_rehearsal_reports_current_progress_without_writes(handover):
    sample = handover["preview"]
    assert (sample["hp"], sample["position"]) == (42, [11, 10, 7])
    assert (sample["last_waypoint"], sample["next_waypoint"]) == (2, 3)
    if handover["held_receipt"]:
        assert not sample["potion_seconds"]  # the passive sample includes only observed uses
    else:
        assert 0 < sample["potion_seconds"]["10161"] <= 60
