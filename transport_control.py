"""Launcher-owned control endpoint for the opt-in local transport session."""
import asyncio
from contextlib import asynccontextmanager, suppress
import hmac
import json
import os
import secrets

from transport_session import LocalTransportSession

HOST = "127.0.0.1"
PORT_ENV = "DBV_LOCAL_TRANSPORT_CONTROL_PORT"
KEY_ENV = "DBV_LOCAL_TRANSPORT_CONTROL_KEY"


@asynccontextmanager
async def local_transport_host(upstream_port):
    """Own a forwarding session and a single controller for the same lifetime."""
    manager = LocalTransportSession()
    try:
        async with controller_endpoint(manager, upstream_port) as env:
            yield env
    finally:
        await manager.stop()


@asynccontextmanager
async def controller_endpoint(manager, upstream_port):
    """Grant one controller access until its requests have been closed and joined."""
    key = secrets.token_hex(32)
    pending = {}
    closing = False

    async def handle(reader, writer):
        try:
            request = json.loads(await asyncio.wait_for(reader.readline(), 2))
            if not isinstance(request, dict):
                return
            supplied = request.get("key")
            if not isinstance(supplied, str) or not hmac.compare_digest(key, supplied):
                return
            command = request.get("command")
            try:
                if command == "status":
                    reply = manager.snapshot()
                elif command == "start" and request.get("port") == upstream_port:
                    reply = await manager.start(upstream_port)
                elif command == "stop":
                    reply = await manager.stop()
                else:
                    reply = {"error": "Unknown command or endpoint outside this local session"}
            except (OSError, RuntimeError):
                reply = {"error": "Local transport operation failed"}
            writer.write(json.dumps(reply).encode("ascii") + b"\n")
            await writer.drain()
        except (OSError, ValueError, TypeError):
            pass
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

    def accept(reader, writer):
        if closing:
            writer.close()
            return
        task = asyncio.create_task(handle(reader, writer))
        pending[task] = writer
        task.add_done_callback(pending.pop)

    server = await asyncio.start_server(accept, HOST, 0, limit=1024)
    try:
        yield {PORT_ENV: str(server.sockets[0].getsockname()[1]), KEY_ENV: key}
    finally:
        closing = True
        server.close()
        # A partially read command and a Start already holding the manager lock
        # must both finish before releasing this owner's worker.
        requests = list(pending.items())
        for task, writer in requests:
            writer.close()
            task.cancel()
        await asyncio.gather(*(task for task, _ in requests), return_exceptions=True)
        await server.wait_closed()


class LocalTransportControl:
    """MCP holds no data socket and never owns the forwarding process."""

    def __init__(self):
        self.port = os.environ.get(PORT_ENV)
        self.key = os.environ.get(KEY_ENV)

    async def _call(self, command, upstream_port=None):
        if not self.port or not self.key:
            if command in {"status", "stop"}:
                return {"state": "stopped"}
            raise ValueError("Launch the local session with run_bot.py --local-transport PORT")
        request = {"key": self.key, "command": command, "port": upstream_port}
        try:
            async with asyncio.timeout(1 if command == "status" else 12):
                reader, writer = await asyncio.open_connection(HOST, int(self.port), limit=1024)
                try:
                    writer.write(json.dumps(request).encode("ascii") + b"\n")
                    await writer.drain()
                    reply = json.loads(await reader.readline())
                    if not isinstance(reply, dict) or not ("error" in reply or "state" in reply):
                        raise ValueError("Invalid transport status")
                finally:
                    writer.close()
                    with suppress(OSError):
                        await writer.wait_closed()
        except (OSError, ValueError):
            if command == "status":
                return {"state": "status_unavailable"}
            raise RuntimeError("Local transport supervisor unavailable") from None
        if "error" in reply:
            raise ValueError(reply["error"])
        return reply

    async def snapshot(self):
        return await self._call("status")

    async def start(self, upstream_port):
        return await self._call("start", upstream_port)

    async def stop(self):
        return await self._call("stop")
