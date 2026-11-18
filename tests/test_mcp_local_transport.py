"""Exercise the transport through the bot's stdio entry point with loopback peers."""
import asyncio
from contextlib import asynccontextmanager, suppress
import io
import json
from pathlib import Path
import os
import sys
import threading

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import LATEST_PROTOCOL_VERSION
import pytest

import run_bot
from transport_control import KEY_ENV, PORT_ENV, local_transport_host
from transport_session import LocalTransportSession

ROOT = Path(__file__).resolve().parents[1]
LOCAL = "127.0.0.1"


@asynccontextmanager
async def bot(control_env):
    params = StdioServerParameters(command=sys.executable, args=["-B", str(ROOT / "run_mcp.py")],
                                   cwd=ROOT, env={**control_env, "PYTHONUTF8": "1"})
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        status = await session.call_tool("get_status", {})
        assert not status.isError and "Proxy: not started" in status.content[0].text
        assert not (await session.call_tool("list_actions", {})).isError
        yield session


async def start(session, port):
    result = await session.call_tool("start_local_transport", {"upstream_port": port})
    assert not result.isError, result.content
    return result.structuredContent


async def echo(reader, writer):
    try:
        while block := await reader.read(8192):
            writer.write(block)
            await writer.drain()
        writer.write(b"EOF")
        await writer.drain()
    except OSError:
        pass
    finally:
        writer.close()
        with suppress(OSError):
            await writer.wait_closed()


def test_start_inspect_and_stop_through_stdio():
    async def scenario():
        async with await asyncio.start_server(echo, LOCAL, 0) as service:
            upstream = service.sockets[0].getsockname()[1]
            async with local_transport_host(upstream) as env, bot(env) as session:
                await check_controls(session, upstream)

    async def check_controls(session, upstream):
        assert "Local transport: stopped" in (await session.call_tool("get_status", {})).content[0].text
        listening = await start(session, upstream)
        assert listening["state"] == "listening" and listening["connections"] == 0
        assert await start(session, upstream) == listening
        other = 1 if upstream != 1 else 2
        assert (await session.call_tool("start_local_transport", {"upstream_port": other})).isError
        reader, writer = await asyncio.open_connection(LOCAL, listening["port"])
        try:
            writer.write(b"through bot")
            await writer.drain()
            assert await reader.readexactly(11) == b"through bot"
            async with asyncio.timeout(5):
                while True:
                    status = (await session.call_tool("get_status", {})).content[0].text
                    if "Local transport: connected" in status:
                        break
                    await asyncio.sleep(.03)
            assert "connections 1" in status and "Proxy: not started" in status
            for _ in range(2):
                result = await session.call_tool("stop_local_transport", {})
                assert not result.isError and result.structuredContent == {"state": "stopped"}
            with suppress(ConnectionResetError):
                assert await reader.read() == b""
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

    asyncio.run(asyncio.wait_for(scenario(), 30))


def test_launcher_restarts_mcp_without_interrupting_open_stream():
    async def scenario():
        async with await asyncio.start_server(echo, LOCAL, 0) as service:
            upstream = service.sockets[0].getsockname()[1]
            launcher = await asyncio.create_subprocess_exec(
                sys.executable, "-B", "-X", "utf8", str(ROOT / "run_bot.py"),
                "--local-transport", str(upstream), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE)
            async def read_listener_port():
                line = await launcher.stdout.readline()
                assert line.startswith(b"Transport testowy 127.0.0.1:"), line
                assert b"restart" in await launcher.stdout.readline()
                return int(line.split()[2].split(b":")[1])

            writer = None
            try:
                port = await read_listener_port()
                reader, writer = await asyncio.open_connection(LOCAL, port)
                writer.write(b"before")
                await writer.drain()
                assert await reader.readexactly(6) == b"before"
                launcher.stdin.write(b"restart\n")
                await launcher.stdin.drain()
                assert await read_listener_port() == port
                # Same socket, new MCP process, including a half-close in flight.
                data = bytes(range(256)) * 2048
                writer.write(data)
                await writer.drain()
                writer.write_eof()
                assert await reader.read() == data + b"EOF"
            finally:
                if writer is not None:
                    writer.close()
                    with suppress(OSError):
                        await writer.wait_closed()
                launcher.stdin.close()
                try:
                    assert await asyncio.wait_for(launcher.wait(), 8) == 0
                finally:
                    if launcher.returncode is None:
                        launcher.kill()
                        await launcher.wait()
            with pytest.raises(OSError):
                await asyncio.open_connection(LOCAL, port)
    asyncio.run(asyncio.wait_for(scenario(), 30))


