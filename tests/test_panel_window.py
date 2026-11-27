"""The panel's desktop window (dbv/mcp_server/panel_window.py) with a fake pywebview.
Ported from the Electron security and tray tests."""
import json

import pytest

from dbv.mcp_server import panel_window
from dbv.mcp_server.panel_window import PanelApi, PanelApp, is_panel_url, valid_cell, valid_plan

TOKEN = "t" * 43


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self):
        return [handler() for handler in self.handlers]


class FakeWindow:
    def __init__(self, title, url=None, html=None, js_api=None, **options):
        self.title, self.url, self.api = title, url or ("data:text/html," if html else ""), js_api
        self.visible, self.destroyed, self.loads = True, False, []
        self.events = type("Events", (), {})()
        for name in ("closing", "closed", "loaded"):
            setattr(self.events, name, Event())

    def get_current_url(self):
        return self.url

    def load_url(self, url):
        self.loads.append(url)
        self.url = url

    def hide(self):
        self.visible = False

    def show(self):
        self.visible = True

    def restore(self):
        pass

    def destroy(self):
        self.destroyed = True


class FakeWebview:
    def __init__(self):
        self.windows = []

    def create_window(self, title, url=None, **options):
        window = FakeWindow(title, url, **options)
        self.windows.append(window)
        return window


class FakeTray:
    def __init__(self):
        self.closed, self.errors = False, []

    def close(self):
        self.closed = True

    def error(self, title, text):
        self.errors.append(title)


def panel(env=None, **kwargs):
    env = {"DBV_DASHBOARD_TOKEN": TOKEN, "DBV_ENGINE_LABEL": "v2.1.0 (abcdef0)", **(env or {})}
    webview = FakeWebview()
    copied = []
    app = PanelApp(env, webview, clipboard=copied.append, **kwargs)
    main = app.create_main()
    main.url = "http://127.0.0.1:4747/"
    return app, main, copied, env


def test_the_token_leaves_the_environment_and_the_title_names_the_engine():
    app, main, _, env = panel()
    assert "DBV_DASHBOARD_TOKEN" not in env
    assert main.title == "DBVictory Bot — v2.1.0 (abcdef0)"


@pytest.mark.parametrize("url,trusted", [
    ("http://127.0.0.1:4747", True), ("http://127.0.0.1:4747/route-editor?draft=", True),
    ("http://127.0.0.1:47470/", False), ("http://127.0.0.1:4747.evil.example/", False),
    ("https://127.0.0.1:4747/", False), ("http://localhost:4747/", False), ("file:///C:/x.html", False),
    ("data:text/html,x", False), (None, False)])
def test_only_the_panel_origin_is_trusted(url, trusted):
    assert is_panel_url(url) is trusted


def test_only_the_trusted_main_frame_receives_the_dashboard_credential():
    app, main, _, _ = panel()
    assert main.api.get_token() == TOKEN
    for url in ("https://evil.example/", "data:text/html,", "http://127.0.0.1:4748/"):
        main.url = url
        with pytest.raises(PermissionError):
            main.api.get_token()
    tokenless, window, _, _ = panel({"DBV_DASHBOARD_TOKEN": ""})
    with pytest.raises(PermissionError):
        window.api.get_token()


def test_copy_accepts_bounded_tile_text_only_from_the_trusted_frame():
    app, main, copied, _ = panel()
    assert main.api.copy_cell("Pole (100, 200, 7) — trawa") is True
    for text in ("rm -rf /", "Pole (1, 2, 7) — " + "x" * 20000, "Pole (1, 2, 7) — \0", 7):
        with pytest.raises(ValueError):
            main.api.copy_cell(text)
    main.url = "https://evil.example/"
    with pytest.raises(PermissionError):
        main.api.copy_cell("Pole (1, 2, 7) — x")
    assert copied == ["Pole (100, 200, 7) — trawa"]


def test_route_copying_rejects_executable_plans_and_oversized_data():
    plan = {"preview_only": True, "scope": "hunt", "path": [[1, 2, 7]], "points": []}
    assert valid_plan(json.dumps(plan))
    assert not valid_plan(json.dumps({**plan, "preview_only": False}))
    assert not valid_plan(json.dumps({**plan, "points": [{}] * 129}))
    assert not valid_plan(json.dumps({**plan, "path": [[0, 0, 7]] * 40001}))
    assert not valid_plan("x" * (panel_window.MAX_PLAN + 1))
    assert not valid_plan("not json")
    assert valid_cell("Pole (1, 2, 7) — ") and not valid_cell("pole (1, 2, 7) — ")
    app, main, copied, _ = panel()
    with pytest.raises(ValueError):
        main.api.copy_plan(json.dumps({**plan, "preview_only": False}))
    assert main.api.copy_plan(json.dumps(plan)) is True
    assert len(copied) == 1


def test_the_editor_opens_only_a_fixed_local_route_and_only_from_the_main_window():
    app, main, _, _ = panel()
    with pytest.raises(ValueError):
        main.api.open_editor("../../etc")
    assert main.api.open_editor("a" * 32) is True
    editor = app.windows["editor"]
    assert editor.url == "http://127.0.0.1:4747/route-editor?draft=" + "a" * 32
    with pytest.raises(PermissionError):
        editor.api.open_editor("")
    assert main.api.open_editor("") is False  # a second request focuses the open editor
    editor.events.closed.fire()
    assert "editor" not in app.windows
    with pytest.raises(PermissionError):
        editor.api.get_token()  # a closed editor keeps no access


