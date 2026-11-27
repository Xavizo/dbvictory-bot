"""DBVictory Launcher: the start window, in a pywebview (WebView2) window.

Started by `DBVictory.exe` as `venv/Scripts/DBVictory.exe -m launcher.desktop_app [--check]`
with the installation as working directory. The window shows
launcher/desktop/index.html; this module is the logic behind its buttons:
check and install releases, find and open the game client, run the engine
session (`launcher.desktop_session`) and hide once its panel is ready.

All state lives on one asyncio loop in a background thread. The page reaches it
only through LauncherApi (state, act, mcp), and only from the launcher's own page.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
import threading
import time
from uuid import uuid4

from launcher import desktop_host as host_module

WAITING_FOR_LIST = ("Zaloguj się na konto i pozostaw otwartą listę postaci. Gdy będziesz gotowy, kliknij "
                    "„Połącz z grą”. Nie wchodź jeszcze na postać.")
ACTIVE_PHASES = ("running", "prepared", "connecting", "starting", "panel", "waiting")
TITLE = "DBVictory Launcher"
ICON = Path(__file__).resolve().parent / "desktop.ico"
APP_ID = "DBVictory.Launcher"


def use_app_icon(app_id):
    """Group our windows under our own taskbar button and icon instead of pythonw's."""
    if os.name == "nt":
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except (AttributeError, OSError):
            pass
    return str(ICON) if ICON.is_file() else None


def settings_file():
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "DBVictory Launcher" / "client.json"  # the Electron launcher's file: choices carry over


STAGES = {"download": "Pobieranie", "extract": "Rozpakowywanie", "apply": "Instalowanie"}


def _megabytes(n):
    return f"{n / 1e6:.1f}".replace(".", ",")


def _duration(seconds):
    seconds = max(1, round(seconds))
    return f"{seconds} s" if seconds < 60 else f"{seconds // 60} min {seconds % 60:02d} s"


def describe_progress(report, now):
    """The bar and label for github_release's progress.json, or None for an unreadable report.

    Downloads show megabytes, speed and an estimate; extracting and installing show files.
    """
    try:
        stage, done, total = report["stage"], int(report["done"]), int(report["total"])
        started = float(report.get("started") or now)
    except (KeyError, TypeError, ValueError):
        return None
    if stage not in STAGES or total <= 0:
        return None
    done = min(max(done, 0), total)
    elapsed, rate = max(now - started, 0.0), ""
    if stage == "download":
        what = "łatki" if report.get("kind") == "patch" else "pełnego wydania"
        label = f"Pobieranie {what}: {_megabytes(done)} z {_megabytes(total)} MB"
        if elapsed >= 1 and done:
            speed = done / elapsed
            rate = f"{_megabytes(speed)} MB/s"
            if done < total:
                rate += f" · ok. {_duration((total - done) / speed)}"
    else:
        label = f"{STAGES[stage]}: {done} z {total} plików"
    step = {"download": 1, "extract": 2, "apply": 3}[stage]
    return {"stage": stage, "step": step, "steps": 3, "percent": round(100 * done / total), "label": label,
            "rate": rate}


def read_progress(path, now=None):
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # missing, or caught mid-write
    return describe_progress(report, time.time() if now is None else now) if isinstance(report, dict) else None


# What a player opens or keeps in the installation folder; the release's other top-level
# entries are program internals.
SHOWN = {"DBVictory.exe", "README.md"}


