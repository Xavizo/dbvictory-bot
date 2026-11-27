r"""
Named pipe client for communicating with the injected dbvbot.dll.

The DLL creates \\.\pipe\dbvbot and serves creature data as newline-delimited
JSON. This module provides a clean Python interface to that pipe.
"""

import ctypes
import ctypes.wintypes
import json
import logging

log = logging.getLogger("dll_bridge")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if hasattr(ctypes, "WinDLL") else None

# ctypes defaults to a 32-bit int return value. HANDLE is pointer-sized,
# including INVALID_HANDLE_VALUE on 64-bit Python.
from ctypes import wintypes
if kernel32 is not None:
    kernel32.CreateFileA.argtypes = [wintypes.LPCSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CreateFileA.restype = wintypes.HANDLE
    kernel32.SetNamedPipeHandleState.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
                                           ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    kernel32.SetNamedPipeHandleState.restype = wintypes.BOOL
    kernel32.WaitNamedPipeA.argtypes = [wintypes.LPCSTR, wintypes.DWORD]
    kernel32.WaitNamedPipeA.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                             ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.WriteFile.argtypes = kernel32.ReadFile.argtypes
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                  ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                                  ctypes.POINTER(wintypes.DWORD)]
    kernel32.PeekNamedPipe.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


# Win32 constants
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.wintypes.HANDLE(-1).value
PIPE_READMODE_BYTE = 0x00000000

PIPE_NAME = b"\\\\.\\pipe\\dbvbot"


