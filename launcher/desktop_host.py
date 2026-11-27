"""What the desktop launcher asks of Windows and of the local Python tools.

Every operation that touches the installation runs in a separate process
(`launcher_files.py`, `mcp_clients.py`, `launcher.desktop_session`), as the
Electron launcher did: an update replaces the files of the code it runs.
"""
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess

CLIENT_NAMES = ("dbvStart.exe", "dbvStart_gl.exe")
MCP_TARGETS = ("codex", "claude-desktop", "claude-code")
REPORT_STATES = ("current", "updated", "available", "recovery", "busy", "error")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DESKTOP_DIR = Path(__file__).resolve().parent / "desktop"


class MultipleClients(RuntimeError):
    """Two client windows from the selected folder are open."""


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{40}", value) is not None


def python_exe(root):
    """The engine's Python: python.exe with the bot's name and icon when the release ships it."""
    scripts = Path(root) / "venv" / "Scripts"
    branded = scripts / "DBVictory Engine.exe"
    return branded if branded.is_file() else scripts / "python.exe"


async def _run(command, cwd, timeout=None):
    """(returncode, stdout) of a hidden child process."""
    proc = await asyncio.create_subprocess_exec(
        *map(str, command), cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode, out.decode("utf-8", "replace")


async def check_release(root, args):
    try:
        code, output = await _run([python_exe(root), "-B", "-X", "utf8", "launcher_files.py", *args], root)
    except OSError as error:
        raise RuntimeError("Nie udało się wykonać lokalnego aktualizatora. Sprawdź instalację Pythona.") from error
    try:
        report = json.loads(output)
    except ValueError as error:
        if not output:
            raise RuntimeError("Nie udało się wykonać lokalnego aktualizatora. Sprawdź instalację Pythona.") from error
        raise RuntimeError("Niepoprawna odpowiedź aktualizatora.") from error
    if code and (not isinstance(report, dict) or report.get("state") != "error"):
        raise RuntimeError("Aktualizator zakończył się błędem.")
    if (not isinstance(report, dict) or report.get("state") not in REPORT_STATES
            or (report.get("installed") is not None and not valid_sha(report["installed"]))
            or (report.get("pending") is not None and not valid_sha(report["pending"]))
            or not isinstance(report.get("message"), str)):
        raise RuntimeError("Niepoprawna odpowiedź aktualizatora.")
    return report


def history(root, label):
    """Release notes up to the installed version, newest first."""
    match = re.match(r"^v(\d+)\.(\d+)\.(\d+)(?:-test\.[0-9a-f]{7})?(?:\s|$)", label or "")
    if not match:
        return []
    installed = tuple(map(int, match.groups()))
    file = Path(root) / "launcher" / "desktop" / "release-notes.json"
    if not file.is_file():
        file = DESKTOP_DIR / "release-notes.json"
    notes = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(notes, list):
        raise ValueError("Niepoprawny format historii wydań.")
    kept = []
    for release in notes:
        if (not isinstance(release, dict) or not isinstance(release.get("version"), str)
                or not re.fullmatch(r"v\d+\.\d+\.\d+", release["version"])
                or not isinstance(release.get("changes"), list)
                or not all(isinstance(item, dict) and isinstance(item.get("title"), str)
                           and isinstance(item.get("detail"), str) for item in release["changes"])):
            raise ValueError("Niepoprawny wpis historii wydań.")
        if tuple(map(int, release["version"][1:].split("."))) <= installed:
            kept.append(release)
    return kept


def client_exe(folder):
    if not isinstance(folder, str) or not re.match(r"^[a-z]:[\\/]", folder, re.I):
        return None
    for name in CLIENT_NAMES:
        exe = Path(folder) / name
        if exe.is_file():
            return exe
    return None


def _same_folder(exe_path, folder):
    return os.path.normcase(os.path.dirname(exe_path)) == os.path.normcase(os.path.abspath(folder))


async def clients():
    """Running client processes: [{Id, Path, WindowHandle, HasWindow}]."""
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    query = ("[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); ConvertTo-Json -Compress -InputObject "
             "@(Get-Process -Name dbvStart,dbvStart_gl -ErrorAction SilentlyContinue | Select-Object Id,Path,"
             "@{Name='WindowHandle';Expression={$_.MainWindowHandle.ToInt64()}},"
             "@{Name='HasWindow';Expression={$_.MainWindowHandle -ne [IntPtr]::Zero}})")
    try:
        _, output = await _run([powershell, "-NoProfile", "-NonInteractive", "-Command", query], Path.cwd(), 12)
        found = json.loads(output)
    except (OSError, ValueError, asyncio.TimeoutError) as error:
        raise RuntimeError("Nie udało się sprawdzić okna klienta w Windows.") from error
    return found if isinstance(found, list) else []


def window_pid(processes, folder):
    matches = [p for p in processes if p.get("HasWindow") and p.get("Path") and _same_folder(p["Path"], folder)]
    if len(matches) > 1:
        raise MultipleClients("Otwarte są co najmniej dwa okna klienta z tego folderu. Pozostaw jedno.")
    return matches[0]["Id"] if matches else None


async def open_window_pid(folder):
    return window_pid(await clients(), folder)


async def open_client(folder):
    exe = client_exe(folder)
    if not exe:
        raise RuntimeError("Wybierz katalog z dbvStart.exe lub dbvStart_gl.exe.")
    found = await clients()
    same = next((p for p in found if p.get("Path") and _same_folder(p["Path"], folder)), None)
    if same:
        return same["Id"]
    if found:
        raise RuntimeError("Klient działa z innej lokalizacji. Wybierz jego folder.")
    try:
        child = subprocess.Popen([str(exe)], cwd=folder, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    except OSError as error:
        raise RuntimeError("Windows nie uruchomił klienta DBVictory. Sprawdź wybrany folder.") from error
    return child.pid


async def run_json(root, script, args, timeout=90):
    try:
        _, output = await _run([python_exe(root), "-B", "-X", "utf8", script, *args], root, timeout)
    except (OSError, asyncio.TimeoutError) as error:
        raise RuntimeError("Nie udało się uruchomić lokalnego Pythona.") from error
    if not output:
        raise RuntimeError("Nie udało się uruchomić lokalnego Pythona.")
    result = json.loads(output)
    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(result["error"])
    return result


async def mcp_clients(root, operation, target=None):
    if operation == "status":
        return await run_json(root, "mcp_clients.py", ["status"], 30)
    if operation not in ("connect", "disconnect") or target not in MCP_TARGETS:
        raise RuntimeError("Nieznana integracja.")
    return await run_json(root, "mcp_clients.py", [operation, target])


async def set_channel(root, channel):
    if channel not in ("stable", "test"):
        raise RuntimeError("Nieznany kanał aktualizacji.")
    try:
        return await run_json(root, "launcher_files.py", ["channel", channel], 30)
    except ValueError as error:
        raise RuntimeError("Nie udało się zapisać kanału aktualizacji.") from error


async def client_support(root, folder):
    exe = client_exe(folder)
    if not exe:
        return {"supported": False, "missing": True,
                "message": "Wybierz katalog klienta DBVictory (z plikiem dbvStart.exe lub dbvStart_gl.exe) "
                           "przyciskiem „Zmień”."}
    return await run_json(root, "launcher_files.py", ["client-check", str(exe)], 30)


def find_client_folder(env=None):
    """No fixed default: look for a client folder in a few usual places."""
    env = os.environ if env is None else env
    home = env.get("USERPROFILE", "")
    roots = ["C:\\", "D:\\", "E:\\", "C:\\Gry", "D:\\Gry", "E:\\Gry", "C:\\Games", "D:\\Games",
             env.get("ProgramFiles(x86)"), env.get("ProgramFiles"),
             home and os.path.join(home, "Desktop"), home and os.path.join(home, "Downloads"),
             home and os.path.join(home, "Documents"), home and os.path.join(home, "Games")]
    for base in filter(None, roots):
        try:
            entries = list(os.scandir(base))
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_dir() or not re.search(r"dbv|victory", entry.name, re.I):
                    continue
            except OSError:
                continue
            folder = os.path.join(base, entry.name)
            if client_exe(folder):
                return folder
    return ""


class Session:
    """The interactive engine process started by Start: lines out, commands in."""

    def __init__(self, proc, reader_tasks):
        self.proc = proc
        self._readers = reader_tasks

    @property
    def pid(self):
        return self.proc.pid

    def write(self, line):
        try:
            self.proc.stdin.write(line.encode("utf-8"))
        except (OSError, RuntimeError, AttributeError):
            pass  # The child may exit just before a user submits input.

    def end(self):
        try:
            self.proc.stdin.close()
        except (OSError, RuntimeError, AttributeError):
            pass

    async def wait(self):
        code = await self.proc.wait()
        await asyncio.gather(*self._readers, return_exceptions=True)
        return code


async def interactive(root, launcher_id, revision, on_line):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "DBV_LAUNCHER_ID": launcher_id,
           "DBV_LAUNCHER_REVISION": revision or ""}
    proc = await asyncio.create_subprocess_exec(
        str(python_exe(root)), "-B", "-u", "-X", "utf8", "-m", "launcher.desktop_session", cwd=str(root), env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)

    async def pump(stream):
        while True:
            raw = await stream.readline()
            if not raw:
                return
            on_line(raw.decode("utf-8", "replace").rstrip("\r\n"))
    readers = [asyncio.ensure_future(pump(proc.stdout)), asyncio.ensure_future(pump(proc.stderr))]
    return Session(proc, readers)
