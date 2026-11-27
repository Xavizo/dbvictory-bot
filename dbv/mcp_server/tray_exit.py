"""Allow the native tray to end its owning session like EOF on standard input."""
import asyncio
from contextlib import contextmanager
import os
from pathlib import Path
from queue import SimpleQueue
import sys
from tempfile import TemporaryDirectory
from threading import Thread


EXIT_FILE_ENV = "DBV_TRAY_EXIT_FILE"


@contextmanager
def tray_input(encoding):
    loop = asyncio.get_running_loop()
    requests = SimpleQueue()
    # A console or an MCP host may leave stdin open indefinitely. A private
    # handle lets the process finish without waiting for a blocked readline.
    stream = open(os.dup(sys.stdin.fileno()), encoding=encoding, errors="replace")

    def deliver(future, line):
        if not future.done():
            future.set_result(line)

    def read_input():
        with stream:
            for future in iter(requests.get, None):
                try:
                    line = stream.readline()
                except OSError:
                    line = ""
                try:
                    loop.call_soon_threadsafe(deliver, future, line)
                except RuntimeError:
                    return  # Event loop has already closed.
                if not line:
                    return

    with TemporaryDirectory(prefix="dbv-session-") as folder:
        exit_file = Path(folder) / "quit"

        async def lines():
            while not exit_file.exists():
                future = loop.create_future()
                requests.put(future)
                while not future.done():
                    await asyncio.wait([future], timeout=0.25)
                    if exit_file.exists():
                        future.cancel()
                        return
                line = future.result()
                if not line:
                    return
                yield line

        Thread(target=read_input, name="dbv-stdin", daemon=True).start()
        try:
            yield exit_file, lines()
        finally:
            requests.put(None)
