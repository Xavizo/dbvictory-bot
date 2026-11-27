import sys
from unittest.mock import Mock

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows pause badge")


def test_badge_tracks_only_the_foreground_game_and_hides_on_resume(monkeypatch):
    from dbv.mcp_server.game_pause_badge import GamePauseBadge, gui, con, win32process, win32ts

    badge = GamePauseBadge()
    try:
        flags = gui.GetWindowLong(badge.hwnd, con.GWL_EXSTYLE)
        for required in (con.WS_EX_NOACTIVATE, con.WS_EX_LAYERED, con.WS_EX_TRANSPARENT, con.WS_EX_TOOLWINDOW):
            assert flags & required
        assert not gui.IsWindowVisible(badge.hwnd)
        assert badge.window_proc(badge.hwnd, con.WM_NCHITTEST, 0, 0) == con.HTTRANSPARENT
        foreground = [11]
        viewport = [(-1200, 40), (0, 0, 1000, 700)]
        place, hide = Mock(), Mock()
        monkeypatch.setattr(gui, "GetForegroundWindow", lambda: foreground[0])
        monkeypatch.setattr(gui, "IsIconic", lambda _: False)
        monkeypatch.setattr(win32process, "GetWindowThreadProcessId", lambda hwnd: (1, hwnd))
        monkeypatch.setattr(win32ts, "WTSEnumerateProcesses", lambda: [(1, 11, "dbvStart_gl.exe", None), (1, 22, "editor.exe", None)])
        monkeypatch.setattr(gui, "ClientToScreen", lambda *_: viewport[0])
        monkeypatch.setattr(gui, "GetClientRect", lambda _: viewport[1])
        monkeypatch.setattr(badge, "dpi_for_window", lambda _: 144)
        monkeypatch.setattr(gui, "SetWindowPos", place)
        monkeypatch.setattr(gui, "ShowWindow", hide)
        badge.update(False)
        place.assert_not_called()
        badge.update(True)
        place.assert_called_once_with(badge.hwnd, con.HWND_TOPMOST, -826, 55, 252, 54,
                                      con.SWP_NOACTIVATE | con.SWP_SHOWWINDOW)
        badge.update(True)
        assert place.call_count == 1
        viewport[0] = (10, 30)
        badge.update(True)
        assert place.call_count == 2
        foreground[0] = 22
        badge.update(True)
        hide.assert_called_once_with(badge.hwnd, con.SW_HIDE)
        foreground[0] = 11
        badge.update(True)
        assert place.call_count == 3
        badge.update(False)
        badge.update(False)
        assert hide.call_count == 2
    finally:
        badge.close()
