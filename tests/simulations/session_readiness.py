"""Replay save-action initialization against a synthetic session, without a client."""
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dbv.brain.state.session_startup import LoginStartup


async def exercise(case):
    proxy = SimpleNamespace(logged_in=True, session_generation=1,
                            server_writer=object() if case == "ready" else None)
    state = SimpleNamespace(connected=True, game_state=object(), game_proxy=proxy)
    startup = LoginStartup()
    calls = []

    def start():
        calls.append(proxy.session_generation)
        return 0

    first = startup.schedule(state, start)
    assert startup.schedule(state, start) is first
    await asyncio.sleep(0)
    if case == "delayed":
        proxy.server_writer = object()
    elif case == "disconnect":
        state.connected = proxy.logged_in = False
    elif case == "replace":
        proxy.session_generation += 1
        state.game_state = object()
        proxy.server_writer = object()
        await first
        assert calls == []
        await startup.schedule(state, start)
    elif case == "writer-missing":
        await asyncio.sleep(.12)
        assert startup.phase == "waiting_for_session" and not first.done()
        startup.cancel()
    await asyncio.gather(first, return_exceptions=True)
    if case in ("ready", "delayed", "replace"):
        await startup.schedule(state, start)
    return calls


async def main():
    expected = {"ready": [1], "delayed": [1], "disconnect": [], "replace": [2], "writer-missing": []}
    results = {case: await exercise(case) for case in expected}
    print(json.dumps(results, indent=2))
    assert results == expected


if __name__ == "__main__":
    asyncio.run(main())
