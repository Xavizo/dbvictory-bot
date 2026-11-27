"""Read-only bridge from another MCP instance to the process that owns the game.

The owner listens on a local named pipe (loopback TCP off Windows) with an HMAC
handshake. Its address and a random key go to .codex/runtime/observer.json,
removed on exit. Requests and replies are JSON, never pickle. Only operations
the owner registered are served; each runs on the owner's event loop. The
bridge itself has no way to send packets or change actions.
"""
import asyncio
import json
import logging
import os
from pathlib import Path
import secrets
import threading
import time
from multiprocessing.connection import Client, Listener
from dbv.paths import INSTALL_ROOT, RUNTIME_DIR

log = logging.getLogger(__name__)

ROOT = INSTALL_ROOT
INFO_PATH = RUNTIME_DIR / "observer.json"
MAX_REQUEST = 4096
CALL_TIMEOUT = 15.


class BridgeUnavailable(RuntimeError):
    pass


def _family():
    return "AF_PIPE" if os.name == "nt" else "AF_INET"


class ObserverBridge:
    def __init__(self, loop, handlers, info_path=None):
        self.loop = loop
        self.handlers = dict(handlers)
        self.info_path = Path(info_path or INFO_PATH)
        self.listener = None
        self.address = None
        self.closed = False

    def start(self):
        """Publish only if no other live owner already answers on the same file."""
        if self.info_path.exists():
            try:
                info = call("ping", info_path=self.info_path, timeout=2)
                log.warning("Observer bridge not published: pid %s already owns it", info.get("pid"))
                return False
            except BridgeUnavailable:
                pass
        key = secrets.token_bytes(32)
        family = _family()
        address = rf"\\.\pipe\dbv-observer-{secrets.token_hex(12)}" if family == "AF_PIPE" else ("127.0.0.1", 0)
        self.listener = Listener(address, family, authkey=key)
        self.address = self.listener.address
        info = {"pid": os.getpid(), "family": family,
                "address": self.address if family == "AF_PIPE" else list(self.address),
                "key": key.hex(), "started_at": time.time(), "operations": sorted(self.handlers)}
        self.info_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.info_path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(info), encoding="utf-8")
        os.replace(temporary, self.info_path)
        threading.Thread(target=self._accept, name="observer-bridge", daemon=True).start()
        log.info("Observer bridge ready (read-only, pid %s)", os.getpid())
        return True

    def _accept(self):
        while not self.closed:
            try:
                connection = self.listener.accept()
            except (OSError, EOFError) as error:
                if self.closed:
                    return
                # A failed HMAC handshake raises AuthenticationError (a ProcessError).
                log.debug("Observer bridge rejected a connection: %s", error)
                continue
            except Exception as error:
                if self.closed:
                    return
                log.debug("Observer bridge rejected a connection: %s", error)
                continue
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, connection):
        with connection:
            while not self.closed:
                try:
                    raw = connection.recv_bytes(MAX_REQUEST)
                except (EOFError, OSError):
                    return
                connection.send_bytes(json.dumps(self._dispatch(raw), default=str).encode("utf-8"))

    def _dispatch(self, raw):
        try:
            request = json.loads(raw)
            op, args = request.get("op"), request.get("args") or {}
            if not isinstance(args, dict):
                raise ValueError("args must be an object")
        except (ValueError, AttributeError):
            return {"ok": False, "error": "malformed request"}
        if op == "ping":
            return {"ok": True, "result": {"pid": os.getpid(), "operations": sorted(self.handlers)}}
        handler = self.handlers.get(op)
        if handler is None:
            return {"ok": False, "error": f"unknown operation {op!r}"}
        try:
            future = asyncio.run_coroutine_threadsafe(handler(**args), self.loop)
            return {"ok": True, "result": future.result(CALL_TIMEOUT)}
        except TypeError as error:
            return {"ok": False, "error": f"bad arguments: {error}"}
        except Exception as error:
            return {"ok": False, "error": f"{type(error).__name__}: {error}"}

    def close(self):
        self.closed = True
        try:
            info = json.loads(self.info_path.read_text(encoding="utf-8"))
            if info.get("pid") == os.getpid():
                self.info_path.unlink()
        except (OSError, ValueError):
            pass
        if self.listener is not None:
            try:
                self.listener.close()
            except OSError:
                pass


def read_info(info_path=None):
    try:
        return json.loads(Path(info_path or INFO_PATH).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise BridgeUnavailable(f"observer file unreadable: {error}") from error


def call(op, info_path=None, timeout=CALL_TIMEOUT, **args):
    """Blocking call to the owner; raises BridgeUnavailable when there is none."""
    info = read_info(info_path)
    if info is None:
        raise BridgeUnavailable("no running launcher session published an observer bridge")
    address = info["address"] if info["family"] == "AF_PIPE" else tuple(info["address"])
    try:
        connection = Client(address, info["family"], authkey=bytes.fromhex(info["key"]))
    except (OSError, EOFError, ValueError) as error:
        raise BridgeUnavailable(f"observer bridge of pid {info.get('pid')} is not answering ({error})") from error
    except Exception as error:  # AuthenticationError
        raise BridgeUnavailable(f"observer bridge refused the handshake ({error})") from error
    with connection:
        try:
            connection.send_bytes(json.dumps({"op": op, "args": args}).encode("utf-8"))
            if not connection.poll(timeout):
                raise BridgeUnavailable(f"observer bridge timed out on {op}")
            reply = json.loads(connection.recv_bytes())
        except (OSError, EOFError, ValueError) as error:
            raise BridgeUnavailable(f"observer bridge connection failed ({error})") from error
    if not reply.get("ok"):
        raise RuntimeError(reply.get("error") or "observer bridge error")
    return reply["result"]
