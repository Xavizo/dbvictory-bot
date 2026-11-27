"""Load the observer DLL into the running game client.

The client (``dbvStart.exe``) is a 32-bit program, while the bot usually runs
on 64-bit Python. A 64-bit process cannot use its own ``LoadLibraryW`` address
for a 32-bit target, so the address is computed from the target's 32-bit
``kernel32.dll``: its load address in the client plus the export's offset read
from ``SysWOW64\\kernel32.dll`` on disk.

The DLL path is passed as UTF-16 (``LoadLibraryW``), so install folders with
non-ASCII names (for example a Windows user called "Łukasz") work.
"""
from __future__ import annotations

import ctypes
import logging
import os
import struct
import sys
from pathlib import Path

log = logging.getLogger("injector")

CLIENT_EXE = "dbvstart.exe"
LOAD_TIMEOUT_MS = 15_000

_CREATE_THREAD, _VM_OPERATION, _VM_READ, _VM_WRITE, _QUERY_INFO = 0x2, 0x8, 0x10, 0x20, 0x400
_ACCESS = _CREATE_THREAD | _VM_OPERATION | _VM_READ | _VM_WRITE | _QUERY_INFO
_COMMIT_RESERVE, _RELEASE, _READWRITE = 0x3000, 0x8000, 0x04
_SNAP_MODULES = 0x08 | 0x10   # TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32 (the WOW64 modules)
_SNAPSHOT_RETRIES = 5        # a snapshot can fail while the client is loading modules
_WAIT_OBJECT_0 = 0


class InjectionError(RuntimeError):
    pass


# ── PE export lookup (pure, testable on any OS) ────────────────────

def export_rva(image: bytes, name: str) -> int:
    """RVA of an exported function in a PE file image (as read from disk)."""
    def u16(at): return struct.unpack_from("<H", image, at)[0]
    def u32(at): return struct.unpack_from("<I", image, at)[0]

    try:
        if image[:2] != b"MZ":
            raise InjectionError("not a PE file")
        pe = u32(0x3C)
        if image[pe:pe + 4] != b"PE\0\0":
            raise InjectionError("not a PE file")
        sections, optional_size = u16(pe + 6), u16(pe + 20)
        optional = pe + 24
        directories = optional + {0x10B: 96, 0x20B: 112}.get(u16(optional), -1)
        if directories < optional:
            raise InjectionError("unknown PE optional header")
        exports, exports_size = u32(directories), u32(directories + 4)  # data directory 0: exports
        table = optional + optional_size
        spans = [(u32(s + 12), max(u32(s + 8), u32(s + 16)), u32(s + 20))
                 for s in range(table, table + 40 * sections, 40)]

        def at(rva):
            for start, size, raw in spans:
                if start <= rva < start + size:
                    return raw + rva - start
            raise InjectionError(f"RVA {rva:#x} is outside every section")

        directory = at(exports)
        count, functions, names, ordinals = (u32(directory + o) for o in (24, 28, 32, 36))
        wanted = name.encode("ascii")
        for i in range(count):
            start = at(u32(at(names) + 4 * i))
            if image[start:image.index(b"\0", start)] == wanted:
                rva = u32(at(functions) + 4 * u16(at(ordinals) + 2 * i))
                if exports <= rva < exports + exports_size:
                    # A forwarder names another DLL's function; calling it would crash the client.
                    raise InjectionError(f"{name} is forwarded to another DLL")
                return rva
    except (struct.error, ValueError, IndexError) as exc:
        raise InjectionError(f"malformed PE file: {exc}") from None
    raise InjectionError(f"{name} is not exported")


# ── Win32 plumbing ──────────────────────────────────────────────────

def _win32():
    if not hasattr(ctypes, "WinDLL"):
        raise InjectionError("DLL injection needs Windows")
    from ctypes import wintypes as w
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    # ctypes functions are unhashable, so the table is keyed by name.
    signatures = {
        "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        "CloseHandle": ([w.HANDLE], w.BOOL),
        "IsWow64Process": ([w.HANDLE, ctypes.POINTER(w.BOOL)], w.BOOL),
        "VirtualAllocEx": ([w.HANDLE, ctypes.c_void_p, ctypes.c_size_t, w.DWORD, w.DWORD], ctypes.c_void_p),
        "VirtualFreeEx": ([w.HANDLE, ctypes.c_void_p, ctypes.c_size_t, w.DWORD], w.BOOL),
        "WriteProcessMemory": ([w.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                ctypes.POINTER(ctypes.c_size_t)], w.BOOL),
        "CreateRemoteThread": ([w.HANDLE, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                ctypes.c_void_p, w.DWORD, ctypes.c_void_p], w.HANDLE),
        "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
        "GetExitCodeThread": ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
        "GetModuleHandleW": ([w.LPCWSTR], w.HMODULE),
        "GetProcAddress": ([w.HMODULE, w.LPCSTR], ctypes.c_void_p),
        "CreateToolhelp32Snapshot": ([w.DWORD, w.DWORD], w.HANDLE),
        "Module32FirstW": ([w.HANDLE, ctypes.c_void_p], w.BOOL),
        "Module32NextW": ([w.HANDLE, ctypes.c_void_p], w.BOOL),
    }
    for name, (args, result) in signatures.items():
        function = getattr(k, name)
        function.argtypes, function.restype = args, result
    return k


