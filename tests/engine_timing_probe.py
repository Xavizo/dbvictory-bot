"""Offline A/B workload. Run this same file with each revision's worktree as argv[1]."""
import asyncio
import argparse
import gc
import json
import logging
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time


async def measure_steps(state, folder):
    from test_adaptive_cavebot import setup
    controller, _, _ = setup(state, folder)
    controller.status = "running"
    gs = state.game_state
    sample = state.passive_map_monitor.snapshot
    loop = asyncio.get_running_loop()
    loop_now = time.perf_counter
    visible = list(gs.position)
    state.passive_map_monitor.snapshot = lambda: {**sample(), "center": visible.copy()}
    timings = {"step_ms": [], "position_ms": [], "map_ms": [], "cycle_ms": []}
    for i in range(32):
        target = [11 if i % 2 == 0 else 10, 10, 7]
        gs.stats_updated_at = time.time()

        async def schedule_step(payload, guard):
            nonlocal dispatched
            assert guard()
            dispatched = loop_now()
            ack_delay = (.018, .033, .026, .040)[i % 4]
            map_delay = (.016, .028, .012, .020)[i % 4]
            loop.call_later(ack_delay, setattr, gs, "position", tuple(target))
            def update_map():
                visible[:] = target
                journal = getattr(gs, "observations", None)
                if journal is not None:
                    journal.publish("map", ("map",))
            loop.call_later(ack_delay + map_delay, update_map)

        state.game_proxy.inject_to_server = schedule_step
        dispatched = None
        started = loop_now()
        if getattr(gs, "engine_journal", None):
            controller.save_session_cursor(pending=target)
        assert await controller.step(target)
        acknowledged = loop_now()
        if getattr(gs, "engine_journal", None):
            controller.index = int(target[0] == 11)
            controller.mark_waypoint()
            controller.save_session_cursor()
        await controller.fresh_map()
        ready = loop_now()
        timings["position_ms"].append((acknowledged - dispatched) * 1000)
        timings["map_ms"].append((ready - acknowledged) * 1000)
        timings["step_ms"].append((ready - dispatched) * 1000)
        timings["cycle_ms"].append((ready - started) * 1000)

    return timings


async def measure_blockage(state, folder, pulses=16):
    from dbv.mcp_server import server
    from dbv.brain.cavebot.adaptive_cavebot import OFFSETS
    from dbv.client_bridge.command_delivery import CommandReceipt
    from dbv.brain.combat.potions import run_potions
    from dbv.client_bridge.protocol import ClientOpcode, WALK_OPCODES
    from test_adaptive_cavebot import setup
    from test_automation_domains import configure, freshen, until

    controller, cells, _ = setup(state, folder)
    row = configure(state, controller, cells)
    gs = state.game_state
    gs.hp = 100
    loop_now = time.perf_counter
    heal_ms = []
    # A wall across the full observed corridor, with a reachable creature on
    # this side. Navigation must wait for terrain while both other workers run.
    for cell in cells:
        if cell["x"] == 12:
            cell["kind"] = "blocked"
    acknowledged_walks = []
    spells = []
    heals = []
    sequence = 0

    async def dispatch(payload, guard):
        nonlocal sequence
        assert guard()
        opcode = payload[0]
        if opcode == ClientOpcode.SAY:
            spells.append(loop_now())
        elif opcode == ClientOpcode.USE_ITEM:
            heals.append(loop_now())
            gs.hp = 100
        elif opcode != ClientOpcode.ATTACK:
            dx, dy = next(d for d, facing in OFFSETS.items() if WALK_OPCODES[facing] == opcode)
            x, y, z = gs.position
            gs.position = (x + dx, y + dy, z)
            acknowledged_walks.append(gs.position)
        sequence += 1
        receipt = CommandReceipt(sequence, 0, "server", opcode)
        receipt.finish("dispatched")
        return receipt

    state.game_proxy.inject_to_server = dispatch
    tasks = [asyncio.create_task(freshen(state, row)), asyncio.create_task(controller.run()),
             asyncio.create_task(run_potions(server.BotContext("auto_potions")))]
    try:
        await until(lambda: controller.blockage is not None, timeout=3)
        position = gs.position
        walks_at_block = len(acknowledged_walks)
        blocked_since = loop_now()
        for i in range(pulses):
            await asyncio.sleep((.125, .146, .166, .187)[i % 4])
            gs.stats_updated_at = time.time()
            old_count = len(heals)
            dropped = loop_now()
            gs.hp = 40
            await until(lambda: len(heals) > old_count)
            heal_ms.append((heals[-1] - dropped) * 1000)
        assert controller.status == "running" and controller.blockage and gs.position == position
        assert len(acknowledged_walks) == walks_at_block
        while_blocked = sum(stamp >= blocked_since for stamp in spells)
        assert while_blocked >= 2 and len(heals) == pulses
        for cell in cells:
            if cell["x"] == 12:
                cell["kind"] = "clear"
        row["health"] = 0
        await until(lambda: gs.position[0] == 13, timeout=3)
    finally:
        controller.pause("end of measurement")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return {"heal_ms": heal_ms, "casts_while_blocked": while_blocked, "heals_while_blocked": len(heals)}


async def measure(folder, with_recovery=False):
    from dbv.mcp_server import server

    server.load_settings = lambda: {"actions": {}}
    state = server.state = server.BotState()
    if with_recovery:
        from dbv.brain.state.engine_recovery import EngineJournal, RecoveryOwner
        owner = RecoveryOwner(folder)
        owner.begin("benchmark")
        state.game_state.engine_journal = EngineJournal(owner.path, "benchmark", owner.handover("benchmark"), 101)
    try:
        steps = await measure_steps(state, folder)
        blockage = await measure_blockage(state, folder)
        return {**steps, **blockage}
    finally:
        if with_recovery:
            owner.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tree", type=Path)
    parser.add_argument("--with-recovery", action="store_true")
    args = parser.parse_args()
    tree = args.tree.resolve(strict=True)
    sys.path[:0] = [str(tree), str(tree / "tests")]
    logging.disable(logging.CRITICAL)
    with TemporaryDirectory() as folder:
        print(json.dumps(asyncio.run(measure(Path(folder), args.with_recovery))))
        gc.collect()  # Release sqlite connections before removing the fixture on Windows.