class DllBridge:
    """Client for the dbvbot.dll named pipe."""

    def __init__(self, *, read_only=False, profile=None):
        from dbv.client_bridge.client_profiles import UPDATED, select_profile
        if profile is not None:
            if select_profile(profile.sha256) != profile or profile.observer != read_only:
                raise ValueError("DLL profile does not match the verified client")
        self.profile = profile or (UPDATED if read_only else None)
        self.read_only = read_only
        self.pipe_name = self.profile.observer_pipe if read_only else PIPE_NAME
        self._handle = None
        self._buffer = ""
        self.marker_ready=False;self.marker_seen=0;self.marker_sequence=0
        self.marker_desired=0;self.marker_sent_at=-1e9;self.marker_sent_id=None
        self.client_target=0xFFFFFFFF;self.marked_target=0;self.marker_result=0
        self._extras = []   # non-creature JSON responses (scan results, etc.)
        self.dll_pos = (0, 0, 0)  # player position from DLL global memory scan

    @property
    def connected(self) -> bool:
        return self._handle is not None

    def connect(self) -> bool:
        """Open the named pipe. Returns True on success."""
        if kernel32 is None:
            return False
        if self._handle is not None:
            return True

        handle = kernel32.CreateFileA(
            self.pipe_name,
            GENERIC_READ | GENERIC_WRITE,
            0,       # no sharing
            None,    # default security
            OPEN_EXISTING,
            0,       # default attributes
            None,    # no template
        )

        if handle is None or handle == INVALID_HANDLE_VALUE:
            err = ctypes.get_last_error()
            log.debug(f"Pipe not available (error {err})")
            return False

        # Set to byte read mode
        mode = ctypes.wintypes.DWORD(PIPE_READMODE_BYTE)
        if not kernel32.SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None):
            kernel32.CloseHandle(handle)
            return False

        self._handle = handle
        self._buffer = ""
        log.info("Connected to dbvbot pipe")
        return True

    def pipe_exists(self) -> bool:
        """Check if the named pipe exists (DLL is loaded and running)."""
        if kernel32 is None:
            return False
        if self._handle is not None:
            return True  # already connected
        if kernel32.WaitNamedPipeA(self.pipe_name, 1):
            return True
        # A busy pipe still exists; querying availability must not consume its
        # sole connection or report an already-connected bridge as missing.
        return ctypes.get_last_error() in (121, 231)  # SEM_TIMEOUT, PIPE_BUSY

    def send_command(self, cmd: dict) -> bool:
        """Send a JSON command to the DLL. Returns True on success."""
        if self.read_only or self._handle is None:
            return False

        data = (json.dumps(cmd) + "\n").encode("utf-8")
        written = ctypes.wintypes.DWORD(0)
        ok = kernel32.WriteFile(self._handle, data, len(data), ctypes.byref(written), None)
        if not ok:
            log.warning(f"WriteFile failed (error {ctypes.get_last_error()})")
            self.disconnect()
            return False
        return True

    def set_target_marker(self, creature_id):
        """Called only after dispatch or manual cancellation; no pipe IO here."""
        if type(creature_id) is not int or (creature_id and not 0x40000000<=creature_id<0x80000000):return False
        self.marker_desired=creature_id
        return True

    def refresh_target_marker(self, *, active):
        """Short visual lease; no native Game target or arbitrary commands."""
        import time
        now=time.monotonic()
        if not active:self.marker_desired=0
        ident=self.marker_desired
        if (not self.read_only or self._handle is None or not self.marker_ready
            or not 0<=now-self.marker_seen<.5 or self.client_target==0xFFFFFFFF):return False
        if ident==self.marker_sent_id and now-self.marker_sent_at<.3:return False
        self.marker_sequence+=1
        data=f'M {ident} {self.client_target} {self.marker_sequence}\n'.encode('ascii')
        written=ctypes.wintypes.DWORD(0)
        ok=kernel32.WriteFile(self._handle,data,len(data),ctypes.byref(written),None)
        if ok and written.value==len(data):
            self.marker_sent_at=now;self.marker_sent_id=ident;return True
        return False

    def read_creatures(self) -> list[dict] | None:
        """Non-blocking read of creature data from the pipe.

        Returns a list of creature dicts, or None if no data available.
        """
        if self._handle is None:
            return None

        # Peek to see if data is available
        bytes_available = ctypes.wintypes.DWORD(0)
        ok = kernel32.PeekNamedPipe(
            self._handle, None, 0, None, ctypes.byref(bytes_available), None
        )
        if not ok:
            err = ctypes.get_last_error()
            log.warning(f"PeekNamedPipe failed (error {err}) — pipe broken, disconnecting")
            self.disconnect()
            return None

        if bytes_available.value == 0:
            return None

        # Read available data
        buf = ctypes.create_string_buffer(bytes_available.value + 1)
        bytes_read = ctypes.wintypes.DWORD(0)
        ok = kernel32.ReadFile(self._handle, buf, bytes_available.value, ctypes.byref(bytes_read), None)
        if not ok or bytes_read.value == 0:
            self.disconnect()
            return None

        self._buffer += buf.raw[:bytes_read.value].decode("utf-8", errors="replace")

        # Parse complete lines — use the LAST complete JSON (most recent data)
        creatures = None
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if "creatures" in data:
                    creatures = data["creatures"]
                    if self.read_only:
                        import time
                        self.marker_ready=data.get('target_marker') is True
                        self.marker_seen=time.monotonic()
                        value=data.get('client_target')
                        self.client_target=value if type(value)is int and 0<=value<=0xFFFFFFFF else 0xFFFFFFFF
                        self.marked_target=data.get('marked_target',0)
                        self.marker_result=data.get('target_marker_result',0)
                    # Store DLL-reported position (from global memory scan)
                    if "px" in data and data["px"] > 0:
                        self.dll_pos = (data["px"], data["py"], data["pz"])
                else:
                    log.warning(f"DLL extra response: {list(data.keys())}")
                    self._extras.append(data)
            except json.JSONDecodeError:
                if "scan_light" in line:
                    log.error(f"JSON parse failed for scan_light line: {line[:200]}")
                pass

        return creatures

    def pop_extras(self) -> list[dict]:
        """Return and clear any non-creature JSON responses (scan results, etc.)."""
        extras = self._extras
        self._extras = []
        return extras

    def disconnect(self):
        """Close the pipe handle; the visual lease expires in <= 1.2 s."""
        self.marker_desired=0;self.marker_ready=False;self.marker_seen=0
        self.marker_sent_id=None
        if self._handle is not None:
            try:
                kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None
            self._buffer = ""
            log.info("Disconnected from dbvbot pipe")
