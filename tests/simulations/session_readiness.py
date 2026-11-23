"""Offline characterization of both login callbacks on one synthetic session.

Run: python tests/simulations/session_readiness.py [--acceptance]
This harness only counts invocations of the action-list starter. It replaces
that starter with a recorder, never imports the server and never sends commands.
"""
import ast
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
NAMES = ("_direct_start", "_wait_for_login_and_start")


def load_callbacks(env):
    path = ROOT / "mcp_server.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [node for node in ast.walk(tree)
             if isinstance(node, ast.AsyncFunctionDef) and node.name in NAMES]
    if len(nodes) != len(NAMES):
        raise RuntimeError("Startup was refactored. Bind this replay to the new coordinator.")
    for node in nodes:
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), env)
    return [env[name] for name in NAMES]


async def exercise(case):
    # Both callbacks are from login A. A separate B starter is intentionally
    # absent in 'replace'. Any attempt recorded there is attributable to A.
    login = SimpleNamespace(name="A")
    state = SimpleNamespace(ready=True, connected=True, game_state=login,
                            game_proxy=SimpleNamespace(logged_in=True, server_writer=None),
                            _login_event=asyncio.Event())
    state._login_event.set()
    starts = []
    checks = {}
    async def wait_for_next_check(delay):
        assert delay > 0
        task = asyncio.current_task()
        name = task.get_name()
        checks[name] = checks.get(name, 0) + 1
        if case == "disconnect":
            state.connected = state.ready = state.game_proxy.logged_in = False
        elif case == "replace" and state.game_state is login:
            state.game_state = SimpleNamespace(name="B")
            state.game_proxy.server_writer = object()
        elif case == "ready" or (case == "delayed" and checks[name] >= 4):
            state.game_proxy.server_writer = object()
        await asyncio.sleep(0)

    def on_start():
        starts.append({"callback": asyncio.current_task().get_name(),
                       "generation": state.game_state.name, "logged_in": state.game_proxy.logged_in,
                       "writer": state.game_proxy.server_writer is not None})
        return 0

    callbacks = load_callbacks(dict(state=state, login_state=login, _start_all_enabled_actions=on_start,
                                    asyncio=SimpleNamespace(sleep=wait_for_next_check,
                                                           wait_for=asyncio.wait_for,
                                                           TimeoutError=asyncio.TimeoutError),
                                    time=time, log=logging.getLogger("session-readiness")))
    await asyncio.gather(*(asyncio.create_task(callback(), name=callback.__name__)
                           for callback in callbacks))
    return starts


async def main():
    cases = {case: await exercise(case)
             for case in ("ready", "delayed", "disconnect", "replace", "writer-missing")}
    print(json.dumps(cases, indent=2))
    for case in ("ready", "delayed"):
        assert cases[case] and all(row["writer"] and row["logged_in"] for row in cases[case])
    if "--acceptance" in sys.argv:
        failures = [case for case, rows in cases.items()
                    if len(rows) != (1 if case in {"ready", "delayed"} else 0)]
        if failures:
            raise AssertionError("Startup gate failed " + ", ".join(failures))


if __name__ == "__main__":
    asyncio.run(main())
