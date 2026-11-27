"""Copy an executable and give the copy the bot's icon.

Usage: python -I tools/brand_exe.py SOURCE.exe TARGET.exe ICON.ico

The release ships CPython's pythonw.exe and python.exe again as
venv/Scripts/DBVictory.exe (the launcher and panel windows) and
venv/Scripts/DBVictory Engine.exe (the engine), so Task Manager and the taskbar
show the bot instead of Python. A renamed copy runs exactly like the original:
CPython finds its standard library next to the executable, whatever its name.

The icon resources are replaced with the Windows resource-update API (ctypes,
no extra packages). The copy loses the Python Software Foundation's signature,
like any edited executable; the release's own files are unsigned anyway.
"""
import argparse
import shutil
import struct
import sys
from pathlib import Path

RT_ICON, RT_GROUP_ICON = 3, 14


def read_ico(data):
    """[(width, height, colors, planes, bit_count, image bytes)] from an .ico file."""
    reserved, kind, count = struct.unpack_from("<HHH", data)
    if reserved != 0 or kind != 1 or not count:
        raise ValueError("not an icon file")
    images = []
    for i in range(count):
        width, height, colors, _, planes, bits, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        image = data[offset:offset + size]
        if len(image) != size:
            raise ValueError("truncated icon file")
        images.append((width, height, colors, planes, bits, image))
    return images


def group_icon(images, first_id=1):
    """The RT_GROUP_ICON resource naming RT_ICON resources first_id, first_id + 1, …"""
    data = struct.pack("<HHH", 0, 1, len(images))
    for n, (width, height, colors, planes, bits, image) in enumerate(images):
        data += struct.pack("<BBBBHHIH", width, height, colors, 0, planes, bits, len(image), first_id + n)
    return data


def _existing(path, kinds):
    """{kind: [(name, language)]} of the executable's current resources of these kinds."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LoadLibraryExW.restype = wintypes.HMODULE
    kernel.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    kernel.FreeLibrary.argtypes = [wintypes.HMODULE]
    names_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    langs_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p,
                                    wintypes.WORD, ctypes.c_void_p)
    kernel.EnumResourceNamesW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, names_proc, ctypes.c_void_p]
    kernel.EnumResourceLanguagesW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, langs_proc,
                                              ctypes.c_void_p]
    module = kernel.LoadLibraryExW(str(path), None, 0x2)  # LOAD_LIBRARY_AS_DATAFILE
    if not module:
        raise ctypes.WinError(ctypes.get_last_error())
    found = {kind: [] for kind in kinds}
    try:
        for kind in kinds:
            names = []

            def on_name(_module, _kind, name, _param):
                # An integer ID (≤ 0xFFFF) or a pointer to the resource's string name.
                names.append(name if name <= 0xFFFF else ctypes.wstring_at(name))
                return True
            kernel.EnumResourceNamesW(module, kind, names_proc(on_name), None)
            for name in names:
                def on_language(_module, _kind, _name, language, _param):
                    found[kind].append((name, language))
                    return True
                key = ctypes.c_wchar_p(name) if isinstance(name, str) else name
                kernel.EnumResourceLanguagesW(module, kind, ctypes.cast(key, ctypes.c_void_p) if isinstance(name, str)
                                              else name, langs_proc(on_language), None)
    finally:
        kernel.FreeLibrary(module)
    return found


def set_icon(path, icon):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.BeginUpdateResourceW.restype = wintypes.HANDLE
    kernel.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
    kernel.UpdateResourceW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wintypes.WORD,
                                       ctypes.c_void_p, wintypes.DWORD]
    kernel.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
    images = read_ico(Path(icon).read_bytes())
    old = _existing(path, (RT_ICON, RT_GROUP_ICON))
    language = next((lang for _, lang in old[RT_GROUP_ICON]), 0)
    handle = kernel.BeginUpdateResourceW(str(path), False)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    keep = []  # resource buffers must outlive EndUpdateResource

    def update(kind, name, lang, data):
        key = ctypes.c_wchar_p(name) if isinstance(name, str) else name
        keep.append(key)
        buffer = None if data is None else ctypes.create_string_buffer(data, len(data))
        keep.append(buffer)
        if not kernel.UpdateResourceW(handle, kind, ctypes.cast(key, ctypes.c_void_p) if isinstance(name, str)
                                      else name, lang, buffer, 0 if data is None else len(data)):
            raise ctypes.WinError(ctypes.get_last_error())
    try:
        for kind in (RT_GROUP_ICON, RT_ICON):
            for name, lang in old[kind]:
                update(kind, name, lang, None)  # drop Python's icons
        for n, image in enumerate(images, 1):
            update(RT_ICON, n, language, image[5])
        update(RT_GROUP_ICON, 1, language, group_icon(images))
    except Exception:
        kernel.EndUpdateResourceW(handle, True)
        raise
    if not kernel.EndUpdateResourceW(handle, False):
        raise ctypes.WinError(ctypes.get_last_error())


def brand(source, target, icon):
    target = Path(target)
    shutil.copy2(source, target)
    set_icon(target, icon)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("icon", type=Path)
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        raise SystemExit("brand_exe.py runs on Windows (it edits PE resources)")
    print(f"Branded {brand(args.source, args.target, args.icon)}")


if __name__ == "__main__":
    main()
