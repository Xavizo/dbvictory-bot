"""The launcher's Start/Connect flow (launcher/desktop_app.py) with no real client,
processes, user settings, windows or timers. Ported from the Electron tests."""
import asyncio
import json
import os
from pathlib import Path

import pytest

from launcher import desktop_app, desktop_host

FOLDER = os.path.abspath("DBVictory")


def test_a_clean_installation_gets_readable_notes_up_to_its_installed_version(tmp_path):
    bundled = [r["version"] for r in desktop_host.history(tmp_path, "v1.2.0 (example)")]
    assert bundled[0] == "v1.2.0" and all(v <= "v1.2.0" for v in bundled)
    directory = tmp_path / "launcher" / "desktop"
    directory.mkdir(parents=True)
    file = directory / "release-notes.json"
    notes = [{"version": v, "changes": [{"title": "Zmiana", "detail": "Opis"}]} for v in ("v1.10.0", "v1.9.0", "v1.8.9")]
    file.write_text(json.dumps(notes), encoding="utf-8")
    assert desktop_host.history(tmp_path, "v1.9.0 (abcdef0)") == notes[1:]
    file.write_text("invalid", encoding="utf-8")
    with pytest.raises(ValueError):
        desktop_host.history(tmp_path, "v1.9.0")


def test_client_detection_requires_a_window_in_the_selected_installation():
    proc = {"Id": 4, "Path": os.path.join(FOLDER, "dbvStart.exe"), "HasWindow": True}
    other = {**proc, "Id": 7, "Path": os.path.join(FOLDER, "other", "dbvStart_gl.exe")}
    assert desktop_host.window_pid([other, {**proc, "HasWindow": False}], FOLDER) is None
    assert desktop_host.window_pid([other, proc], FOLDER) == 4
    with pytest.raises(desktop_host.MultipleClients):
        desktop_host.window_pid([proc, {**proc, "Id": 8}], FOLDER)


def test_only_the_launcher_page_is_trusted(tmp_path):
    page = tmp_path / "launcher dir" / "index.html"
    assert desktop_app.is_page_url(page.as_uri(), page)
    assert not desktop_app.is_page_url(page.as_uri() + "?x=1", page)
    assert not desktop_app.is_page_url("http://127.0.0.1:4747/index.html", page)
    assert not desktop_app.is_page_url((tmp_path / "other.html").as_uri(), page)
    assert not desktop_app.is_page_url(None, page)


class FakeSession:
    def __init__(self):
        self.writes, self.ended = [], False
        self.closed = asyncio.get_running_loop().create_future()
        self.pid = 99

    def write(self, line):
        self.writes.append(line)

    def end(self):
        self.ended = True

    async def wait(self):
        return await self.closed


class FakeHost:
    valid_sha = staticmethod(desktop_host.valid_sha)

    def __init__(self, pid):
        self.pid, self.opens, self.support, self.on_open = pid, 0, None, None
        self.session = None

    def client_exe(self, folder):
        return Path(folder) / "dbvStart.exe"

    async def clients(self):
        return [{"Id": self.pid, "Path": os.path.join(FOLDER, "dbvStart.exe"), "HasWindow": True}] if self.pid else []

    async def open_window_pid(self, folder):
        return self.pid

    async def open_client(self, folder):
        self.opens += 1
        if self.on_open:
            self.on_open()
        self.pid = 20

    async def check_release(self, root, args):
        return {"state": "current", "message": "", "installed": None}

    def history(self, root, label):
        return []

    async def client_support(self, root, folder):
        return self.support or {"supported": True}

    async def interactive(self, root, launcher_id, revision, on_line):
        self.session = FakeSession()
        self.on_line = on_line
        return self.session


class FakeWindow:
    def __init__(self):
        self.views, self.hidden, self.quit_called = [], False, False

    def send(self, view):
        self.views.append(view)

    def reveal(self):
        pass

    def hide(self):
        self.hidden = True

    def quit(self):
        self.quit_called = True


class Handle:
    def cancel(self):
        pass


