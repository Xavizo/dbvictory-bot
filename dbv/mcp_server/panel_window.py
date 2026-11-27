"""The panel's desktop window: dashboard/.next in a pywebview (WebView2) window.

Started by the engine (`_launch_dashboard`) as `pythonw -m dbv.mcp_server.panel_window`
with the session token, the engine label and, when a session owns it, the tray's
exit file in the environment. Replaces the Electron main process:

- one main window and at most one route editor, both on http://127.0.0.1:4747 only;
- the page reaches the engine token and a few fixed operations through PanelApi
  (see dashboard/public/desktop-bridge.js), and only from that origin;
- closing hides to the tray; the tray restores, closes the panel or ends the bot;
- alert sounds keep polling while hidden (panel_alerts).
"""
import json
import os
import re
import sys
import threading
import time
import urllib.request

from dbv import paths
from dbv.mcp_server.panel_alerts import AlertSounds, NotificationAudio

PANEL_URL = "http://127.0.0.1:4747"
EXIT_FILE_ENV = "DBV_TRAY_EXIT_FILE"
MAX_PLAN = 4 * 1024 * 1024
LOADING_HTML = """<html><body style="margin:0;background:#111827;color:#9ca3af;display:flex;align-items:center;
justify-content:center;height:100vh;font-family:system-ui"><div style="text-align:center">
<h2 style="color:#e5e7eb;margin-bottom:8px">DBVictory Bot</h2><p id="msg">Starting dashboard...</p></div>
<script>let d=0;setInterval(()=>{d=(d+1)%4;document.getElementById("msg").textContent="Starting dashboard"+".".repeat(d);},400);
</script></body></html>"""
CELL_TEXT = re.compile(r"^Pole \(\d{1,5}, \d{1,5}, \d{1,2}\) — ")
DRAFT_ID = re.compile(r"^(?:[a-f0-9]{32})?$")


def is_panel_url(url):
    """Exactly the panel's origin, as Electron's isDashboardUrl checked."""
    if not isinstance(url, str):
        return False
    match = re.match(r"^(https?)://([^/?#]+)(?:[/?#]|$)", url)
    return bool(match) and f"{match.group(1)}://{match.group(2)}" == PANEL_URL


def valid_cell(text):
    return isinstance(text, str) and len(text) <= 16384 and "\0" not in text and bool(CELL_TEXT.match(text))


def valid_plan(text):
    if not isinstance(text, str) or len(text) > MAX_PLAN or "\0" in text:
        return False
    try:
        plan = json.loads(text)
    except ValueError:
        return False
    return (isinstance(plan, dict) and plan.get("preview_only") is True and isinstance(plan.get("scope"), str)
            and isinstance(plan.get("path"), list) and len(plan["path"]) <= 40000
            and isinstance(plan.get("points"), list) and len(plan["points"]) <= 128)


def write_clipboard(text):
    import win32clipboard
    import win32con
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


class PanelApi:
    """window.pywebview.api for one panel window. Everything checks the caller's page first."""

    def __init__(self, app, window_kind):
        self._app = app
        self._kind = window_kind

    def _trusted(self):
        window = self._app.windows.get(self._kind)
        if window is None or not self._app.token or not is_panel_url(window.get_current_url()):
            raise PermissionError("Open the dashboard through the bot's start_bot tool.")

    def get_token(self):
        self._trusted()
        return self._app.token

    def copy_cell(self, text):
        self._trusted()
        if not valid_cell(text):
            raise ValueError("Invalid map text.")
        self._app.clipboard(text)
        return True

    def copy_plan(self, text):
        self._trusted()
        if not valid_plan(text):
            raise ValueError("Invalid route preview.")
        self._app.clipboard(text)
        return True

    def open_editor(self, draft_id):
        self._trusted()
        if self._kind != "main":
            raise PermissionError("Only the main dashboard may open its editor.")
        if not isinstance(draft_id, str) or not DRAFT_ID.match(draft_id):
            raise ValueError("Invalid route ID.")
        return self._app.open_editor(draft_id)

    def test_sound(self):
        self._trusted()
        return self._app.sounds.test_sound() if self._app.sounds else False

    def panel_pause(self, value=None):
        self._trusted()
        if self._app.sounds is None:
            return {"paused": True, "session": None, "through": 0}
        return self._app.sounds.panel_pause(value)