def internals(root):
    """Top-level names of a release installation's program files, minus SHOWN.

    Read from the release's manifest, so nothing the player created is ever
    touched; a source checkout (no manifest) has none.
    """
    try:
        files = json.loads((Path(root) / "release-manifest.json").read_text(encoding="utf-8"))["files"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    names = {name.split("/", 1)[0] for name in files} | {"release-manifest.json", "release.json", "venv"}
    return sorted(n for n in names - SHOWN if n and n not in (".", "..") and "\\" not in n and ":" not in n)


def tidy(root, hide=None):
    """Hide the program internals in Explorer so the folder shows DBVictory.exe.

    Nothing moves: every path (updates, AI integrations, shortcuts) stays the
    same, and Explorer's "Hidden items" shows them again. Runs at every start,
    because an update writes files without the attribute.
    """
    if hide is None:
        if os.name != "nt":
            return []
        import ctypes
        kernel = ctypes.windll.kernel32

        def hide(path):
            attributes = kernel.GetFileAttributesW(str(path))
            if attributes != 0xFFFFFFFF and not attributes & 0x2:  # FILE_ATTRIBUTE_HIDDEN
                kernel.SetFileAttributesW(str(path), attributes | 0x2)
    hidden = []
    for name in internals(root):
        path = Path(root) / name
        if path.exists():
            try:
                hide(path)
                hidden.append(name)
            except OSError:
                pass
    return hidden


def pid_alive(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
    import ctypes
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return kernel.GetLastError() == 5  # access denied: it exists
    try:
        code = ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


class Launcher:
    """The launcher's state machine, ported from the Electron main process."""

    def __init__(self, root, host, window, *, read_only=False, settings=None, loop=None):
        self.root = Path(root)
        self.host = host
        self.window = window
        self.read_only = read_only
        self.settings = settings or settings_file()
        self.loop = loop or asyncio.get_event_loop()
        self.call_later = self.loop.call_later
        self.every = self._every
        self.pid_alive = pid_alive
        self.child = None
        self.old_pending = None
        self.marker = None
        self.client_pid = None
        self.poll = None
        self.deadline = None
        self.locked = False
        self.checking_panel = False
        self.quitting = False
        self.session_error = ""
        self.awaiting_start = False
        self.awaiting_connection = False
        self.recheck_on_close = False
        self.view = {"phase": "checking", "text": "Sprawdzanie lokalnej kolejki wydań…", "version": "",
                     "changes": [], "folder": "", "installed": False, "clientOpen": False, "canDefer": False,
                     "readOnly": read_only}

    # ── helpers ───────────────────────────────────────────────────────
    def _every(self, seconds, callback):
        async def repeat():
            while True:
                await asyncio.sleep(seconds)
                await callback()
        return self.loop.create_task(repeat())

    def set(self, phase, text, **extra):
        self.view = {**self.view, "phase": phase, "text": text, "canDefer": False, "canInstall": False,
                     "clientOutdated": False, "failedStep": None, "progress": None, **extra}
        self.window.send(self.view)

    def reveal(self):
        self.window.reveal()

    def stop_monitor(self):
        for handle in (self.poll, self.deadline):
            if handle is not None:
                handle.cancel()
        self.poll = self.deadline = None
        self.checking_panel = False
        if self.marker:
            try:
                self.marker.unlink(missing_ok=True)
            except OSError:
                pass
            self.marker = None

    async def client_info(self, folder):
        exe = self.host.client_exe(folder)
        window_pid = None
        if exe:
            try:
                processes = await self.host.clients()
            except Exception:
                processes = []
            for proc in processes:
                if proc.get("HasWindow") and proc.get("Path") and host_module._same_folder(proc["Path"], folder):
                    window_pid = proc.get("Id")
                    break
        return {"folder": folder, "installed": bool(exe), "clientOpen": bool(window_pid), "windowPid": window_pid}

    def show_start(self, info=None):
        info = info or {}
        open_ = info.get("clientOpen", self.view.get("clientOpen"))
        text = ("Kliknij Start, a następnie na liście postaci wybierz „Połącz z grą”." if open_
                else "Start otworzy klienta. Na liście postaci wybierz „Połącz z grą”.")
        self.set("ready", text, **info)

    # ── releases ──────────────────────────────────────────────────────
    async def check(self, defer=None, install=None):
        self.set("checking", "Sprawdzanie lokalnej kolejki wydań…")
        report = await self.host.check_release(self.root, ["preflight", "--check"])
        github = report.get("source") == "github"
        if (not self.read_only and defer and report["state"] == "available" and defer == report.get("pending")
                and self.host.valid_sha(defer)):
            self.set("applying", "Odkładanie wybranego wydania…")
            report = await self.host.check_release(self.root, ["defer", defer])
        elif (not self.read_only and install and github and report["state"] == "available"
              and install == report.get("pending") and self.host.valid_sha(install)):
            label = report.get("pending_label") or "nowej wersji"
            self.set("applying", f"Przygotowywanie aktualizacji do {label}…", version=report.get("installed_label"))
            watch = self.every(0.5, lambda: self._show_install_progress(label))
            try:
                report = await self.host.check_release(self.root, ["install", install])
            finally:
                watch.cancel()
            if report["state"] == "updated" and report.get("restart"):
                self.set("applying", "Zainstalowano aktualizację. Ponowne uruchamianie launchera…")
                await asyncio.sleep(0.9)
                # The new launcher must load the new code; start it through the EXE after this process exits.
                self.quitting = True
                exe = self.root / "DBVictory.exe"
                self.window.relaunch(exe if exe.is_file() else self.root / "DBVictory Launcher.exe")
                return
        elif not self.read_only and github and report["state"] == "recovery":
            self.set("applying", report["message"], version=report.get("installed_label"))
            report = await self.host.check_release(self.root, ["preflight"])
        elif not self.read_only and not github and report["state"] in ("available", "recovery"):
            self.set("applying", report["message"], version=report.get("installed_label"))
            report = await self.host.check_release(self.root, ["preflight"])
        self.old_pending = report.get("pending")
        changes, notes_error = [], False
        try:
            changes = self.host.history(self.root, report.get("installed_label"))
        except Exception:
            notes_error = True
        extra = {"revision": report.get("installed"), "version": report.get("installed_label") or "",
                 "changes": changes, "notesError": notes_error,
                 "canDefer": bool(report.get("can_defer") and self.old_pending),
                 "canInstall": (github and report["state"] == "available" and bool(self.old_pending)
                                and not self.read_only),
                 "pendingLabel": report.get("pending_label") or ""}
        if report["state"] in ("updated", "current"):
            if self.read_only:
                self.set("ready", "Sprawdzenie zakończone.", **extra)
            else:
                self.set("preparing", "Przygotowywanie launchera…", **extra)
                await self.prepare_prompt()
        else:
            queued = (" W kolejce: " + report["pending_label"] + "."
                      if report["state"] == "busy" and report.get("pending_label") else "")
            state = report["state"] if report["state"] in ("busy", "available", "recovery") else "problem"
            self.set(state, report["message"] + queued, **extra)

    async def _show_install_progress(self, label):
        progress = read_progress(self.root / ".codex" / "updates" / "github" / "progress.json")
        if progress and self.view["phase"] == "applying" and progress != self.view.get("progress"):
            self.set("applying", f"Aktualizacja do {label}", progress=progress)

    # ── engine session ────────────────────────────────────────────────
    def start_deadline(self, proc):
        if self.deadline is not None:
            self.deadline.cancel()

        def expire():
            if self.child is proc and self.view["phase"] == "connecting":
                self.set("waiting", "Silnik nie potwierdził połączenia z klientem.")
                return
            if self.child is proc and self.view["phase"] in ("starting", "panel"):
                self.set("waiting", "Główne okno nie potwierdziło gotowości." if self.checking_panel
                         else "Silnik nie potwierdził gotowości.",
                         failedStep="panel" if self.checking_panel else "engine")
        self.deadline = self.call_later(75, expire)

    def watch_session(self, proc):
        self.start_deadline(proc)
        probing = [False]

        async def probe():
            if probing[0] or self.child is not proc:
                return
            probing[0] = True
            try:
                if self.client_pid and not self.pid_alive(self.client_pid):
                    # A client update can replace its process. Follow only this installation.
                    processes = await self.host.clients()
                    if self.child is not proc:
                        return
                    replacement = next((p for p in processes if p.get("Path")
                                        and host_module._same_folder(p["Path"], self.view["folder"])), None)
                    if replacement:
                        self.client_pid = replacement["Id"]
                    else:
                        self.client_pid = None
                        proc.end()
                        return
                if self.checking_panel and self.marker:
                    if not self.marker.exists() or self.child is not proc:
                        return
                    self.checking_panel = False
                    if self.deadline is not None:
                        self.deadline.cancel()
                    self.set("running", "Panel gotowy. Stan zalogowania i odczyty postaci zobaczysz w głównym oknie.")
                    self.window.hide()
            except Exception:
                pass  # A temporarily unavailable process listing does not end the session.
            finally:
                probing[0] = False
        self.poll = self.every(0.6, probe)
        self.probe = probe

    def on_line(self, proc, line):
        if self.child is not proc:
            return
        if "Naciśnij Enter, gdy postać jest wylogowana" in line:
            self.awaiting_start = True
            if self.deadline is not None:
                self.deadline.cancel()
            self.show_start()
        elif line == "OCZEKIWANIE NA POŁĄCZENIE" and self.awaiting_connection:
            if self.deadline is not None:
                self.deadline.cancel()
            self.set("prepared", WAITING_FOR_LIST)
        elif line.startswith("GOTOWE"):
            self.checking_panel = True
            self.set("panel", "Silnik gotowy. Oczekiwanie na główne okno…")
        elif line.startswith("BŁĄD:") or line.startswith("Błąd launchera:"):
            self.session_error = line[:440]

    async def prepare_prompt(self):
        if self.child or self.read_only:
            return
        launcher_id = uuid4().hex
        events = self.root / ".codex" / "launcher-events"
        events.mkdir(parents=True, exist_ok=True)
        self.marker = events / f"{launcher_id}.ready"
        self.session_error = ""
        holder = {}
        proc = await self.host.interactive(self.root, launcher_id, self.view.get("revision"),
                                           lambda line: self.on_line(holder.get("proc"), line))
        holder["proc"] = proc
        self.child = proc
        self.loop.create_task(self._closed(proc))

        def no_prompt():
            if self.child is proc and not self.awaiting_start:
                self.session_error = "Proces przygotowania nie zgłosił gotowości."
                proc.end()
                self.set("problem", self.session_error)
        self.deadline = self.call_later(30, no_prompt)

    async def _closed(self, proc):
        try:
            code = await proc.wait()
        except Exception:
            self.session_error = "Nie udało się otworzyć procesu sesji."
            code = 1
        self.session_closed(proc, code)

    def session_closed(self, proc, code):
        if self.child is not proc:
            return
        was_running = self.view["phase"] in ("running", "prepared")
        failed_step = "panel" if self.checking_panel else "engine" if self.client_pid else None
        self.stop_monitor()
        self.child = None
        self.client_pid = None
        self.awaiting_start = self.awaiting_connection = False
        if self.quitting or (was_running and code == 0):
            self.window.quit()
            return
        if self.recheck_on_close:
            self.recheck_on_close = False
            self.loop.create_task(self.action("retry"))
            return
        self.reveal()
        if code == 75:
            self.set("problem", "Pojawiło się nowsze wydanie. Sprawdź aktualizację ponownie.")
        elif code or self.session_error:
            self.set("problem", self.session_error or "Proces sesji zakończył się błędem.", failedStep=failed_step)
        else:
            self.set("closed", "Sesja została zamknięta.")

    async def find_or_launch_client(self, proc):
        folder = self.view["folder"]

        def current():
            return not self.quitting and proc is self.child and self.awaiting_start
        self.set("detecting", "Wykrywanie klienta DBVictory…")
        existing = await self.host.open_window_pid(folder)
        if not current():
            return None
        if existing:
            return existing
        self.set("detecting", "Uruchamianie klienta DBVictory…", clientOpen=False, windowPid=None)
        await self.host.open_client(folder)
        if not current():
            return None
        self.set("detecting", "Oczekiwanie na okno klienta DBVictory…")
        until = time.monotonic() + 60
        while current():
            pid = await self.host.open_window_pid(folder)
            if not current():
                return None
            if pid:
                return pid
            remaining = until - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("Okno DBVictory nie pojawiło się w ciągu minuty. "
                                   "Po aktualizacji klienta możesz ponowić próbę.")
            await asyncio.sleep(min(0.75, remaining))
        return None

    # ── buttons ───────────────────────────────────────────────────────
    async def action(self, name):
        if self.locked:
            return
        self.locked = True
        phase = self.view["phase"]
        try:
            if name == "retry" and not self.child:
                await self.check()
            elif name == "defer" and not self.child and not self.read_only and self.view.get("canDefer"):
                await self.check(defer=self.old_pending)
            elif name == "install" and not self.child and not self.read_only and self.view.get("canInstall"):
                await self.check(install=self.old_pending)
            elif (name == "recheck" and self.child and self.awaiting_start and phase in ("ready", "clientError")
                  and not self.read_only):
                # Close the idle session prompt; its close handler checks for a new release.
                self.recheck_on_close = True
                self.set("closing", "Sprawdzanie aktualizacji…")
                self.child.end()
            elif name == "start" and self.awaiting_start and phase in ("ready", "clientError") and not self.read_only:
                await self._start()
            elif name == "connect" and self.awaiting_connection and phase == "prepared" and not self.read_only:
                await self._connect()
            elif name == "cancel" and self.child and phase == "waiting":
                self.set("closing", "Zamykanie uruchamianej sesji…")
                self.child.end()
            elif name == "folder" and self.awaiting_start and phase in ("ready", "clientError") and not self.read_only:
                await self._choose_folder()
        except Exception as error:
            if self.awaiting_start:
                self.set("clientError", str(error), failedStep="client")
            else:
                self.set("problem", str(error))
        finally:
            self.locked = False

    async def _start(self):
        proc = self.child
        try:
            # A game client update needs a matching bot release: say so before touching the client.
            support = await self.host.client_support(self.root, self.view["folder"])
            if self.quitting or proc is not self.child or not self.awaiting_start:
                return
            if not support.get("supported"):
                self.set("clientError", support.get("message", ""), failedStep="client",
                         clientOutdated=not support.get("missing"))
                return
            pid = await self.find_or_launch_client(proc)
            if not pid or self.quitting or proc is not self.child or not self.awaiting_start:
                return
            self.client_pid = pid
            self.awaiting_connection = True
        except Exception as error:
            if not self.quitting and proc is self.child:
                self.set("clientError", str(error), failedStep="client")
            return
        self.awaiting_start = False
        self.set("starting", "Uruchamianie silnika…", clientOpen=True, windowPid=self.client_pid)
        self.watch_session(proc)
        proc.write("prepare-login\n")

    async def _connect(self):
        proc = self.child
        confirmed = self.client_pid
        try:
            # The click confirms the character list. Check only the selected process,
            # never capture the window or infer login state from its contents.
            pid = await self.host.open_window_pid(self.view["folder"])
            if self.quitting or proc is not self.child or not self.awaiting_connection or self.view["phase"] != "prepared":
                return
            if not pid:
                raise RuntimeError("Nie znaleziono otwartego okna klienta. Otwórz klienta z wybranego folderu i "
                                   "ponownie kliknij „Połącz z grą” na liście postaci.")
            self.client_pid = pid
            if pid != confirmed:
                self.set("prepared", "Klient został ponownie uruchomiony. Na jego liście postaci kliknij jeszcze "
                         "raz „Połącz z grą”.", windowPid=pid)
                return
        except Exception as error:
            if not self.quitting and proc is self.child:
                self.set("prepared", str(error))
            return
        self.awaiting_connection = False
        self.set("connecting", "Łączenie z klientem DBVictory…")
        self.start_deadline(proc)
        proc.write("connect\n")

    async def _choose_folder(self):
        folder = await self.loop.run_in_executor(None, self.window.pick_folder)
        if not folder:
            return
        info = await self.client_info(folder)
        if not info["installed"]:
            self.set("ready", "W tym katalogu brakuje dbvStart.exe lub dbvStart_gl.exe.")
            return
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        temp = self.settings.with_name(self.settings.name + ".tmp")
        temp.write_text(json.dumps({"folder": info["folder"]}), encoding="utf-8")
        os.replace(temp, self.settings)
        self.show_start(info)

    def on_close(self):
        """True when the window may close now."""
        if self.quitting:
            return True
        if (self.locked and self.view["phase"] != "detecting") or self.view["phase"] in ("checking", "applying"):
            return False
        if self.child and self.view["phase"] in ACTIVE_PHASES:
            self.window.hide()
            return False
        if self.child:
            self.quitting = True
            self.child.end()
            return False
        return True

    async def startup(self):
        try:
            folder = None
            try:
                folder = json.loads(self.settings.read_text(encoding="utf-8")).get("folder")
            except (OSError, ValueError, AttributeError):
                pass
            if not folder:
                try:
                    processes = await self.host.clients()
                except Exception:
                    processes = []
                if processes and processes[0].get("Path"):
                    folder = os.path.dirname(processes[0]["Path"])
                else:
                    folder = await self.loop.run_in_executor(None, self.host.find_client_folder)
            self.view = {**self.view, **await self.client_info(folder or "")}
            await self.action("retry")
        except Exception as error:
            self.set("problem", str(error))


def is_page_url(url, page):
    """Whether a WebView2 URL (file:///D:/Gry/DBV%20Bot/launcher/desktop/index.html) shows `page`."""
    from urllib.parse import unquote, urlsplit
    if not isinstance(url, str):
        return False
    parts = urlsplit(url)
    if parts.scheme != "file" or parts.netloc not in ("", "localhost") or parts.query:
        return False
    path = unquote(parts.path)
    if len(path) > 2 and path[0] == "/" and path[2] == ":":
        path = path[1:]  # /D:/… is D:/… on Windows
    return os.path.normcase(os.path.normpath(path)) == os.path.normcase(os.path.normpath(str(page)))


class LauncherApi:
    """What index.html may call, as window.pywebview.api (see desktop/bridge.js)."""

    def __init__(self, launcher, page):
        self._launcher = launcher
        self._page = page

    def _trusted(self):
        if not self._launcher.window.current_url_is(self._page):
            raise PermissionError("Nieznane okno.")

    def _run(self, coroutine, timeout=None):
        return asyncio.run_coroutine_threadsafe(coroutine, self._launcher.loop).result(timeout)

    def state(self):
        self._trusted()

        async def snapshot():
            return self._launcher.view
        return self._run(snapshot(), 10)

    def act(self, name):
        self._trusted()
        if not isinstance(name, str):
            raise ValueError("Nieznana akcja.")
        self._run(self._launcher.action(name))
        return None

    def mcp(self, operation, target=None):
        self._trusted()
        if self._launcher.read_only and operation != "status":
            raise PermissionError("Tryb tylko do odczytu.")
        return self._run(self._launcher.host.mcp_clients(self._launcher.root, operation, target), 120)


class WebviewWindow:
    """The pywebview window behind Launcher.window."""

    def __init__(self):
        self.window = None
        self.on_quit = None

    def send(self, view):
        if self.window is not None:
            self.window.run_js("window.__dbvDesktopChanged && window.__dbvDesktopChanged(%s)"
                               % json.dumps(view, ensure_ascii=False))

    def reveal(self):
        if self.window is not None:
            self.window.restore()
            self.window.show()

    def hide(self):
        # Never wait here: the closing handler holds the UI thread, and hide() marshals onto it.
        if self.window is not None:
            threading.Thread(target=self.window.hide, daemon=True).start()

    def current_url_is(self, page):
        return self.window is not None and is_page_url(self.window.get_current_url(), page)

    def pick_folder(self):
        import webview
        dialog = getattr(webview, "FileDialog", None)
        kind = dialog.FOLDER if dialog else webview.FOLDER_DIALOG
        result = self.window.create_file_dialog(kind)
        return result[0] if result else None

    def relaunch(self, exe):
        import subprocess
        _release_single_instance()  # the new launcher must not find this one still running
        if Path(exe).is_file():
            subprocess.Popen([str(exe)], cwd=str(Path(exe).parent),
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        self.quit()

    def quit(self):
        # Called on the launcher loop. destroy() waits for the UI thread, which runs the closing
        # handler, which waits for this loop: run it aside, or the close times out and is cancelled
        # (an updated launcher then left the old window open next to the new one).
        if self.window is not None:
            threading.Thread(target=self.window.destroy, daemon=True).start()


def _single_instance():
    """False when another launcher runs; that one is brought to the front."""
    if os.name != "nt":
        return True
    import ctypes
    kernel, user = ctypes.windll.kernel32, ctypes.windll.user32
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    _single_instance.mutex = kernel.CreateMutexW(None, False, "Local\\DBVictoryLauncher")
    if kernel.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
        return True
    hwnd = user.FindWindowW(None, TITLE)
    if hwnd:
        user.ShowWindow(hwnd, 9)  # SW_RESTORE
        user.SetForegroundWindow(hwnd)
    return False


def _release_single_instance():
    mutex = getattr(_single_instance, "mutex", None)
    if mutex and os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.CloseHandle(mutex)
    _single_instance.mutex = None


def _log_to_file(root):
    """pythonw has no console: keep start-up failures in .codex/launcher.log."""
    import logging
    try:
        folder = Path(root) / ".codex"
        folder.mkdir(exist_ok=True)
        log_file = folder / "launcher.log"
        if log_file.exists() and log_file.stat().st_size > 1_000_000:
            log_file.unlink()
        logging.basicConfig(filename=str(log_file), level=logging.INFO, encoding="utf-8",
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    except OSError:
        pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    _log_to_file(Path.cwd())
    read_only = "--check" in argv
    if not _single_instance():
        return 0
    try:
        tidy(Path.cwd())
    except Exception:
        pass  # cosmetic; never keep the launcher from starting
    import webview
    root = Path.cwd()  # the EXE starts us with the installation as working directory
    page = host_module.DESKTOP_DIR / "index.html"
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, name="launcher-loop", daemon=True).start()
    frame = WebviewWindow()
    launcher = Launcher(root, host_module, frame, read_only=read_only, loop=loop)
    started = []
    api = LauncherApi(launcher, page)
    window = webview.create_window(TITLE, str(page), js_api=api, width=1060, height=670, resizable=False, text_select=True,
                                   background_color="#0b1222", hidden=True)
    frame.window = window

    def closing():
        if launcher.quitting:
            return True  # decided already; never wait for a loop that may be closing this window
        try:
            return asyncio.run_coroutine_threadsafe(_on_close(launcher), loop).result(5)
        except Exception:
            return False  # undecided: keep the window and the session it supervises

    def loaded():
        if not is_page_url(window.get_current_url(), page):
            window.load_url(page.as_uri())  # never stay on another page
        elif not started:
            started.append(True)
            window.show()
            asyncio.run_coroutine_threadsafe(launcher.startup(), loop)

    window.events.closing += closing
    window.events.loaded += loaded
    webview.start(gui="edgechromium" if os.name == "nt" else None, private_mode=True, icon=use_app_icon(APP_ID))
    loop.call_soon_threadsafe(loop.stop)
    return 0


async def _on_close(launcher):
    return launcher.on_close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import logging
        logging.getLogger("launcher").exception("The launcher stopped")
        raise
