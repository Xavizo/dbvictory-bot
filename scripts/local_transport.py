"""Standalone byte relay for exercising process lifetimes with local services."""
import argparse
import asyncio
from contextlib import suppress
import json
import os
import queue
import sys
import threading
from uuid import uuid4

LOOPBACK = "127.0.0.1"
BUFFER_SIZE = 64 * 1024


async def copy_stream(reader, writer):
    while data := await reader.read(BUFFER_SIZE):
        writer.write(data)
        await writer.drain()
    writer.write_eof()
    await writer.drain()


async def relay_once(client_in, client_out, upstream_port, connected):
    server_in, server_out = await asyncio.wait_for(
        asyncio.open_connection(LOOPBACK, upstream_port, limit=BUFFER_SIZE), timeout=3
    )
    try:
        connected()
        async with asyncio.TaskGroup() as streams:
            streams.create_task(copy_stream(client_in, server_out))
            streams.create_task(copy_stream(server_in, client_out))
    finally:
        server_out.close()
        with suppress(OSError):
            await server_out.wait_closed()


class ParentMonitor:
    """Publish only the latest state. A stalled reader cannot stall forwarding."""

    def __init__(self, loop, detached):
        self.updates = queue.Queue(maxsize=1)
        self.loop = loop
        self.detached = detached

    def start(self):
        threading.Thread(target=self._write_updates, daemon=True).start()
        threading.Thread(target=self._watch_parent, daemon=True).start()

    def publish(self, state):
        with suppress(queue.Empty):
            self.updates.get_nowait()
        self.updates.put_nowait(dict(state))

    def _write_updates(self):
        try:
            while True:
                data = json.dumps(self.updates.get()).encode("ascii") + b"\n"
                os.write(sys.stdout.fileno(), data)
        except OSError:
            return

    def _watch_parent(self):
        # Only this direct parent holds the write end. EOF also covers a crash.
        try:
            while os.read(sys.stdin.fileno(), 1):
                pass
        except OSError:
            pass
        with suppress(RuntimeError):
            self.loop.call_soon_threadsafe(self.detached.set)


async def serve(upstream_port, once=False, managed=False, recovery_owner=None):
    finished = asyncio.Event()
    detached = asyncio.Event()
    busy = False
    monitor = ParentMonitor(asyncio.get_running_loop(), detached) if managed else None
    state = {"state": "listening", "connections": 0, "failures": 0}

    def publish(phase):
        state["state"] = phase
        if phase != "connected":
            state.pop("connection_id", None)
        if monitor:
            monitor.publish(state)

    def connected():
        state["connections"] += 1
        state["connection_id"] = uuid4().hex
        if recovery_owner is not None:
            recovery_owner.begin(state["connection_id"])
        publish("connected")

    async def accept(reader, writer):
        nonlocal busy
        if busy or detached.is_set():
            writer.close()
            return
        busy = True
        finished.clear()
        publish("connecting")
        try:
            try:
                await relay_once(reader, writer, upstream_port, connected)
            except* OSError:
                # Connection failure is local to this stream pair.
                state["failures"] += 1
        finally:
            if recovery_owner is not None and state.get("connection_id"):
                recovery_owner.end(state["connection_id"])
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()
            busy = False
            publish("listening")
            finished.set()

    server = await asyncio.start_server(accept, LOOPBACK, 0, limit=BUFFER_SIZE)
    async with server:
        state["port"] = server.sockets[0].getsockname()[1]
        if monitor:
            monitor.start()
            publish("listening")
            await detached.wait()
            # Stop accepting new connections and drain only the existing pair.
            # asyncio.Server.wait_closed() can wait for active clients on 3.12.
            server.close()
            if busy:
                await finished.wait()
        elif once:
            print(json.dumps({"port": state["port"]}), flush=True)
            await finished.wait()
        else:
            print(json.dumps({"port": state["port"]}), flush=True)
            await server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("upstream_port", type=int, help="Port of the local test service")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Exit after one connection")
    mode.add_argument("--managed", action="store_true", help="Publish status to the owning bot")
    parser.add_argument("--recovery-dir", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.upstream_port <= 65535:
        parser.error("Port must be in 1..65535")
    try:
        owner = None
        if args.recovery_dir:
            if not args.managed:
                parser.error("recovery-dir requires managed mode")
            from pathlib import Path
            # The supervisor starts this interpreter with -I and no PYTHONPATH.
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from engine_recovery import RecoveryOwner
            owner = RecoveryOwner(args.recovery_dir)
        try:
            asyncio.run(serve(args.upstream_port, args.once, args.managed, owner))
        finally:
            if owner is not None:
                owner.close()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