async def launcher_with_client(tmp_path, pid):
    settings = tmp_path / "client.json"
    settings.write_text(json.dumps({"folder": FOLDER}), encoding="utf-8")
    host, window = FakeHost(pid), FakeWindow()
    launcher = desktop_app.Launcher(tmp_path, host, window, settings=settings, loop=asyncio.get_running_loop())
    launcher.call_later = lambda seconds, callback: Handle()
    polls = []
    launcher.every = lambda seconds, callback: polls.append(callback) or Handle()
    launcher.pid_alive = lambda pid: True
    await launcher.startup()
    host.on_line("Naciśnij Enter, gdy postać jest wylogowana")
    launcher.poll_now = lambda: polls[-1]()
    return launcher, host, host.session, window


def run(test):
    return lambda tmp_path: asyncio.run(test(tmp_path))


@run
async def test_cold_start_keeps_the_panel_closed_until_connect(tmp_path):
    launcher, host, session, window = await launcher_with_client(tmp_path, None)
    await asyncio.gather(launcher.action("start"), launcher.action("start"))
    assert host.opens == 1
    assert launcher.view["phase"] == "starting"
    assert session.writes == ["prepare-login\n"]
    host.on_line("OCZEKIWANIE NA POŁĄCZENIE")
    await launcher.poll_now()
    assert launcher.view["phase"] == "prepared"
    instruction = launcher.view["text"]
    await launcher.poll_now()
    assert launcher.view["text"] == instruction
    await asyncio.gather(launcher.action("connect"), launcher.action("connect"))
    assert session.writes == ["prepare-login\n", "connect\n"]
    assert not window.hidden
    host.on_line("GOTOWE — możesz zalogować postać.")
    launcher.marker.write_text("")  # the panel window reports itself ready
    await launcher.poll_now()
    assert launcher.view["phase"] == "running"
    assert window.hidden


@run
async def test_an_already_open_client_still_waits_for_the_users_connect_confirmation(tmp_path):
    launcher, host, session, _ = await launcher_with_client(tmp_path, 17)
    await launcher.action("start")
    assert host.opens == 0
    assert launcher.view["phase"] == "starting"
    assert session.writes == ["prepare-login\n"]
    host.on_line("OCZEKIWANIE NA POŁĄCZENIE")
    await launcher.action("connect")
    assert session.writes == ["prepare-login\n", "connect\n"]


@run
async def test_a_replacement_client_must_be_confirmed_again_on_the_character_list(tmp_path):
    launcher, host, session, _ = await launcher_with_client(tmp_path, None)
    await launcher.action("start")
    host.on_line("OCZEKIWANIE NA POŁĄCZENIE")
    await launcher.poll_now()
    host.pid = 42
    await launcher.action("connect")
    assert launcher.view["phase"] == "prepared"
    assert session.writes == ["prepare-login\n"]
    await launcher.action("connect")
    assert host.opens == 1
    assert session.writes == ["prepare-login\n", "connect\n"]


@run
async def test_connect_reports_a_missing_window_and_allows_another_manual_attempt(tmp_path):
    launcher, host, session, _ = await launcher_with_client(tmp_path, 17)
    await launcher.action("start")
    host.on_line("OCZEKIWANIE NA POŁĄCZENIE")
    host.pid = None
    await launcher.action("connect")
    assert launcher.view["phase"] == "prepared"
    assert "okn" in launcher.view["text"]
    assert session.writes == ["prepare-login\n"]
    host.pid = 17
    await launcher.action("connect")
    assert session.writes == ["prepare-login\n", "connect\n"]


@run
async def test_connect_preserves_an_actionable_process_error(tmp_path):
    launcher, host, session, _ = await launcher_with_client(tmp_path, None)
    await launcher.action("start")
    host.on_line("OCZEKIWANIE NA POŁĄCZENIE")
    original = host.open_window_pid

    async def failing(folder):
        raise RuntimeError("Pozostaw jedno okno klienta.")
    host.open_window_pid = failing
    await launcher.action("connect")
    assert launcher.view["phase"] == "prepared"
    assert launcher.view["text"] == "Pozostaw jedno okno klienta."
    assert session.writes == ["prepare-login\n"]
    host.open_window_pid = original
    await launcher.action("connect")
    assert session.writes == ["prepare-login\n", "connect\n"]


