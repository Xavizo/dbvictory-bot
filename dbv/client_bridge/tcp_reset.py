"""Drop a process's TCP connections so the game client returns to its login screen.

Windows lists every IPv4 connection with its owning process
(GetExtendedTcpTable) and lets an administrator delete one (SetTcpEntry
with the DELETE_TCB state). The bot runs elevated already, because it
reads client memory.

``connections_to_close`` holds the filtering, so it can be tested without
Windows; ``close_connections`` is the Windows part.
"""
from __future__ import annotations

import ctypes
import logging
import socket
import struct

log = logging.getLogger("tcp_reset")

_AF_INET = 2
_OWNER_PID_ALL = 5      # TCP_TABLE_OWNER_PID_ALL
_DELETE_TCB = 12        # MIB_TCP_STATE_DELETE_TCB
_ULONG = ctypes.c_uint32


class _Connection(ctypes.Structure):
    """MIB_TCPROW; addresses and ports in network byte order."""
    _fields_ = [(field, _ULONG) for field in ("state", "local_addr", "local_port", "remote_addr", "remote_port")]


class _OwnedConnection(ctypes.Structure):
    """MIB_TCPROW_OWNER_PID: a connection plus the id of the process that owns it."""
    _fields_ = _Connection._fields_ + [("pid", _ULONG)]


def _port(raw: int) -> int:
    return socket.ntohs(raw & 0xFFFF)


def _rows(table: bytes):
    """The rows of a MIB_TCPTABLE_OWNER_PID: a u32 count, then the rows."""
    count = struct.unpack_from("<I", table)[0]
    size = ctypes.sizeof(_OwnedConnection)
    for at in range(4, 4 + count * size, size):
        if at + size > len(table):
            return
        yield _OwnedConnection.from_buffer_copy(table, at)


def connections_to_close(table: bytes, pid: int, remote_ports) -> list[_OwnedConnection]:
    """Rows of ``pid`` connected to any of ``remote_ports``, the local proxy included."""
    return [row for row in _rows(table) if row.pid == pid and _port(row.remote_port) in remote_ports]


def _table(iphlpapi) -> bytes | None:
    size = _ULONG(0)
    iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, _AF_INET, _OWNER_PID_ALL, 0)
    buffer = ctypes.create_string_buffer(size.value)
    error = iphlpapi.GetExtendedTcpTable(buffer, ctypes.byref(size), False, _AF_INET, _OWNER_PID_ALL, 0)
    if error:
        log.warning("GetExtendedTcpTable failed: %s", error)
        return None
    return buffer.raw


def close_connections(pid: int, remote_ports) -> int:
    """Delete every connection of ``pid`` to ``remote_ports``. Returns how many were deleted."""
    iphlpapi = ctypes.windll.iphlpapi
    table = _table(iphlpapi)
    if table is None:
        return 0
    closed = 0
    for row in connections_to_close(table, pid, set(remote_ports)):
        address = socket.inet_ntoa(struct.pack("<I", row.remote_addr))
        log.info("Closing game connection: %s:%s (state=%s)", address, _port(row.remote_port), row.state)
        target = _Connection(_DELETE_TCB, row.local_addr, row.local_port, row.remote_addr, row.remote_port)
        error = iphlpapi.SetTcpEntry(ctypes.byref(target))
        if error:
            log.warning("  -> SetTcpEntry failed: %s", error)
        else:
            closed += 1
            log.info("  -> Closed successfully")
    return closed
