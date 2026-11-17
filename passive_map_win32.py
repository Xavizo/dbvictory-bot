"""Win32 reader with QUERY_INFORMATION | VM_READ only. No write API."""
import ctypes
from ctypes import wintypes
import hashlib
from pathlib import Path
import pymem.process

from passive_map import MapReadError
from client_profiles import select_profile


class ReadOnlyClient:
    def __init__(self):
        self._handle = None
        self._k = ctypes.WinDLL("kernel32", use_last_error=True)
        k = self._k
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        k.ReadProcessMemory.restype = wintypes.BOOL
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                              wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        processes = [int(p.th32ProcessID) for p in pymem.process.list_processes()
                     if bytes(p.szExeFile).split(b"\0", 1)[0].lower() == b"dbvstart.exe"]
        if len(processes) != 1:
            raise MapReadError("Podgląd wymaga jednego otwartego klienta DBVictory.")
        self._handle = k.OpenProcess(0x0400 | 0x0010, False, processes[0])
        if not self._handle:
            raise MapReadError("Brak uprawnień do odczytu pamięci klienta.")
        try:
            length = wintypes.DWORD(32768)
            path = ctypes.create_unicode_buffer(length.value)
            if not k.QueryFullProcessImageNameW(self._handle, 0, path, ctypes.byref(length)):
                raise MapReadError("Nie można zweryfikować pliku klienta.")
            self.path = Path(path.value)
            if self.path.stat().st_size > 64 * 1024 * 1024:
                raise MapReadError("Nieobsługiwana wersja klienta.")
            try:
                self.profile = select_profile(hashlib.sha256(self.path.read_bytes()).hexdigest())
            except ValueError as exc:
                raise MapReadError(str(exc)) from None
            module = pymem.process.base_module(self._handle)
            if module is None:
                raise MapReadError("Proces klienta zakończył działanie.")
            self.base = module.lpBaseOfDll
        except Exception:
            self.close()
            raise

    def read(self, address, size):
        if not self._handle or not 0x10000 <= address < 0x7FFE0000 or not 0 < size <= 8192:
            raise MapReadError("Nieprawidłowy zakres odczytu mapy.")
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t()
        if not self._k.ReadProcessMemory(self._handle, address, buf, size, ctypes.byref(got)) or got.value != size:
            raise MapReadError("Dane mapy chwilowo niedostępne.")
        return buf.raw

    def close(self):
        if self._handle:
            self._k.CloseHandle(self._handle)
            self._handle = None