@run
async def test_an_updated_game_client_is_explained_before_the_client_is_touched(tmp_path):
    launcher, host, session, _ = await launcher_with_client(tmp_path, None)
    host.support = {"supported": False, "message": "Klient DBVictory został zaktualizowany"}
    await launcher.action("start")
    assert launcher.view["phase"] == "clientError"
    assert launcher.view["clientOutdated"] is True
    assert "zaktualizowany" in launcher.view["text"]
    assert host.opens == 0
    assert session.writes == []
    host.support = None
    await launcher.action("start")
    assert session.writes == ["prepare-login\n"]


@run
async def test_closing_the_launcher_while_the_game_is_opening_cannot_advance_startup(tmp_path):
    launcher, host, session, _ = await launcher_with_client(tmp_path, None)
    closes = []
    host.on_open = lambda: closes.append(launcher.on_close())
    await launcher.action("start")
    assert closes == [False]
    assert session.ended
    assert session.writes == []


@run
async def test_a_finished_session_after_running_quits_the_launcher(tmp_path):
    launcher, host, session, window = await launcher_with_client(tmp_path, 17)
    await launcher.action("start")
    host.on_line("OCZEKIWANIE NA POŁĄCZENIE")
    await launcher.action("connect")
    host.on_line("GOTOWE")
    launcher.marker.write_text("")
    await launcher.poll_now()
    session.closed.set_result(0)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert window.quit_called
    assert launcher.marker is None


class Frame:
    def __init__(self, url):
        self.url = url

    def current_url_is(self, page):
        return desktop_app.is_page_url(self.url, page)


def test_the_api_refuses_other_pages_and_writes_in_check_mode(tmp_path):
    page = tmp_path / "index.html"

    class Owner:
        window, read_only = Frame("http://evil.example/"), True
    api = desktop_app.LauncherApi(Owner, page)
    with pytest.raises(PermissionError):
        api.act("start")
    Owner.window = Frame(page.as_uri())
    with pytest.raises(PermissionError):
        api.mcp("connect", "codex")


def test_download_progress_names_the_package_size_speed_and_remaining_time():
    report = {"stage": "download", "done": 12_300_000, "total": 33_000_000, "unit": "bytes", "kind": "patch",
              "started": 100.0}
    shown = desktop_app.describe_progress(report, now=106.0)
    assert shown["label"] == "Pobieranie łatki: 12,3 z 33,0 MB"
    assert shown["rate"] == "2,0 MB/s · ok. 10 s"
    assert (shown["step"], shown["percent"]) == (1, 37)
    full = desktop_app.describe_progress({**report, "kind": "full", "done": 33_000_000}, now=106.0)
    assert (full["label"], full["rate"]) == ("Pobieranie pełnego wydania: 33,0 z 33,0 MB", "5,5 MB/s")
    slow = desktop_app.describe_progress({**report, "done": 1_000_000}, now=106.0)
    assert slow["rate"] == "0,2 MB/s · ok. 3 min 12 s"


def test_extract_and_install_progress_count_files_and_bad_reports_are_ignored(tmp_path):
    shown = desktop_app.describe_progress({"stage": "apply", "done": 5, "total": 20, "started": 1}, now=2)
    assert shown == {"stage": "apply", "step": 3, "steps": 3, "percent": 25, "label": "Instalowanie: 5 z 20 plików",
                     "rate": ""}
    for bad in ({}, {"stage": "x", "done": 1, "total": 2}, {"stage": "apply", "done": 1, "total": 0},
                {"stage": "apply", "done": "?", "total": 2}):
        assert desktop_app.describe_progress(bad, now=0) is None
    path = tmp_path / "progress.json"
    assert desktop_app.read_progress(path) is None
    path.write_text('{"stage": "extract", "do', encoding="utf-8")  # caught mid-write
    assert desktop_app.read_progress(path) is None
    path.write_text(json.dumps({"stage": "extract", "done": 1, "total": 4, "started": 0}), encoding="utf-8")
    assert desktop_app.read_progress(path, now=1)["label"] == "Rozpakowywanie: 1 z 4 plików"


