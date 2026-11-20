"""One Windows taskbar pulse and its bundled sound. Runs only for an alert."""
from pathlib import Path
import sys


def flash_game_taskbar():
    import win32con
    import win32gui
    import win32process
    import win32ts

    game_pids = {pid for _, pid, name, _ in win32ts.WTSEnumerateProcesses()
                 if name and name.casefold() in {"dbvstart.exe", "dbvstart_gl.exe"}}
    if not game_pids:
        return
    windows = []

    def collect(hwnd, _):
        if (win32process.GetWindowThreadProcessId(hwnd)[1] in game_pids
                and win32gui.IsWindowVisible(hwnd) and not win32gui.GetWindow(hwnd, win32con.GW_OWNER)):
            windows.append(hwnd)
        return True

    win32gui.EnumWindows(collect, None)
    if len(windows) == 1:
        win32gui.FlashWindowEx(windows[0], win32con.FLASHW_TRAY, 2, 180)


def play(kind):
    import winsound

    try:
        flash_game_taskbar()
    except Exception:
        # An unavailable window or Windows desktop must not suppress the sound.
        pass
    sound = Path(__file__).with_name("sounds") / {"message": "message.wav", "player": "player.wav"}[kind]
    winsound.PlaySound(str(sound), winsound.SND_FILENAME | winsound.SND_NODEFAULT)


if __name__ == "__main__":
    play(sys.argv[1])