class PanelApp:
    def __init__(self, env, webview_module, *, clipboard=write_clipboard, tray_factory=None, audio=None):
        self.webview = webview_module
        self.token = env.pop("DBV_DASHBOARD_TOKEN", None)
        self.exit_file = env.pop(EXIT_FILE_ENV, None)
        self.label = env.get("DBV_ENGINE_LABEL") or "wersja silnika niedostępna"
        self.launcher_id = env.get("DBV_LAUNCHER_ID")
        self.clipboard = clipboard
        self.windows = {}
        self.quitting = False
        self.editor_was_visible = False
        self.tray = None
        self.tray_factory = tray_factory
        self.audio = audio
        self.sounds = None
        self._ready_written = False

    # ── windows ───────────────────────────────────────────────────────
    def create_main(self):
        window = self.webview.create_window(f"DBVictory Bot — {self.label}", html=LOADING_HTML,
                                            js_api=PanelApi(self, "main"), width=1000, height=740,
                                            min_size=(720, 540), background_color="#111827")
        self.windows["main"] = window
        window.events.closing += self._closing_main
        window.events.loaded += lambda: self._loaded("main")
        return window

    def open_editor(self, draft_id):
        editor = self.windows.get("editor")
        if editor is not None:
            editor.restore()
            editor.show()
            return False
        editor = self.webview.create_window("DBVictory — Edytor trasy",
                                            f"{PANEL_URL}/route-editor?draft={draft_id}",
                                            js_api=PanelApi(self, "editor"), width=1200, height=850,
                                            min_size=(820, 600), background_color="#111827")
        self.windows["editor"] = editor
        editor.events.closed += lambda: self.windows.get("editor") is editor and self.windows.pop("editor", None)
        editor.events.loaded += lambda: self._loaded("editor")
        return True

    def _loaded(self, kind):
        window = self.windows.get(kind)
        if window is None:
            return
        url = window.get_current_url() or ""
        if url.startswith("data:") or url in ("about:blank", ""):
            return  # the loading screen
        if not is_panel_url(url):
            # Navigation away from the panel's origin is never kept.
            window.load_url(PANEL_URL if kind == "main" else f"{PANEL_URL}/route-editor")
            return
        if kind == "main":
            self._notify_launcher()

    def _notify_launcher(self):
        if (self._ready_written or not self.token or not self.exit_file
                or not re.fullmatch(r"[0-9a-f]{32}", self.launcher_id or "")):
            return
        self._ready_written = True
        marker = paths.INSTALL_ROOT / ".codex" / "launcher-events" / f"{self.launcher_id}.ready"
        try:
            with open(marker, "x"):
                pass
        except OSError:
            pass

    def _closing_main(self):
        """Hide instead of closing; the tray is how the panel closes. Keeps unsaved sketches."""
        if self.quitting:
            return True
        editor = self.windows.get("editor")
        self.editor_was_visible = editor is not None
        if editor is not None:
            editor.hide()
        self.windows["main"].hide()
        return False

    def show(self):
        main = self.windows.get("main")
        if main is None:
            return
        main.restore()
        main.show()
        editor = self.windows.get("editor")
        if self.editor_was_visible and editor is not None:
            editor.show()
        self.editor_was_visible = False

    def quit(self):
        self.quitting = True
        if self.sounds:
            self.sounds.stop()
        if self.audio:
            self.audio.stop()
        if self.tray:
            self.tray.close()
        for window in list(self.windows.values()):
            try:
                window.destroy()
            except Exception:
                pass

    def exit_bot(self):
        if not self.exit_file or self.quitting:
            return False
        try:
            with open(self.exit_file, "x"):
                pass
        except FileExistsError:
            pass
        except OSError:
            self.tray and self.tray.error("Zakończ bota", "Nie udało się przekazać żądania zamknięcia do silnika.")
            return False
        self.quit()
        return True

    # ── startup ───────────────────────────────────────────────────────
    def wait_for_panel(self, attempts=None):
        """Load the panel once its server answers, like Electron's tryLoad."""
        count = 0
        while not self.quitting and (attempts is None or count < attempts):
            count += 1
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(PANEL_URL, timeout=2) as reply:
                    if reply.status == 200:
                        self.windows["main"].load_url(PANEL_URL)
                        return True
            except OSError:
                pass
            time.sleep(0.5)
        return False

    def started(self):
        if self.tray_factory:
            self.tray = self.tray_factory(self)
        if self.audio is None:
            self.audio = NotificationAudio(fallback=_beep)
        tray = self.tray
        self.sounds = AlertSounds(
            self.token, self.audio.play,
            on_mute=lambda: (self.audio.interrupt(), tray and tray.remove_balloon()),
            on_hunt_ready=lambda: tray and tray.balloon("Nagrywanie adaptacyjne",
                                                        "Obieg potwierdzony. Możesz zakończyć nagranie.")).start()
        self.wait_for_panel()