@run
async def test_installing_shows_the_installers_progress_until_it_finishes(tmp_path):
    launcher, host, session, window = await launcher_with_client(tmp_path, None)
    feed = tmp_path / ".dbvictory" / "updates" / "github"
    feed.mkdir(parents=True)
    watches = []
    launcher.every = lambda seconds, callback: watches.append(callback) or Handle()
    sha = "a" * 40

    async def check_release(root, args):
        if args[0] == "install":
            (feed / "progress.json").write_text(json.dumps(
                {"stage": "download", "done": 5, "total": 10, "kind": "full", "started": 0}), encoding="utf-8")
            await watches[-1]()
            assert launcher.view["progress"]["percent"] == 50
            assert launcher.view["text"] == "Aktualizacja do v9.9.9"
            return {"state": "current", "message": "", "installed": None}
        return {"state": "available", "source": "github", "pending": sha, "pending_label": "v9.9.9", "message": ""}
    host.check_release = check_release
    host.valid_sha = lambda value: True
    await launcher.check(install=sha)
    assert launcher.view["progress"] is None


def test_quitting_never_waits_for_the_window_it_closes():
    import threading
    released, destroyed = threading.Event(), threading.Event()

    class Blocking:
        def destroy(self):  # like WinForms: waits for the UI thread, which waits for the closing handler
            released.wait(5)
            destroyed.set()
    frame = desktop_app.WebviewWindow()
    frame.window = Blocking()
    frame.quit()  # returns at once, so the loop can answer the closing handler
    assert not destroyed.is_set()
    released.set()
    assert destroyed.wait(5)


def test_the_launcher_and_panel_windows_use_the_bots_icon():
    import struct
    from dbv.mcp_server import panel_window
    assert desktop_app.use_app_icon(desktop_app.APP_ID) == str(desktop_app.ICON)
    assert Path(panel_window.use_app_icon()) == desktop_app.ICON
    data = desktop_app.ICON.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    sizes = {data[6 + 16 * i] or 256 for i in range(count)}
    assert (reserved, kind) == (0, 1) and {16, 32, 48, 256} <= sizes


def test_tidy_hides_only_the_releases_own_internals(tmp_path):
    files = {"DBVictory.exe": ["x", 1], "DBVictory Launcher.exe": ["x", 1], "README.md": ["x", 1],
             "run_bot.py": ["x", 1], "dbv/paths.py": ["x", 1], "venv/Scripts/python.exe": ["x", 1]}
    (tmp_path / "release-manifest.json").write_text(json.dumps({"format": 1, "files": files}), encoding="utf-8")
    for name in ("DBVictory.exe", "DBVictory Launcher.exe", "README.md", "run_bot.py", "dbv", "venv", "local_data",
                 "my notes.txt"):
        (tmp_path / name).mkdir() if "." not in name else (tmp_path / name).write_text("")
    hidden = []
    assert desktop_app.tidy(tmp_path, hide=hidden.append) == [
        "DBVictory Launcher.exe", "dbv", "release-manifest.json", "run_bot.py", "venv"]
    assert {p.name for p in hidden} == {"DBVictory Launcher.exe", "dbv", "release-manifest.json", "run_bot.py", "venv"}
    (tmp_path / "release-manifest.json").unlink()
    assert desktop_app.tidy(tmp_path, hide=hidden.append) == []  # a source checkout is left alone


