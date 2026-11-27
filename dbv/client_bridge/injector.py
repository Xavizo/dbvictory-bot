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
_LIST_MODULES_32BIT = 0x01
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
        exports = u32(directories)  # data directory 0 is the export table
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
                return u32(at(functions) + 4 * u16(at(ordinals) + 2 * i))
    except (struct.error, ValueError, IndexError) as exc:
        raise InjectionError(f"malformed PE file: {exc}") from None
    raise InjectionError(f"{name} is not exported")


# ── Win32 plumbing ──────────────────────────────────────────────────

def _win32():
    if not hasattr(ctypes, "WinDLL"):
        raise InjectionError("DLL injection needs Windows")
    from ctypes import wintypes as w
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    p = ctypes.WinDLL("psapi", use_last_error=True)
    sig = {
        k.OpenProcess: ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        k.CloseHandle: ([w.HANDLE], w.BOOL),
        k.IsWow64Process: ([w.HANDLE, ctypes.POINTER(w.BOOL)], w.BOOL),
        k.VirtualAllocEx: ([w.HANDLE, ctypes.c_void_p, ctypes.c_size_t, w.DWORD, w.DWORD], ctypes.c_void_p),
        k.VirtualFreeEx: ([w.HANDLE, ctypes.c_void_p, ctypes.c_size_t, w.DWORD], w.BOOL),
        k.WriteProcessMemory: ([w.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                ctypes.POINTER(ctypes.c_size_t)], w.BOOL),
        k.CreateRemoteThread: ([w.HANDLE, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                ctypes.c_void_p, w.DWORD, ctypes.c_void_p], w.HANDLE),
        k.WaitForSingleObject: ([w.HANDLE, w.DWORD], w.DWORD),
        k.GetExitCodeThread: ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
        k.GetModuleHandleW: ([w.LPCWSTR], w.HMODULE),
        k.GetProcAddress: ([w.HMODULE, w.LPCSTR], ctypes.c_void_p),
        p.EnumProcessModulesEx: ([w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), w.DWORD], w.BOOL),
        p.GetModuleBaseNameW: ([w.HANDLE, ctypes.c_void_p, w.LPWSTR, w.DWORD], w.DWORD),
    }
    for fn, (args, result) in sig.items():
        fn.argtypes, fn.restype = args, result
    return k, p


def _fail(what):
    raise InjectionError(f"{what} failed (Windows error {ctypes.get_last_error()})")


def find_client_pid() -> int:
    import pymem.process
    found = [int(p.th32ProcessID) for p in pymem.process.list_processes()
             if bytes(p.szExeFile).split(b"\0", 1)[0].decode("ascii", "replace").lower() == CLIENT_EXE]
    if len(found) != 1:
        raise InjectionError("Expected exactly one running DBVictory client")
    return found[0]


def _module_base_32(psapi, process, name: str) -> int:
    from ctypes import wintypes as w
    slots = (ctypes.c_void_p * 1024)()
    needed = w.DWORD()
    if not psapi.EnumProcessModulesEx(process, slots, ctypes.sizeof(slots), ctypes.byref(needed),
                                      _LIST_MODULES_32BIT):
        _fail("EnumProcessModulesEx")
    label = ctypes.create_unicode_buffer(260)
    for base in slots[:needed.value // ctypes.sizeof(ctypes.c_void_p)]:
        if base and psapi.GetModuleBaseNameW(process, base, label, 260) and label.value.lower() == name:
            return base
    raise InjectionError(f"{name} is not loaded in the client yet")


def _load_library_address(k, psapi, process) -> int:
    from ctypes import wintypes as w
    wow64 = w.BOOL()
    if not k.IsWow64Process(process, ctypes.byref(wow64)):
        _fail("IsWow64Process")
    if wow64.value and sys.maxsize > 2**32:
        system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "SysWOW64" / "kernel32.dll"
        return _module_base_32(psapi, process, "kernel32.dll") + export_rva(system.read_bytes(), "LoadLibraryW")
    address = k.GetProcAddress(k.GetModuleHandleW("kernel32.dll"), b"LoadLibraryW")
    if not address:
        _fail("GetProcAddress")
    return address


def inject(dll_path, *, pid: int | None = None) -> int:
    """Make the client load ``dll_path``. Returns the 32-bit module handle."""
    path = Path(dll_path).resolve()
    if not path.is_file():
        raise InjectionError(f"DLL not found: {path}")
    k, psapi = _win32()
    pid = find_client_pid() if pid is None else pid
    process = k.OpenProcess(_ACCESS, False, pid)
    if not process:
        _fail("OpenProcess")
    try:
        entry = _load_library_address(k, psapi, process)
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
