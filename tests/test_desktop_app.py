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
