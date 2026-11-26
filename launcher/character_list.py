"""Recognize the two public labels in a DBVictory character-selection window."""

from contextlib import ExitStack
from functools import lru_cache
from pathlib import Path
import struct
import sys


@lru_cache
def labels(scale):
    blocks = Path(__file__).with_name("character_list_labels.txt").read_text("ascii").strip().split("\n\n")
    patterns = []
    for block in blocks:
        lines = block.splitlines()
        width = round(len(lines[0]) * scale)
        height = round(len(lines) * scale)
        source_x = [min(int(x / scale), len(lines[0]) - 1) for x in range(width)]
        rows = []
        for y in range(height):
            line = lines[min(int(y / scale), len(lines) - 1)]
            rows.append(bytes(line[x] == "#" for x in source_x))
        patterns.append(rows)
    return patterns


def locate(ink, width, height, rows):
    """Find short distinctive runs first, then compare all strokes in the label."""
    w, h = len(rows[0]), len(rows)
    if w > width or h > height:
        return
    glyphs = sum(sum(row) for row in rows)
    packed_rows = [int.from_bytes(row, "little") for row in rows]
    seen = set()
    for fraction, row_index in ((0.13, h//3), (0.45, h//2), (0.70, 2*h//3)):
        x_offset = int(w * fraction)
        anchor = rows[row_index][x_offset:x_offset+20]
        start = 0
        while (pos := ink.find(anchor, start)) >= 0:
            start = pos + 1
            origin = pos - row_index * width - x_offset
            y, x = divmod(origin, width)
            if origin in seen or x+w > width or y < 0 or y+h > height:
                continue
            seen.add(origin)
            common = sample = 0
            for j, mask in enumerate(packed_rows):
                offset = origin + j * width
                actual = int.from_bytes(ink[offset:offset+w], "little")
                common += (actual & mask).bit_count()
                sample += actual.bit_count()
            # Intersection over union of the reference strokes and observed ink.
            if common >= 0.85 * (glyphs + sample - common):
                yield x, y


def character_list_visible(ink, width, height, dpi=96):
    for scale in dict.fromkeys((1.0, max(0.75, min(dpi / 96, 3.0)))):
        heading, button = labels(scale)
        for x, y in locate(ink, width, height, heading):
            # Both captions belong to the same dialog. Search its lower part only.
            left = max(0, x - round(32 * scale))
            right = min(width, x + round(182 * scale))
            top = y + round(135 * scale)
            bottom = min(height, y + round(340 * scale))
            if right <= left or bottom <= top:
                continue
            region = b"".join(ink[i*width+left:i*width+right] for i in range(top, bottom))
            if next(locate(region, right-left, bottom-top, button), None) is not None:
                return True
    return False


def check_window(hwnd, pid):
    import ctypes
    from ctypes import wintypes
    import win32gui
    import win32process

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.SetThreadDpiAwarenessContext.argtypes = [wintypes.HANDLE]
    user32.SetThreadDpiAwarenessContext(wintypes.HANDLE(-4))
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    gdi.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    gdi.CreateDIBSection.restype = wintypes.HBITMAP

    def is_target():
        return (win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd) and not win32gui.IsIconic(hwnd)
                and win32process.GetWindowThreadProcessId(hwnd)[1] == pid)

    if not is_target():
        return False
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right-left, bottom-top
    if width < 150 or height < 180 or width * height > 36_000_000:
        return False

    # A top-down 32-bit DIB gives predictable BGRX rows. PrintWindow draws only
    # the selected window, including when the launcher overlaps it.
    header = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    with ExitStack() as cleanup:
        source = win32gui.GetWindowDC(hwnd)
        cleanup.callback(win32gui.ReleaseDC, hwnd, source)
        dc = win32gui.CreateCompatibleDC(source)
        cleanup.callback(win32gui.DeleteDC, dc)
        bitmap = gdi.CreateDIBSection(source, header, 0, ctypes.byref(bits), None, 0)
        if not bitmap:
            return False
        cleanup.callback(win32gui.DeleteObject, bitmap)
        previous = win32gui.SelectObject(dc, bitmap)
        cleanup.callback(win32gui.SelectObject, dc, previous)
        if not user32.PrintWindow(hwnd, dc, 2) or not is_target():
            return False
        gdi.GdiFlush()
        pixels = ctypes.string_at(bits, width * height * 4)
    # Both captions are light text on a dark background.
    ink = pixels[1::4].translate(bytes(int(i >= 151) for i in range(256)))
    return character_list_visible(ink, width, height, user32.GetDpiForWindow(hwnd))


if __name__ == "__main__":
    print("ready" if check_window(int(sys.argv[1]), int(sys.argv[2])) else "waiting")