def test_data_keeps_flowing_after_controller_error_and_before_replacement(monkeypatch):
    async def scenario():
        async with await asyncio.start_server(echo, LOCAL, 0) as service:
            upstream = service.sockets[0].getsockname()[1]
            writer = None
            active = None
            old_key = None
            pending_stop = None
            resumed = False

            async def exercise_controller(params, upstream_port, console_input):
                nonlocal active, writer, reader, resumed, old_key, pending_stop
                if active is None:
                    async with bot(params.env) as first:
                        active = await start(first, upstream)
                        reader, writer = await asyncio.open_connection(LOCAL, active["port"])
                        writer.write(b"first")
                        await writer.drain()
                        assert await reader.readexactly(5) == b"first"
                        async with asyncio.timeout(3):
                            while not (active := await start(first, upstream)).get("connection_id"):
                                await asyncio.sleep(.02)
                    old_key = params.env[KEY_ENV]
                    pending_stop = await asyncio.open_connection(LOCAL, int(params.env[PORT_ENV]))
                    # This request is still incomplete when its controller exits.
                    pending_stop[1].write(json.dumps({"key": old_key, "command": "stop"}).encode())
                    await pending_stop[1].drain()
                    raise RuntimeError("simulated controller error")
                writer.write(b"between controllers")
                await writer.drain()
                assert await reader.readexactly(19) == b"between controllers"
                assert await asyncio.wait_for(pending_stop[0].read(), 1) == b""
                # Even on the replacement's port, credentials from the first
                # controller cannot stop the shared worker.
                stale_read, stale_write = await asyncio.open_connection(LOCAL, int(params.env[PORT_ENV]))
                try:
                    stale_write.write(json.dumps({"key": old_key, "command": "stop"}).encode() + b"\n")
                    await stale_write.drain()
                    assert await asyncio.wait_for(stale_read.read(), 1) == b""
                finally:
                    stale_write.close()
                    await stale_write.wait_closed()
                async with bot(params.env) as replacement:
                    same = await start(replacement, upstream_port)
                    assert same["pid"] == active["pid"] and same["port"] == active["port"]
                    assert same["state"] == "connected" and same["connections"] == 1
                    assert same["connection_id"] == active["connection_id"]
                    assert not (await replacement.call_tool("stop_local_transport", {})).isError
                resumed = True
                return False

            reader = None
            monkeypatch.setattr(run_bot, "local_controller_console", exercise_controller)
            monkeypatch.setattr(sys, "stdin", io.StringIO("restart\n"))
            try:
                await run_bot.run_local_transport(upstream)
                assert resumed
            finally:
                if pending_stop:
                    pending_stop[1].close()
                    with suppress(OSError):
                        await pending_stop[1].wait_closed()
                if writer is not None:
                    writer.close()
                    with suppress(OSError):
                        await writer.wait_closed()
    asyncio.run(asyncio.wait_for(scenario(), 30))


