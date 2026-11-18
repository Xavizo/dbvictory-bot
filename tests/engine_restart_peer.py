"""Instrument the real MCP engine for the offline restart acceptance tests."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace


def main(tree, folder):
    sys.path[:0] = [str(tree), str(tree / "tests")]
    import mcp_server as server
    from client_player_state import bind_local_player
    from potions import run_potions
    from protocol import ClientOpcode
    from test_adaptive_cavebot import setup
    from test_automation_domains import until
    from test_command_delivery import transport

    server.load_settings = lambda: {"actions": {}}
    server._check_process_running = lambda name: False
    server._check_pipe_exists = lambda: False
    server._check_port_listening = lambda port: False
    server._dashboard_running = lambda kind: False
    state = server.state = server.BotState()
    events = folder / "writes.jsonl"
    workers = []
    write_count = 0
    step_count = 0

    def start_sink():
        proxy = state.game_proxy = transport()
        proxy._client_packet_callbacks = []
        state.ready = True

        def write(payload):
            nonlocal write_count, step_count
            write_count += 1
            if payload == b"queued-only":
                event = "queued-only"
            elif payload[0] == ClientOpcode.USE_ITEM:
                event = "potion"
            else:
                event = "walk"
            with events.open("a", encoding="ascii") as stream:
                print(json.dumps([os.getpid(), event, time.perf_counter()]), file=stream)
            if event == "walk":
                step_count += 1
                if step_count == 1:
                    state.game_state.position = (11, 10, 7)
                    state.game_state.stats_updated_at = time.time()

        proxy.server_writer.write.side_effect = write
        pump = asyncio.create_task(proxy._process_inject_queue())
        workers.append(pump)
        return pump

    def configure_potion():
        state.settings["actions"]["auto_potions"] = {
            "enabled": True, "shared_cooldown": 60, "rules": [
                dict(id="heal", enabled=True, resource="hp", item_id=10161,
                     threshold=60, jitter=0, interval=.1, cooldown=60, mode="use")
            ]}

    @server.mcp.tool()
    async def fixture_inspect() -> dict:
        gs = state.game_state
        route = getattr(state, "adaptive_cavebot", None)
        return dict(pid=os.getpid(), hp=gs.hp, position=list(gs.position),
                    waypoint=route.waypoint_progress() if route else None,
                    queued=state.game_proxy._inject_queue.qsize() if state.game_proxy else 0,
                    writes=write_count)

    @server.mcp.tool()
    async def fixture_prime() -> dict:
        controller, _, draft = setup(state, folder, points=[[10,10,7], [11,10,7], [13,10,7]])
        gs = state.game_state
        gs.hp = 42
        gs.player_id = 101
        pump = start_sink()
        configure_potion()
        workers.append(asyncio.create_task(run_potions(server.BotContext("auto_potions"))))
        workers.append(asyncio.create_task(controller.run()))
        await until(lambda: step_count == 2 and controller.last_waypoint == 1
                    and 10161 in state.potion_scheduler.last_use)
        # One walk reached the sink without a position ACK. Another command
        # is queued behind the stopped pump and must never cross the restart.
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
        await state.game_proxy.inject_to_server(b"queued-only")
        return {**await fixture_inspect(), "draft": draft}

    @server.mcp.tool()
    async def fixture_observe_again() -> dict:
        start_sink()
        # The external source changed HP and position while MCP was absent.
        # Supply it through the same binding used for a newly observed player.
        def sample():
            return dict(hp=37, max_hp=100, position=(12,10,7), player_id=101,
                        stats_updated_at=time.time(), monotonic=time.monotonic())
        bot = SimpleNamespace(is_connected=True, sleep=asyncio.sleep)
        assert await bind_local_player(bot, state, state.game_state,
                                       SimpleNamespace(snapshot=sample, player_id=101))
        return await fixture_inspect()

    @server.mcp.tool()
    async def fixture_restart_healing() -> dict:
        configure_potion()
        task = asyncio.create_task(run_potions(server.BotContext("auto_potions")))
        try:
            await asyncio.sleep(.16)  # Multiple polls within the first item's 60 s cooldown.
            if task.done():
                task.result()
            return await fixture_inspect()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @server.mcp.tool()
    async def fixture_exit() -> bool:
        asyncio.get_running_loop().call_later(.08, os._exit, 23)
        return True

    server.mcp.run(transport="stdio")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
