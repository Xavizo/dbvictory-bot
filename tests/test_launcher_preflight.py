"""Exercise the actual desktop entry in a disposable directory without a game."""
from pathlib import Path
import subprocess
import sys

import pytest

import launcher_files


@pytest.mark.parametrize("close_result", [0, 2, 1])
def test_desktop_waits_for_update_and_loads_only_fresh_console(tmp_path, close_result):
    entry = tmp_path / "run_bot.py"
    entry.write_bytes((launcher_files.ROOT / entry.name).read_bytes())
    # No engine, tray or game integration exists in this directory. Simulate
    # replacing the console's code while the normal desktop entry is open.
    (tmp_path / "launcher_files.py").write_text(
        "from contextlib import contextmanager\n"
        "@contextmanager\n"
        "def desktop_files(root):\n"
        "    (root / 'run_bot.py').write_text(\"def main(*, session_lock_held): assert session_lock_held; print('new release'); return 0\\n\")\n"
        f"    if {close_result} == 1: raise RuntimeError('window failed')\n"
        f"    yield {close_result} == 0\n", encoding="utf-8")
    result = subprocess.run([sys.executable, "-B", str(entry)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == (1 if close_result == 1 else 0), result.stderr
    assert result.stdout.strip() == ("new release" if close_result == 0 else "")


def test_check_only_does_not_apply(monkeypatch):
    import dbv_release

    monkeypatch.setattr(dbv_release, "status", lambda root: {"state": "available", "pending": "a" * 40})
    monkeypatch.setattr(dbv_release, "apply_pending", lambda root: pytest.fail("read-only"))
    assert launcher_files.preflight(Path("."), check_only=True)["state"] == "available"


def test_file_handoff_rechecks_queue_and_holds_lock(tmp_path, monkeypatch):
    from launcher_lock import launcher_lock

    feed = tmp_path / ".dbvictory/updates"
    feed.mkdir(parents=True)
    pending = feed / "pending.json"
    calls = []

    def close_window(root):
        calls.append(root)
        if len(calls) == 1:
            pending.touch()
        else:
            pending.unlink()
        return 0

    if sys.platform != "win32":
        pytest.skip("Windows desktop handoff")
    monkeypatch.setattr(launcher_files, "show_window", close_window)
    lock = tmp_path / ".dbvictory/standalone.lock"
    with launcher_files.desktop_files(tmp_path) as ready:
        assert ready and len(calls) == 2
        with pytest.raises(RuntimeError), launcher_lock(lock):
            pass
    with launcher_lock(lock):
        pass