def _beep():
    try:
        import winsound
        winsound.MessageBeep()
    except (ImportError, RuntimeError):
        pass


class Tray:
    """A Windows tray icon on its own STA WinForms message loop (pythonnet ships with pywebview)."""

    def __init__(self, app):
        import clr
        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Drawing")
        from System.Threading import ApartmentState, Thread, ThreadStart
        self.app = app
        self.icon = None
        self._ready = threading.Event()
        thread = Thread(ThreadStart(self._run))
        thread.SetApartmentState(ApartmentState.STA)
        thread.IsBackground = True
        thread.Start()
        self._ready.wait(10)

    def _run(self):
        from System.Drawing import Bitmap, Icon, SystemIcons
        from System.Windows.Forms import (Application, ContextMenuStrip, MouseButtons, NotifyIcon,
                                          ToolStripMenuItem, ToolStripSeparator)
        icon = NotifyIcon()
        picture = paths.DASHBOARD_DIR / "electron" / "tray.png"
        try:
            icon.Icon = Icon.FromHandle(Bitmap(str(picture)).GetHicon())
        except Exception:
            icon.Icon = SystemIcons.Application
        icon.Text = "DBVictory Bot — kliknij, aby pokazać panel"
        menu = ContextMenuStrip()
        show = ToolStripMenuItem("Pokaż panel")
        show.Click += lambda *_: self.app.show()
        close = ToolStripMenuItem("Zamknij panel")
        close.Click += lambda *_: self.app.quit()
        stop = ToolStripMenuItem("Zakończ bota")
        stop.Enabled = bool(self.app.exit_file)
        stop.Click += lambda *_: self.app.exit_bot()
        for item in (show, ToolStripSeparator(), close, stop):
            menu.Items.Add(item)
        icon.ContextMenuStrip = menu

        def clicked(_sender, event):
            if event.Button == MouseButtons.Left:
                self.app.show()
        icon.MouseClick += clicked
        icon.Visible = True
        self.icon = icon
        self._ready.set()
        Application.Run()

    def balloon(self, title, text):
        from System.Windows.Forms import ToolTipIcon
        if self.icon is not None:
            self.icon.ShowBalloonTip(5000, title, text, ToolTipIcon.Info)

    def remove_balloon(self):
        if self.icon is not None:
            self.icon.Visible = False
            self.icon.Visible = True

    def error(self, title, text):
        from System.Windows.Forms import MessageBox
        MessageBox.Show(text, title)

    def close(self):
        from System.Windows.Forms import Application
        icon, self.icon = self.icon, None
        if icon is not None:
            icon.Visible = False
            icon.Dispose()
            Application.ExitThread()


def _storage():
    base = os.environ.get("LOCALAPPDATA") or str(paths.INSTALL_ROOT / ".codex")
    return os.path.join(base, "DBVictory Bot", "panel-webview")


def main():
    import webview
    app = PanelApp(os.environ, webview, tray_factory=Tray if os.name == "nt" else None)
    app.create_main()
    # Not private: the panel keeps small preferences (alert bell) in local storage, as under Electron.
    webview.start(app.started, gui="edgechromium" if os.name == "nt" else None, private_mode=False,
                  storage_path=_storage())
    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