def test_idle_controller_eof_keeps_pending_console_line_for_restart_prompt():
    async def scenario():
        unblock = threading.Event()

        class Input:
            calls = 0

            def readline(self):
                self.calls += 1
                assert unblock.wait(5)
                return "restart\n"

        stream = Input()
        console_input = run_bot.LocalConsoleInput(stream)
        child = StdioServerParameters(command=sys.executable, args=["-I", "-c", "pass"])
        async with stdio_client(child) as (output, _):
            try:
                with pytest.raises(RuntimeError, match="Kontroler MCP"):
                    await asyncio.wait_for(console_input.readline(output), 2)
            finally:
                unblock.set()
            assert await console_input.readline() == "restart\n"
            assert stream.calls == 1

    asyncio.run(scenario())


def test_abrupt_mcp_process_exit_preserves_existing_local_stream():
    async def scenario():
        async with await asyncio.start_server(echo, LOCAL, 0) as service:
            port = service.sockets[0].getsockname()[1]
            async with local_transport_host(port) as env:
                # Own the child handle so this test can kill just this controller
                # without the graceful shutdown performed by the SDK context.
                child_env = {k:os.environ[k] for k in ("SystemRoot","WINDIR","TEMP","TMP") if k in os.environ}
                child = await asyncio.create_subprocess_exec(
                    sys.executable,"-B",str(ROOT/"run_mcp.py"),cwd=ROOT,
                    env={**child_env,**env,"PYTHONUTF8":"1"},stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
                seq = 0
                async def rpc(method, params):
                    nonlocal seq
                    seq += 1
                    child.stdin.write((json.dumps({"jsonrpc":"2.0","id":seq,"method":method,"params":params})+"\n").encode())
                    await child.stdin.drain()
                    while line := await child.stdout.readline():
                        reply = json.loads(line)
                        if reply.get("id") == seq:
                            assert "error" not in reply
                            return reply["result"]
                    raise AssertionError("MCP exited before replying")
                writer = None
                try:
                    await rpc("initialize",{"protocolVersion":LATEST_PROTOCOL_VERSION,"capabilities":{},
                                            "clientInfo":{"name":"local-test","version":"1"}})
                    child.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
                    assert "Proxy: not started" in (await rpc("tools/call",{"name":"get_status"}))["content"][0]["text"]
                    assert not (await rpc("tools/call",{"name":"list_actions"})).get("isError")
                    result = await rpc("tools/call",{"name":"start_local_transport","arguments":{"upstream_port":port}})
                    first = result["structuredContent"]
                    reader, writer = await asyncio.open_connection(LOCAL,first["port"])
                    writer.write(b"one"); await writer.drain()
                    assert await reader.readexactly(3) == b"one"
                    child.kill()
                    assert await child.wait() != 0
                    writer.write(b"two"); await writer.drain()
                    assert await reader.readexactly(3) == b"two"
                    async with bot(env) as replacement:
                        same = await start(replacement,port)
                        assert (same["pid"],same["port"],same["connections"]) == (first["pid"],first["port"],1)
                        writer.write_eof()
                        assert await reader.read() == b"EOF"
                finally:
                    if child.returncode is None:
                        child.kill()
                        await child.wait()
                    child.stdin.close()
                    if writer is not None:
                        writer.close()
                        with suppress(OSError):
                            await writer.wait_closed()
    asyncio.run(asyncio.wait_for(scenario(), 25))


def test_control_rejects_other_sessions():
    async def scenario():
        async with local_transport_host(12345) as env:
            for key in ("", "wrong-key", "ą"):
                reader, writer = await asyncio.open_connection(LOCAL, int(env[PORT_ENV]))
                writer.write(('{"command":"status","key":"' + key + '"}\n').encode("utf8"))
                await writer.drain()
                assert await reader.read() == b""
                writer.close()
                await writer.wait_closed()
            assert len(env[KEY_ENV]) >= 32
    asyncio.run(asyncio.wait_for(scenario(), 10))


def test_host_shutdown_joins_pending_control_before_stopping_owner(monkeypatch):
    """An in-flight start and an idle client may both span controller shutdown."""
    async def scenario():
        started, released = asyncio.Event(), asyncio.Event()
        trace = []

        class PendingManager:
            async def start(self, port):
                started.set()
                try:
                    await released.wait()
                finally:
                    trace.append("start finished")
                return {"state":"listening"}

            async def stop(self):
                trace.append("owner stopped")

        monkeypatch.setattr("transport_control.LocalTransportSession", PendingManager)
        context = local_transport_host(24004)
        env = await context.__aenter__()
        endpoint = (LOCAL, int(env[PORT_ENV]))
        idle, idle_writer = await asyncio.open_connection(*endpoint)
        active, active_writer = await asyncio.open_connection(*endpoint)
        try:
            active_writer.write(json.dumps({"key":env[KEY_ENV],"command":"start","port":24004}).encode()+b"\n")
            await active_writer.drain()
            await asyncio.wait_for(started.wait(), 2)
            await asyncio.wait_for(context.__aexit__(None,None,None), 1)
            assert trace == ["start finished","owner stopped"]
            assert await asyncio.wait_for(active.read(), 1) == b""
            assert await asyncio.wait_for(idle.read(), 1) == b""
            with pytest.raises(OSError):
                await asyncio.open_connection(*endpoint)
        finally:
            released.set()
            active_writer.close()
            idle_writer.close()
            await asyncio.gather(active_writer.wait_closed(),idle_writer.wait_closed(),return_exceptions=True)
    asyncio.run(scenario())


def test_concurrent_start_and_recovery_from_child_exit():
    async def scenario():
        manager = LocalTransportSession()
        async with await asyncio.start_server(echo, LOCAL, 0) as service:
            upstream = service.sockets[0].getsockname()[1]
            try:
                for value in (0, 65536, True):
                    with pytest.raises(ValueError):
                        await manager.start(value)
                first, same = await asyncio.gather(manager.start(upstream), manager.start(upstream))
                assert first == same
                process = manager._worker.process
                process.terminate()
                await asyncio.to_thread(process.wait, 5)
                assert manager.snapshot()["state"] == "exited"
                again = await manager.start(upstream)
                assert again["state"] == "listening" and again["connections"] == 0
                assert manager._worker.process is not process
            finally:
                await manager.stop()
    asyncio.run(asyncio.wait_for(scenario(), 20))


def test_new_connection_gets_new_identity_even_with_same_listener_or_worker_count():
    from engine_recovery import EngineJournal

    async def scenario():
        manager = LocalTransportSession()
        seen = set()
        async def phase(name):
            async with asyncio.timeout(3):
                while (report := manager.snapshot())["state"] != name:
                    await asyncio.sleep(.02)
            return report

        async with await asyncio.start_server(echo, LOCAL, 0) as service:
            upstream = service.sockets[0].getsockname()[1]
            try:
                for index in range(3):
                    listener = await manager.start(upstream)
                    assert "connection_id" not in listener
                    reader, writer = await asyncio.open_connection(LOCAL, listener["port"])
                    try:
                        active = await phase("connected")
                        assert active["connections"] == (1, 2, 1)[index]
                        assert active["connection_id"] not in seen
                        seen.add(active["connection_id"])
                        grant = manager.recovery_grant(active["connection_id"], "test-controller")
                        journal = EngineJournal(grant["path"], grant["connection_id"], grant["lease"], 101)
                        assert journal.current() and journal.reserve(10161, 60, 60)
                        if index == 2:
                            process = manager._worker.process
                            process.terminate()
                            await asyncio.to_thread(process.wait, 3)
                            assert "connection_id" not in await phase("exited")
                        else:
                            writer.write_eof()
                            assert await reader.read() == b"EOF"
                            assert "connection_id" not in await phase("listening")
                            assert not journal.current()
                    finally:
                        writer.close()
                        with suppress(OSError):
                            await writer.wait_closed()
                    if index == 1:
                        await manager.stop()
            finally:
                await manager.stop()
    asyncio.run(asyncio.wait_for(scenario(), 15))
