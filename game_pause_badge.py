"""Click-through pause badge owned and painted by the bot on its Windows UI thread."""
import ctypes
from ctypes import wintypes

import win32api
import win32con as con
import win32gui as gui
import win32process
import win32ts
from pywintypes import error as WinError


class GamePauseBadge:
    def __init__(self):
        self.hwnd = 0
        self.target = None
        self.is_game = False
        self.bounds = None
        self.font = None
        self.dpi = 0
        self.module = win32api.GetModuleHandle(None)
        self.name = f"DBVictoryPauseBadge_{id(self)}"
        # Window geometry and placement must share physical coordinates on mixed-DPI monitors.
        self.set_dpi_context = ctypes.windll.user32.SetThreadDpiAwarenessContext
        self.set_dpi_context.argtypes = [wintypes.HANDLE]
        self.set_dpi_context.restype = wintypes.HANDLE
        self.dpi_for_window = ctypes.windll.user32.GetDpiForWindow
        self.dpi_for_window.argtypes = [wintypes.HWND]
        self.dpi_for_window.restype = wintypes.UINT
        wc = gui.WNDCLASS()
        wc.hInstance = self.module
        wc.lpszClassName = self.name
        wc.lpfnWndProc = self.window_proc
        self.brush = gui.CreateSolidBrush(win32api.RGB(255, 210, 120))
        self.old_context = self.set_dpi_context(wintypes.HANDLE(-4))
        try:
            gui.RegisterClass(wc)
            flags = con.WS_EX_TOOLWINDOW | con.WS_EX_NOACTIVATE | con.WS_EX_LAYERED | con.WS_EX_TRANSPARENT
            self.hwnd = gui.CreateWindowEx(flags, self.name, "Pauza bota", con.WS_POPUP,
                                           0, 0, 0, 0, 0, 0, self.module, None)
            gui.SetLayeredWindowAttributes(self.hwnd, 0, 246, con.LWA_ALPHA)
        except Exception:
            self.close()
            raise

    def window_proc(self, hwnd, msg, wp, lp):
        if msg == con.WM_NCHITTEST:
            return con.HTTRANSPARENT
        if msg == con.WM_PAINT:
            dc, paint = gui.BeginPaint(hwnd)
            try:
                bounds = gui.GetClientRect(hwnd)
                gui.FillRect(dc, bounds, self.brush)
                gui.SetBkMode(dc, con.TRANSPARENT)
                gui.SetTextColor(dc, win32api.RGB(43, 31, 12))
                old = gui.SelectObject(dc, self.font) if self.font else None
                try:
                    gui.DrawText(dc, "Ⅱ   PAUZA BOTA", -1, bounds, con.DT_CENTER | con.DT_VCENTER | con.DT_SINGLELINE)
                finally:
                    if old:
                        gui.SelectObject(dc, old)
            finally:
                gui.EndPaint(hwnd, paint)
            return 0
        return gui.DefWindowProc(hwnd, msg, wp, lp)

    def hide(self):
        if self.bounds is not None:
            gui.ShowWindow(self.hwnd, con.SW_HIDE)
            self.bounds = None

    def update(self, paused):
        if not paused:
            self.hide()
            self.target = None
            return
        try:
            game = gui.GetForegroundWindow()
            if not game or gui.IsIconic(game):
                self.hide()
                return
            pid = win32process.GetWindowThreadProcessId(game)[1]
            if (game, pid) != self.target:
                self.is_game = any(p == pid and name and name.casefold() in {"dbvstart.exe", "dbvstart_gl.exe"}
                                   for _, p, name, _ in win32ts.WTSEnumerateProcesses())
                self.target = (game, pid)
            if not self.is_game:
                self.hide()
                return
            left, top = gui.ClientToScreen(game, (0, 0))
            _, _, width, height = gui.GetClientRect(game)
            dpi = self.dpi_for_window(game) or 96
            w, h, margin = (round(n * dpi / 96) for n in (168, 36, 10))
            if w > width or h + margin > height:
                self.hide()
                return
            bounds = (left + (width - w) // 2, top + margin, w, h)
            if dpi != self.dpi:
                spec = gui.LOGFONT()
                spec.lfFaceName, spec.lfHeight, spec.lfWeight = "Segoe UI", -round(16 * dpi / 96), 700
                font = gui.CreateFontIndirect(spec)
                if self.font:
                    gui.DeleteObject(self.font)
                self.font, self.dpi = font, dpi
            if bounds != self.bounds:
                gui.SetWindowPos(self.hwnd, con.HWND_TOPMOST, *bounds, con.SWP_NOACTIVATE | con.SWP_SHOWWINDOW)
                self.bounds = bounds
                gui.InvalidateRect(self.hwnd, None, True)
        except WinError:
            # The game window can disappear between foreground and geometry reads.
            self.hide()
            self.target = None

    def close(self):
        if self.hwnd:
            gui.DestroyWindow(self.hwnd)
        try:
            gui.UnregisterClass(self.name, self.module)
        except WinError:
            pass
        if self.font:
            gui.DeleteObject(self.font)
        gui.DeleteObject(self.brush)
        if self.old_context:
            self.set_dpi_context(self.old_context)
