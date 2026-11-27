"""Point the running client at the local proxy.

The client keeps the game server's address as text (``"87.98.220.215"``)
in its memory. Before each login every copy is overwritten with
``"127.0.0.1"`` plus zero bytes, so the client connects to the proxy, which
forwards to the real server. Nothing else in the client is changed.

The search logic works on any object with ``regions()``, ``read()`` and
``write()``; ``ClientMemory`` provides them for the real Windows process.
"""
from __future__ import annotations

import ctypes
import logging

log = logging.getLogger("client_redirect")

PROXY_HOST = b"127.0.0.1"
LARGEST_REGION = 100 * 1024 * 1024   # bigger regions are graphics, never the address
ADDRESS_SPACE_END = 0x1_0000_0000      # the client is a 32-bit process

_COMMIT = 0x1000
_READABLE = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}  # R, RW, WC, XR, XRW, XWC (no guard/no-access)
# Code pages stay executable while unlocked, in case the text shares a page with code.
_UNLOCKED = 0x40


def find_text(memory, text: bytes):
    """Addresses of every copy of ``text`` in readable committed memory."""
    for base, size in memory.regions():
        try:
            data = memory.read(base, size)
        except OSError:
            continue  # the region changed while we looked; skip it
        start = data.find(text)
        while start != -1:
            yield base + start
            start = data.find(text, start + 1)


def redirect_server_address(memory, server_host: str, proxy_host: bytes = PROXY_HOST) -> int:
    """Replace every copy of ``server_host`` with ``proxy_host``. Returns how many changed."""
    old = server_host.encode("ascii")
    if len(proxy_host) > len(old):
        raise ValueError("the proxy address must not be longer than the server address")
    new = proxy_host.ljust(len(old), b"\0")
    changed = 0
    for address in list(find_text(memory, old)):
        try:
            memory.write(address, new)
            if memory.read(address, len(new)) == new:
                changed += 1
            else:
                log.warning("server address at %#010x did not change", address)
        except OSError as exc:
            log.warning("could not redirect the server address at %#010x: %s", address, exc)
    log.info("redirected %d copies of the server address", changed)
    return changed


class _Region(ctypes.Structure):
    """MEMORY_BASIC_INFORMATION; 64-bit Windows adds a partition id before the size."""
    _fields_ = ([("base", ctypes.c_void_p), ("allocation_base", ctypes.c_void_p),
                 ("allocation_protect", ctypes.c_ulong)]
                + ([("partition", ctypes.c_ushort)] if ctypes.sizeof(ctypes.c_void_p) == 8 else [])
                + [("size", ctypes.c_size_t), ("state", ctypes.c_ulong),
                   ("protect", ctypes.c_ulong), ("type", ctypes.c_ulong)])


class ClientMemory:
    """Memory of one process, opened through an existing pymem session."""

    def __init__(self, pm):
        self._pm = pm
        self._k = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k.VirtualQueryEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        self._k.VirtualQueryEx.restype = ctypes.c_size_t
        self._k.VirtualProtectEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                             ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
        self._k.VirtualProtectEx.restype = ctypes.c_int

    def regions(self):
        region, address = _Region(), 0
        while address < ADDRESS_SPACE_END:
            if not self._k.VirtualQueryEx(self._pm.process_handle, address, ctypes.byref(region),
                                          ctypes.sizeof(region)):
                return
            base, size = region.base or 0, region.size
            if (region.state == _COMMIT and region.protect in _READABLE and 0 < size <= LARGEST_REGION):
                yield base, size
            address = base + size

    def read(self, address, size):
        try:
            return self._pm.read_bytes(address, size)
        except Exception as exc:  # pymem raises its own error types
            raise OSError(str(exc)) from None

    def write(self, address, data):
        before = ctypes.c_ulong()
        handle = self._pm.process_handle
        unlocked = self._k.VirtualProtectEx(handle, address, len(data), _UNLOCKED, ctypes.byref(before))
        try:
            self._pm.write_bytes(address, data, len(data))
        except Exception as exc:
            raise OSError(str(exc)) from None
        finally:
            if unlocked:
                self._k.VirtualProtectEx(handle, address, len(data), before.value, ctypes.byref(ctypes.c_ulong()))