def test_unblock_clears_the_download_mark_from_the_releases_own_files(tmp_path):
    files = {"DBVictory.exe": ["x", 1], "run_bot.py": ["x", 1], "venv/Scripts/Lib/site-packages/pythonnet/runtime/Python.Runtime.dll": ["x", 1]}
    (tmp_path / "release-manifest.json").write_text(json.dumps({"format": 1, "files": files}), encoding="utf-8")
    removed = []

    def remove(path):
        if path.startswith(str(tmp_path / "DBVictory.exe")):
            raise FileNotFoundError(path)  # not marked
        removed.append(path)

    dll = tmp_path / "venv" / "Scripts" / "Lib" / "site-packages" / "pythonnet" / "runtime" / "Python.Runtime.dll"
    assert desktop_app.unblock(tmp_path, remove=remove) == [dll]
    assert removed == [f"{dll}:Zone.Identifier"]
    (tmp_path / "release-manifest.json").unlink()
    assert desktop_app.unblock(tmp_path, remove=remove) == []  # a source checkout is left alone


@pytest.mark.skipif(os.name != "nt", reason="alternate data streams are an NTFS feature")
def test_unblock_removes_a_real_zone_identifier_stream(tmp_path):
    (tmp_path / "release-manifest.json").write_text(json.dumps({"format": 1, "files": {"a.dll": ["x", 1]}}),
                                                    encoding="utf-8")
    dll = tmp_path / "a.dll"
    dll.write_bytes(b"MZ")
    try:
        Path(f"{dll}:Zone.Identifier").write_text("[ZoneTransfer]\nZoneId=3\n", encoding="ascii")
    except OSError:
        pytest.skip("this drive keeps no alternate data streams")
    assert desktop_app.unblock(tmp_path) == [dll]
    assert not os.path.exists(f"{dll}:Zone.Identifier")
    assert dll.read_bytes() == b"MZ"


def test_the_bot_runs_under_its_own_executables_when_the_release_ships_them(tmp_path):
    scripts = tmp_path / "venv" / "Scripts"
    scripts.mkdir(parents=True)
    assert desktop_host.python_exe(tmp_path) == scripts / "python.exe"
    (scripts / "DBVictory Engine.exe").write_text("")
    assert desktop_host.python_exe(tmp_path) == scripts / "DBVictory Engine.exe"


def test_a_test_build_still_gets_its_release_notes(tmp_path):
    directory = tmp_path / "launcher" / "desktop"
    directory.mkdir(parents=True)
    notes = [{"version": v, "changes": [{"title": "Zmiana", "detail": "Opis"}]} for v in ("v1.10.0", "v1.9.0")]
    (directory / "release-notes.json").write_text(json.dumps(notes), encoding="utf-8")
    assert desktop_host.history(tmp_path, "v1.9.0-test.abcdef0 (abcdef0)") == notes[1:]


class ChannelHost(FakeHost):
    def __init__(self):
        super().__init__(17)
        self.channel, self.checks = "stable", 0

    async def check_release(self, root, args):
        self.checks += 1
        return {"state": "current", "message": "", "installed": None, "source": "github", "channel": self.channel}

    async def set_channel(self, root, channel):
        self.channel = channel
        return {"channel": channel}


@run
async def test_switching_to_test_builds_saves_the_channel_and_checks_again(tmp_path):
    host, window = ChannelHost(), FakeWindow()
    launcher = desktop_app.Launcher(tmp_path, host, window, settings=tmp_path / "client.json",
                                    loop=asyncio.get_running_loop())
    launcher.call_later = lambda seconds, callback: Handle()
    launcher.prepare_prompt = lambda: asyncio.sleep(0)
    await launcher.action("retry")
    assert launcher.view["channel"] == "stable"
    checks = host.checks
    launcher.view = {**launcher.view, "phase": "ready"}
    assert await launcher.set_channel("test") == {"channel": "test"}
    assert host.channel == "test" and host.checks > checks and launcher.view["channel"] == "test"


@run
async def test_a_git_checkout_has_no_update_channel(tmp_path):
    host, window = FakeHost(17), FakeWindow()
    launcher = desktop_app.Launcher(tmp_path, host, window, settings=tmp_path / "client.json",
                                    loop=asyncio.get_running_loop())
    launcher.prepare_prompt = lambda: asyncio.sleep(0)
    await launcher.action("retry")
    assert launcher.view["channel"] is None
    with pytest.raises(PermissionError):
        await launcher.set_channel("test")