def test_navigation_away_from_the_panel_is_reverted():
    app, main, _, _ = panel()
    main.url = "https://evil.example/"
    main.events.loaded.fire()
    assert main.loads == ["http://127.0.0.1:4747"]
    main.api.open_editor("")
    editor = app.windows["editor"]
    editor.url = "https://evil.example/"
    editor.events.loaded.fire()
    assert editor.loads == ["http://127.0.0.1:4747/route-editor"]


def test_x_hides_panel_and_editor_and_restoring_brings_back_only_an_open_editor():
    app, main, _, _ = panel()
    main.api.open_editor("")
    editor = app.windows["editor"]
    assert main.events.closing.fire() == [False]
    assert not main.visible and not editor.visible
    app.show()
    assert main.visible and editor.visible
    main.events.closing.fire()
    editor.events.closed.fire()
    app.show()
    assert main.visible and not editor.visible


def test_only_an_explicit_tray_exit_closes_the_windows_and_releases_the_tray():
    tray = FakeTray()
    app, main, _, _ = panel(tray_factory=lambda app: tray)
    app.tray = tray
    app.quit()
    assert main.destroyed and tray.closed
    assert main.events.closing.fire() == [True]


def test_a_hidden_panel_can_end_its_session_while_an_unbound_panel_cannot(tmp_path):
    exit_file = tmp_path / "exit"
    app, main, _, env = panel({panel_window.EXIT_FILE_ENV: str(exit_file)})
    assert panel_window.EXIT_FILE_ENV not in env
    main.events.closing.fire()
    assert app.exit_bot() is True
    assert exit_file.exists() and main.destroyed
    unbound, window, _, _ = panel()
    assert unbound.exit_bot() is False and not window.destroyed


def test_a_failed_exit_keeps_the_panel_available(tmp_path):
    tray = FakeTray()
    app, main, _, _ = panel({panel_window.EXIT_FILE_ENV: str(tmp_path / "missing" / "exit")})
    app.tray = tray
    assert app.exit_bot() is False
    assert tray.errors == ["Zakończ bota"] and not main.destroyed and not app.quitting


def test_the_launcher_hears_once_that_the_panel_is_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(panel_window.paths, "INSTALL_ROOT", tmp_path)
    (tmp_path / ".codex" / "launcher-events").mkdir(parents=True)
    launcher_id = "f" * 32
    app, main, _, _ = panel({"DBV_LAUNCHER_ID": launcher_id, panel_window.EXIT_FILE_ENV: str(tmp_path / "exit")})
    main.url = "data:text/html,"
    main.events.loaded.fire()
    marker = tmp_path / ".codex" / "launcher-events" / f"{launcher_id}.ready"
    assert not marker.exists()  # the loading screen is not the panel
    main.url = "http://127.0.0.1:4747/"
    main.events.loaded.fire()
    assert marker.exists()
    marker.unlink()
    main.events.loaded.fire()
    assert not marker.exists()


def test_sound_operations_are_bounded_and_need_the_trusted_frame():
    app, main, _, _ = panel()
    assert main.api.test_sound() is False  # no poller yet
    assert main.api.panel_pause() == {"paused": True, "session": None, "through": 0}

    class Sounds:
        def test_sound(self):
            return True

        def panel_pause(self, value=None):
            return {"paused": value}
    app.sounds = Sounds()
    assert main.api.test_sound() is True
    assert main.api.panel_pause(False) == {"paused": False}
    main.url = "https://evil.example/"
    with pytest.raises(PermissionError):
        main.api.panel_pause(False)


def route_pack_text():
    return json.dumps({"format": "dbvictory-route", "version": 1, "server": "x:1", "drafts": [], "tiles": []})


def test_a_route_pack_is_saved_only_where_the_player_chose(tmp_path):
    import gzip
    app, main, _, _ = panel()
    target = tmp_path / "Garlic"
    dialogs = []
    app.file_dialog = lambda kind, mode, name="": dialogs.append((kind, mode, name)) or (str(target),)
    assert main.api.save_route("Garlic castle.dbvroute", route_pack_text()) == "Garlic.dbvroute"
    assert dialogs == [("main", "save", "Garlic castle.dbvroute")]
    assert json.loads(gzip.decompress((tmp_path / "Garlic.dbvroute").read_bytes()))["format"] == "dbvictory-route"
    app.file_dialog = lambda *args: None
    assert main.api.save_route("Garlic castle.dbvroute", route_pack_text()) is False
    for name, text in (("../x.dbvroute", route_pack_text()), ("x.exe", route_pack_text()), ("x.dbvroute", "{}")):
        with pytest.raises(ValueError):
            main.api.save_route(name, text)
    main.url = "https://evil.example/"
    with pytest.raises(PermissionError):
        main.api.save_route("x.dbvroute", route_pack_text())


def test_opening_a_route_pack_returns_only_a_valid_pack(tmp_path):
    import gzip
    app, main, _, _ = panel()
    good, plain, other = tmp_path / "a.dbvroute", tmp_path / "b.dbvroute", tmp_path / "c.dbvroute"
    good.write_bytes(gzip.compress(route_pack_text().encode()))
    plain.write_text(route_pack_text(), encoding="utf-8")
    other.write_text('{"token": "secret"}', encoding="utf-8")
    for path in (good, plain):
        app.file_dialog = lambda *args, path=path: [str(path)]
        assert json.loads(main.api.open_route())["version"] == 1
    app.file_dialog = lambda *args: [str(other)]
    with pytest.raises(ValueError):
        main.api.open_route()
    app.file_dialog = lambda *args: None
    assert main.api.open_route() is None
