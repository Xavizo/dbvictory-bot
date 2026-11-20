import sys
from unittest.mock import Mock

import pytest

from dashboard.electron import windows_alert


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows taskbar')
def test_only_one_visible_game_window_gets_a_bounded_taskbar_flash(monkeypatch):
    import win32con
    import win32gui
    import win32process
    import win32ts

    pids = {10: 301, 20: 302, 30: 301, 40: 303, 50: 301}
    windows = [10, 20, 30, 50]
    monkeypatch.setattr(win32ts, 'WTSEnumerateProcesses', lambda: [
        (1, 301, 'dbvStart_gl.exe', None), (1, 302, 'electron.exe', None),
        (1, 303, 'DBVStart.exe', None)])
    monkeypatch.setattr(win32gui, 'EnumWindows', lambda visit, arg: [visit(h, arg) for h in windows])
    monkeypatch.setattr(win32process, 'GetWindowThreadProcessId', lambda h: (1, pids[h]))
    monkeypatch.setattr(win32gui, 'IsWindowVisible', lambda h: h != 30)
    monkeypatch.setattr(win32gui, 'GetWindow', lambda h, flag: 10 if h == 50 else 0)
    flash = Mock()
    monkeypatch.setattr(win32gui, 'FlashWindowEx', flash)
    windows_alert.flash_game_taskbar()
    flash.assert_called_once_with(10, win32con.FLASHW_TRAY, 2, 180)
    windows.append(40)
    windows_alert.flash_game_taskbar()
    assert flash.call_count == 1


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows WAV playback')
def test_missing_window_does_not_mute_messages(monkeypatch):
    import winsound

    monkeypatch.setattr(windows_alert, 'flash_game_taskbar', Mock(side_effect=OSError()))
    play = Mock()
    monkeypatch.setattr(winsound, 'PlaySound', play)
    windows_alert.play('message')
    assert play.call_args.args[0].endswith('message.wav')