def _fail(what):
    raise InjectionError(f"{what} failed (Windows error {ctypes.get_last_error()})")


def find_client_pid() -> int:
    import pymem.process
    found = [int(p.th32ProcessID) for p in pymem.process.list_processes()
             if bytes(p.szExeFile).split(b"\0", 1)[0].decode("ascii", "replace").lower() == CLIENT_EXE]
    if len(found) != 1:
        raise InjectionError("Expected exactly one running DBVictory client")
    return found[0]


class _ModuleEntry(ctypes.Structure):
    """MODULEENTRY32W."""
    _fields_ = [("size", ctypes.c_uint32), ("module_id", ctypes.c_uint32), ("pid", ctypes.c_uint32),
                ("global_usage", ctypes.c_uint32), ("process_usage", ctypes.c_uint32),
                ("base", ctypes.c_void_p), ("base_size", ctypes.c_uint32), ("handle", ctypes.c_void_p),
                ("name", ctypes.c_wchar * 256), ("path", ctypes.c_wchar * 260)]


def _module_base_32(k, pid: int, name: str) -> int:
    """Load address of a 32-bit module in the client.

    A Toolhelp module snapshot lists a WOW64 process's 32-bit modules to a
    64-bit caller; psapi's name lookups only see the 64-bit loader list.
    """
    invalid = ctypes.c_void_p(-1).value
    for _ in range(_SNAPSHOT_RETRIES):
        snapshot = k.CreateToolhelp32Snapshot(_SNAP_MODULES, pid)
        if snapshot and snapshot != invalid:
            break
    else:
        _fail("CreateToolhelp32Snapshot")
    try:
        entry = _ModuleEntry()
        entry.size = ctypes.sizeof(entry)
        found = k.Module32FirstW(snapshot, ctypes.byref(entry))
        while found:
            if entry.name.lower() == name:
                return entry.base
            found = k.Module32NextW(snapshot, ctypes.byref(entry))
    finally:
        k.CloseHandle(snapshot)
    raise InjectionError(f"{name} is not loaded in the client yet")


def _load_library_address(k, process, pid) -> int:
    from ctypes import wintypes as w
    wow64 = w.BOOL()
    if not k.IsWow64Process(process, ctypes.byref(wow64)):
        _fail("IsWow64Process")
    if wow64.value and sys.maxsize > 2**32:
        system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "SysWOW64" / "kernel32.dll"
        return _module_base_32(k, pid, "kernel32.dll") + export_rva(system.read_bytes(), "LoadLibraryW")
    address = k.GetProcAddress(k.GetModuleHandleW("kernel32.dll"), b"LoadLibraryW")
    if not address:
        _fail("GetProcAddress")
    return address


def inject(dll_path, *, pid: int | None = None) -> int:
    """Make the client load ``dll_path``. Returns the 32-bit module handle."""
    path = Path(dll_path).resolve()
    if not path.is_file():
        raise InjectionError(f"DLL not found: {path}")
    k = _win32()
    pid = find_client_pid() if pid is None else pid
    process = k.OpenProcess(_ACCESS, False, pid)
    if not process:
        _fail("OpenProcess")
    try:
        entry = _load_library_address(k, process, pid)
        text = (str(path) + "\0").encode("utf-16-le")
        remote = k.VirtualAllocEx(process, None, len(text), _COMMIT_RESERVE, _READWRITE)
        if not remote:
            _fail("VirtualAllocEx")
        try:
            if not k.WriteProcessMemory(process, remote, text, len(text), None):
                _fail("WriteProcessMemory")
            thread = k.CreateRemoteThread(process, None, 0, entry, remote, 0, None)
            if not thread:
                _fail("CreateRemoteThread")
            try:
                if k.WaitForSingleObject(thread, LOAD_TIMEOUT_MS) != _WAIT_OBJECT_0:
                    # The loader still owns the path buffer; leave it allocated.
                    remote = None
                    raise InjectionError("The client did not finish loading the DLL in time")
                module = ctypes.c_ulong()
                if not k.GetExitCodeThread(thread, ctypes.byref(module)):
                    _fail("GetExitCodeThread")
            finally:
                k.CloseHandle(thread)
        finally:
            if remote:
                k.VirtualFreeEx(process, remote, 0, _RELEASE)
    finally:
        k.CloseHandle(process)
    if not module.value:
        raise InjectionError("The client refused to load the DLL")
    log.info("observer DLL loaded at %#010x", module.value)
    return module.value
