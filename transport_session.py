"""Bot-side supervision for an explicit, transparent loopback session."""
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from tempfile import TemporaryDirectory

from engine_recovery import RecoveryOwner
from dbv.paths import SCRIPTS_DIR

WORKER = SCRIPTS_DIR / "local_transport.py"


class _Worker:
    def __init__(self, upstream_port):
        self.upstream_port = upstream_port
        self.first_report = threading.Event()
        self.report = None
        self.recovery_dir = TemporaryDirectory(prefix="dbv-engine-")
        self.recovery_owner = RecoveryOwner(self.recovery_dir.name)
        # Isolated interpreter and a small environment. Dashboard credentials
        # and application settings are not inputs to the transport.
        env = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "TEMP", "TMP")
               if k in os.environ}
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-I", "-B", str(WORKER), str(upstream_port), "--managed",
                 "--recovery-dir", self.recovery_dir.name],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=env, close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            self.recovery_owner.close()
            self.recovery_dir.cleanup()
            raise
        self.reader = threading.Thread(target=self._read_reports, daemon=True)
        self.reader.start()

    def _read_reports(self):
        try:
            with self.process.stdout as stream:
                while line := stream.readline(512):
                    if not line.endswith(b"\n"):
                        break
                    data = json.loads(line)
                    if (not isinstance(data, dict) or data.get("state") not in
                            ("listening", "connecting", "connected")
                            or type(data.get("port")) is not int or not 1 <= data["port"] <= 65535
                            or any(type(data.get(k)) is not int or data[k] < 0
                                   for k in ("connections", "failures"))):
                        break
                    report = {k: data[k] for k in ("port", "state", "connections", "failures")}
                    if report["state"] == "connected":
                        connection_id = data.get("connection_id")
                        if not isinstance(connection_id, str) or not re.fullmatch(r"[0-9a-f]{32}", connection_id):
                            break
                        report["connection_id"] = connection_id
                    self.report = report
                    self.first_report.set()
        except (OSError, ValueError):
            pass
        finally:
            if self.report and self.report.get("connection_id"):
                self.recovery_owner.end(self.report["connection_id"])
            self.first_report.set()

    def snapshot(self):
        info = dict(self.report or {"state": "starting"})
        info.update(upstream_port=self.upstream_port, pid=self.process.pid)
        code = self.process.poll()
        if code is not None:
            info.update(state="exited", exit_code=code)
        elif not self.reader.is_alive():
            info["state"] = "status_unavailable"
        if info["state"] != "connected":
            info.pop("connection_id", None)
        return info

    def stop(self):
        # Only the owned process handle is terminated by an explicit stop.
        self.process.stdin.close()
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
        self.reader.join()
        self.recovery_owner.close()
        self.recovery_dir.cleanup()


class LocalTransportSession:
    def __init__(self):
        self._worker = None
        self._lock = asyncio.Lock()
        self._recovery_login = self._controller = self._lease = None

    def recovery_grant(self, login, controller):
        info = self.snapshot()
        if not login or info.get("connection_id") != login:
            raise ValueError("Recovery requires the current connected stream")
        owner = self._worker.recovery_owner
        if login != self._recovery_login or controller != self._controller:
            self._lease = owner.handover(login)
            self._recovery_login, self._controller = login, controller
        return {"state": "connected", "path": str(owner.path),
                "connection_id": login, "lease": self._lease}

    def revoke_recovery(self, controller):
        if self._worker is not None and self._controller == controller:
            self._worker.recovery_owner.revoke(self._lease)
            self._controller = None

    def snapshot(self):
        return self._worker.snapshot() if self._worker else {"state": "stopped"}

    async def _stop_worker(self):
        if self._worker is None:
            return
        stopping = asyncio.create_task(asyncio.to_thread(self._worker.stop))
        try:
            await asyncio.shield(stopping)
        except asyncio.CancelledError:
            # Keep the lock until this handle has been reaped. A second Start
            # must never reuse a worker already being stopped in another thread.
            await stopping
            raise
        finally:
            if self._worker.process.poll() is not None:
                self._worker = None
                self._recovery_login = self._controller = self._lease = None

    async def start(self, upstream_port):
        if type(upstream_port) is not int or not 1 <= upstream_port <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        async with self._lock:
            if self._worker:
                if self._worker.process.poll() is None:
                    if self._worker.upstream_port != upstream_port:
                        raise ValueError("Stop the current local transport before changing its endpoint")
                    return self.snapshot()
                await self._stop_worker()
            worker = _Worker(upstream_port)
            self._worker = worker
            try:
                ready = await asyncio.to_thread(worker.first_report.wait, 5)
                info = worker.snapshot()
                if not ready or info["state"] not in {"listening", "connecting", "connected"}:
                    raise RuntimeError("Transport failed to become ready")
                return info
            except BaseException:
                await self._stop_worker()
                raise

    async def stop(self):
        async with self._lock:
            await self._stop_worker()
            return self.snapshot()
